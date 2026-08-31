"""Authentication core for the standalone management WebUI.

The account registry is stored in the portable environment configuration, so
accounts remain valid across ordinary image upgrades and CLI configuration.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Optional


_HASH_SCHEME = "pbkdf2_sha256"
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
_ACCOUNTS_ENV_KEY = "WEBUI_ACCOUNTS"
_MAX_MANAGED_ACCOUNTS = 20
_PBKDF2_ITERATIONS = 600_000
_ATTEMPT_WINDOW_SECONDS = 15 * 60
_attempt_lock = threading.Lock()
_attempt_state: dict[str, tuple[int, float, float]] = {}


@dataclass(frozen=True)
class Account:
    username: str
    password_hash: str
    is_owner: bool = False


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    username: str
    password_hash: str
    session_timeout_minutes: int
    accounts: tuple[Account, ...] = ()


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


def _urlsafe_b64decode(value: str) -> Optional[bytes]:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError, binascii.Error):
        return None


def _password_hash_components(password_hash: object) -> Optional[tuple[int, bytes, bytes]]:
    """Parse and cheaply validate the persisted PBKDF2 record structure.

    Loading the account registry is on the request path for every protected
    WebUI endpoint.  It must validate stored records without deriving a test
    password hash: PBKDF2 is intentionally expensive and using it merely to
    inspect an already persisted record added roughly half a second to each
    request on the deployment host.
    """
    try:
        scheme, raw_iterations, encoded_salt, encoded_digest = str(password_hash).split(":", 3)
        iterations = int(raw_iterations)
        if scheme != _HASH_SCHEME or not 100_000 <= iterations <= 1_000_000:
            return None
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected_digest = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        if len(salt) < 16 or len(expected_digest) != 32:
            return None
    except (AttributeError, ValueError, UnicodeEncodeError, binascii.Error):
        return None
    return iterations, salt, expected_digest


def verify_password_hash(password_hash: str, password: str) -> Optional[bool]:
    """Verify a PBKDF2 record stored by the account manager."""
    components = _password_hash_components(password_hash)
    if components is None:
        return None
    iterations, salt, expected_digest = components
    actual_digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def _read_accounts(raw_value: object) -> tuple[Account, ...]:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return ()
    encoded = raw_value.strip()
    if len(encoded) > 32_768:
        return ()
    decoded = _urlsafe_b64decode(encoded)
    if decoded is None:
        return ()
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return ()
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        return ()

    accounts: list[Account] = []
    usernames: set[str] = set()
    owner_seen = False
    for item in raw_accounts[:_MAX_MANAGED_ACCOUNTS]:
        if not isinstance(item, dict):
            return ()
        username = str(item.get("u") or "").strip()
        password_hash = str(item.get("p") or "").strip()
        if (
            not _USERNAME_PATTERN.fullmatch(username)
            or username in usernames
            or _password_hash_components(password_hash) is None
        ):
            return ()
        is_owner = bool(item.get("o")) and not owner_seen
        owner_seen = owner_seen or is_owner
        accounts.append(Account(username, password_hash, is_owner=is_owner))
        usernames.add(username)
    if not accounts:
        return ()
    if not owner_seen:
        first = accounts[0]
        accounts[0] = Account(first.username, first.password_hash, is_owner=True)
    return tuple(accounts)


def read_auth_config(env_values: Mapping[str, object]) -> AuthConfig:
    accounts = _read_accounts(env_values.get(_ACCOUNTS_ENV_KEY))
    if accounts:
        owner = next((account for account in accounts if account.is_owner), accounts[0])
        username, password_hash = owner.username, owner.password_hash
    else:
        username = str(env_values.get("WEBUI_ADMIN_USERNAME", "")).strip()
        password_hash = str(env_values.get("WEBUI_ADMIN_PASSWORD_HASH", "")).strip()
        # Keep the legacy single-account fallback behavior unchanged: an old
        # malformed value remains a configured account whose login check
        # fails safely, rather than unexpectedly exposing first-run setup.
        if _USERNAME_PATTERN.fullmatch(username) and password_hash:
            accounts = (Account(username, password_hash, is_owner=True),)
    return AuthConfig(
        enabled=_as_bool(env_values.get("WEBUI_AUTH_ENABLED"), True),
        username=username,
        password_hash=password_hash,
        session_timeout_minutes=_bounded_int(
            env_values.get("WEBUI_SESSION_TIMEOUT_MINUTES"),
            10_080,
            minimum=5,
            maximum=10_080,
        ),
        accounts=accounts,
    )


def _configured(config: AuthConfig) -> bool:
    return bool(config.accounts)


def validate_username(username: object) -> str | None:
    """Return a localized-safe validation message for a managed username."""
    if not _USERNAME_PATTERN.fullmatch(str(username or "").strip()):
        return "用户名须为 3–64 位字母、数字、`.`、`_` 或 `-`，且以字母或数字开头。"
    return None


def validate_password(password: object) -> str | None:
    """Apply the documented six-character minimum password policy."""
    if len(str(password or "")) < 6:
        return "密码至少需要 6 个字符。"
    return None


def hash_password(password: str) -> str:
    """Create a versioned PBKDF2 record for the account manager."""
    if message := validate_password(password):
        raise ValueError(message)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return ":".join(
        (
            _HASH_SCHEME,
            str(_PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def serialize_accounts(accounts: tuple[Account, ...] | list[Account]) -> str:
    """Encode a validated account registry into one dotenv-safe value."""
    values = tuple(accounts)
    if not values or len(values) > _MAX_MANAGED_ACCOUNTS:
        raise ValueError("账户列表无效。")
    owners = [account for account in values if account.is_owner]
    if len(owners) != 1:
        raise ValueError("账户列表必须包含一个所有者账户。")
    seen: set[str] = set()
    for account in values:
        if (
            validate_username(account.username)
            or account.username in seen
            or _password_hash_components(account.password_hash) is None
        ):
            raise ValueError("账户列表无效。")
        seen.add(account.username)
    payload = {
        "v": 1,
        "accounts": [
            {"u": account.username, "p": account.password_hash, "o": account.is_owner}
            for account in values
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def account_is_owner(config: AuthConfig, username: object) -> bool:
    """Whether the authenticated account may manage the account registry."""
    account = find_account(config, username)
    return bool(account and account.is_owner)


def find_account(config: AuthConfig, username: object) -> Optional[Account]:
    candidate = str(username or "").strip()
    for account in config.accounts:
        if hmac.compare_digest(account.username, candidate):
            return account
    return None


def account_session_marker(account: Account) -> str:
    """Bind an ASGI session to the exact account password record."""
    return hashlib.sha256(
        f"adr-modern-account:v1:{account.password_hash}".encode("utf-8")
    ).hexdigest()


def session_secret(config: AuthConfig) -> str:
    """Provide a stable cookie signer while per-account markers revoke sessions."""
    material = config.password_hash or "unconfigured"
    return hashlib.sha256(f"adr-modern-ui:v2:{material}".encode("utf-8")).hexdigest()


def _retry_delay_seconds(failures: int) -> int:
    if failures < 5:
        return 0
    return min(60, 2 ** min(6, failures - 5))


def _remaining_retry_seconds(username: str) -> int:
    now = time.time()
    with _attempt_lock:
        state = _attempt_state.get(username)
        if state is None:
            return 0
        _failures, last_failure, retry_after = state
        if now - last_failure > _ATTEMPT_WINDOW_SECONDS:
            _attempt_state.pop(username, None)
            return 0
        return max(0, int(retry_after - now))


def _record_failed_attempt(username: str) -> None:
    now = time.time()
    with _attempt_lock:
        failures, last_failure, _retry_after = _attempt_state.get(
            username, (0, 0.0, 0.0)
        )
        if now - last_failure > _ATTEMPT_WINDOW_SECONDS:
            failures = 0
        failures += 1
        delay = _retry_delay_seconds(failures)
        _attempt_state[username] = (failures, now, now + delay if delay else 0.0)


def _clear_attempts(username: str) -> None:
    with _attempt_lock:
        _attempt_state.pop(username, None)
