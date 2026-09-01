"""Local container readiness checks for the Worker and WebUI.

External APIs are deliberately excluded.  A DNS, arXiv, LLM or WebDAV outage
belongs in task-level retry/status reporting and must not make Docker restart a
locally healthy scheduler in a loop.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class ContainerHealthError(RuntimeError):
    """A local invariant required by the running container is unavailable."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContainerHealthError(message)


def _check_writable_directory(path: Path) -> None:
    path = Path(path)
    _require(path.is_dir(), f"directory missing: {path}")
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".health-", dir=path)
        os.close(descriptor)
        Path(temporary_name).unlink()
    except OSError as exc:
        raise ContainerHealthError(f"directory is not writable: {path}: {exc}") from exc


def _check_sqlite(path: Path) -> None:
    """Run a bounded read-only quick check when a configured database exists."""
    path = Path(path)
    _check_writable_directory(path.parent)
    if not path.exists():
        return
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            row = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise ContainerHealthError(f"SQLite check failed: {path}: {exc}") from exc
    result = str(row[0] if row else "").strip().lower()
    _require(result == "ok", f"SQLite quick_check failed: {path}: {result or 'empty result'}")


def _process_named(name: str) -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text(encoding="utf-8").strip() == name:
                return True
        except OSError:
            continue
    return False


def _pid_alive(pid: Optional[int]) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _diagnostic_pid(path: Path) -> Optional[int]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    match = re.search(r"(?:^|\b)PID=(\d+)(?:\b|,)", text)
    return int(match.group(1)) if match else None


def _check_trigger_consumer(data_dir: Path, *, max_age_seconds: float = 45.0) -> None:
    queue = Path(data_dir) / "run" / "webui_triggers"
    status = queue / "status"
    _check_writable_directory(queue)
    _check_writable_directory(status)
    heartbeat = queue / ".watcher-heartbeat"
    try:
        age = max(0.0, time.time() - heartbeat.stat().st_mtime)
    except OSError as exc:
        raise ContainerHealthError("trigger watcher heartbeat is missing") from exc
    if age <= max_age_seconds:
        return

    # The watcher executes one FIFO request synchronously.  During that time
    # its heartbeat is expected to pause, but the claimed request and live
    # child PID prove the consumer is still doing useful work.
    running_requests = list(queue.glob("*.running"))
    pid = _diagnostic_pid(Path(data_dir) / "run" / "webui_triggered.pid")
    if running_requests and _pid_alive(pid):
        return
    raise ContainerHealthError(
        f"trigger watcher heartbeat is stale ({int(age)}s) and no live request is owned"
    )


def _check_lock_files(data_dir: Path) -> None:
    from utils.run_lock import is_lock_held

    run_dir = Path(data_dir) / "run"
    _check_writable_directory(run_dir)
    for lock_path in run_dir.glob("*.lock"):
        try:
            is_lock_held(lock_path)
        except OSError as exc:
            raise ContainerHealthError(f"run lock is inaccessible: {lock_path}: {exc}") from exc


def _drop_to_runtime_user(user_name: str = "adr") -> None:
    if os.geteuid() != 0:
        return
    try:
        account = pwd.getpwnam(user_name)
    except KeyError as exc:
        raise ContainerHealthError(f"runtime user missing: {user_name}") from exc
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.environ["HOME"] = account.pw_dir


def _check_worker_processes(runtime_user: str) -> None:
    if os.environ.get("MODE", "cron").strip().lower() == "manual":
        return
    _require(_process_named("cron"), "cron daemon is not running")
    try:
        account = pwd.getpwnam(runtime_user)
    except KeyError as exc:
        raise ContainerHealthError(f"runtime user missing: {runtime_user}") from exc
    crontab = Path("/var/spool/cron/crontabs") / account.pw_name
    _require(crontab.is_file() and crontab.stat().st_size > 0, "runtime crontab is missing")


def _worker_checks(runtime_user: str) -> None:
    # Process/crontab state needs root visibility.  All filesystem/database
    # checks run after dropping privileges so root cannot mask bad NAS modes.
    _check_worker_processes(runtime_user)
    _drop_to_runtime_user(runtime_user)

    from config import settings

    settings.normalized_score_strategy()
    for module in (
        "modes.daily_research",
        "modes.backfill_queue",
        "modes.history_data_repair",
        "modes.history_omission_scan",
        "modes.legacy_import",
        "modes.trend_research",
        "notifications",
        "utils.backup",
        "utils.webdav_sync",
    ):
        __import__(module)

    data_dir = Path(settings.DATA_DIR)
    directories = {
        data_dir,
        Path(settings.REPORTS_DIR),
        Path(settings.HISTORY_DIR),
        Path(settings.DOWNLOAD_DIR),
        PROJECT_ROOT / "logs",
        PROJECT_ROOT / "configs",
    }
    for directory in directories:
        _check_writable_directory(directory)
    for database in {Path(settings.DAILY_RESEARCH_DB_PATH), Path(settings.KEYWORD_DB_PATH)}:
        _check_sqlite(database)
    _check_lock_files(data_dir)
    _check_trigger_consumer(data_dir)


def _webui_paths(config: dict) -> tuple[Path, Path, set[Path]]:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    daily = config.get("daily_research") if isinstance(config.get("daily_research"), dict) else {}
    raw_data = paths.get("data_dir", "data")
    data_dir = (PROJECT_ROOT / str(raw_data)).resolve()
    raw_db = daily.get("db_path", "data/daily_research/daily_research.db")
    database = (PROJECT_ROOT / str(raw_db)).resolve()
    return data_dir, database, {data_dir, PROJECT_ROOT / "logs", PROJECT_ROOT / "configs"}


def _webui_checks(url: str, runtime_user: str) -> None:
    _drop_to_runtime_user(runtime_user)
    from utils.config_io import read_config_json, read_env

    config = read_config_json()
    read_env()
    data_dir, database, directories = _webui_paths(config)
    for directory in directories:
        _check_writable_directory(directory)
    # A fresh installation has no SQLite file yet.  The modern WebUI can be
    # started before the Worker, so create a configured nested database parent
    # here rather than reporting an unhealthy service until a research task
    # happens to create it first.  This stays inside the already-validated
    # project data path and is the same harmless directory initialization the
    # normal application performs before opening SQLite.
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ContainerHealthError(
            f"database directory cannot be initialized: {database.parent}: {exc}"
        ) from exc
    _check_sqlite(database)
    _check_writable_directory(data_dir / "run" / "webui_triggers")
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(64).decode("utf-8", errors="replace").strip().lower()
            _require(response.status == 200 and body == "ok", "WebUI health endpoint is not ready")
    except (OSError, ValueError) as exc:
        raise ContainerHealthError(f"WebUI health endpoint failed: {exc}") from exc


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local container readiness")
    parser.add_argument("mode", choices=("worker", "webui"))
    parser.add_argument("--runtime-user", default="adr")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8501/api/health",
        help="WebUI health endpoint for webui mode",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        if args.mode == "worker":
            _worker_checks(args.runtime_user)
        else:
            _webui_checks(args.url, args.runtime_user)
    except Exception as exc:
        print(json.dumps({"healthy": False, "error": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps({"healthy": True, "mode": args.mode}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
