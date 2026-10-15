"""Protected local state for approved, user-authorized subscription providers.

This module has no worker dependencies and is safe to import in the WebUI.
Never return access, refresh or ID tokens from public_status().
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "runtime" / "subscriptions"
SUPPORTED_PROVIDERS = {
    "openai_siwc": {
        "label": "ChatGPT (OpenAI SIWC)",
        "implemented": True,
        "note": "Official open-source app authorization; local/self-hosted only.",
    },
    "github_copilot": {
        "label": "GitHub Copilot SDK",
        "implemented": False,
        "note": "Official SDK candidate; integration and deployment tests pending.",
    },
    "google_gemini": {
        "label": "Google Gemini subscription",
        "implemented": False,
        "note": "No approved third-party CLI-token reuse; use API key mode.",
    },
    "anthropic_claude": {
        "label": "Anthropic Claude subscription",
        "implemented": False,
        "note": "No approved third-party Claude Code-token reuse; use API key mode.",
    },
}


class SubscriptionError(RuntimeError):
    """Safe-to-display subscription problem, never including credentials."""

    retryable = False


class SubscriptionTransientError(SubscriptionError):
    retryable = True


class SubscriptionLimitError(SubscriptionError):
    retryable = False


def _root() -> Path:
    configured = os.environ.get("ADR_SUBSCRIPTION_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_ROOT


def _file(name: str) -> Path:
    return _root() / name


def _ensure_root() -> None:
    root = _root()
    if root.is_symlink():
        raise SubscriptionError("Subscription directory must not be a symlink.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        # A pre-existing directory must not be accessible to other users.
        if root.stat().st_mode & 0o077:
            try:
                root.chmod(0o700)
            except OSError as exc:
                raise SubscriptionError("Subscription directory requires mode 0700.") from exc


def _read_json(name: str, fallback: dict) -> dict:
    path = _file(name)
    if _root().is_symlink():
        raise SubscriptionError("Subscription directory must not be a symlink.")
    if not path.exists():
        return fallback
    if path.is_symlink() or not path.is_file():
        raise SubscriptionError("Subscription credential path is not a regular file.")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise SubscriptionError("Subscription credential file must have mode 0600.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SubscriptionError("Subscription credential file cannot be read.") from exc
    if not isinstance(value, dict):
        raise SubscriptionError("Subscription credential format is invalid.")
    return value


def _write_json(name: str, value: dict) -> None:
    _ensure_root()
    path = _file(name)
    if path.is_symlink():
        raise SubscriptionError("Refusing to replace symlinked credential file.")
    fd, tmp_name = tempfile.mkstemp(prefix=".subscription-", dir=str(_root()))
    try:
        if os.name != "nt":
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@contextmanager
def locked_store():
    """Serialize rotating refresh tokens across worker/CLI processes.

    The lock file is never removed (unlinking a live lock defeats exclusion).
    """
    _ensure_root()
    path = _file("openai.lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            if not os.read(fd, 1):
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_accounts() -> dict:
    data = _read_json("openai.json", {"schema": 1, "active": None, "accounts": {}})
    if data.get("schema") != 1 or not isinstance(data.get("accounts"), dict):
        raise SubscriptionError("Unsupported subscription storage format.")
    return data


def write_accounts(data: dict) -> None:
    if data.get("schema") != 1 or not isinstance(data.get("accounts"), dict):
        raise SubscriptionError("Refusing invalid subscription storage.")
    _write_json("openai.json", data)


def host_id() -> str:
    """Persist the host identifier separately from transferable credentials."""
    with locked_store():
        value = _read_json("host.json", {})
        if value.get("host_id"):
            return str(value["host_id"])
        host = f"urn:uuid:{uuid.uuid4()}"
        _write_json("host.json", {"host_id": host})
        return host


def selected_account(data: dict | None = None) -> dict:
    data = read_accounts() if data is None else data
    active = data.get("active")
    item = data["accounts"].get(active)
    if not isinstance(item, dict) or not item.get("client_id") or not item.get("subject"):
        raise SubscriptionError("No authorized ChatGPT account. Run the SIWC login command.")
    return item


def public_status() -> dict:
    """Only non-secret status leaves the worker or becomes visible to WebUI."""
    result = {"providers": SUPPORTED_PROVIDERS, "active": None, "accounts": []}
    try:
        state = read_accounts()
    except SubscriptionError:
        result["storage_error"] = "Credential store is unavailable or insecure."
        return result
    for client_id, account in state["accounts"].items():
        if not isinstance(account, dict):
            continue
        result["accounts"].append({
            "client_id": client_id,
            "label": str(account.get("email") or "ChatGPT account")[:120],
            "connected": bool(account.get("refresh_token")),
            "authorized": "chatgpt.tokens.use.direct" in account.get("scopes", []),
            "active": client_id == state.get("active"),
        })
    result["active"] = next((x for x in result["accounts"] if x["active"]), None)
    return result
