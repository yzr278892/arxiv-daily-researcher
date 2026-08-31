"""Validated, durable WebUI-to-worker trigger protocol.

The browser container deliberately contains no Worker runtime dependencies.
It places a small JSON request in the shared data volume and the Worker's
watcher executes it.  Requests are written atomically and validated again in
the Worker so malformed files cannot turn into shell input.
"""

from __future__ import annotations

import argparse
from collections import deque
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


TRIGGER_SCHEMA_VERSION = 1
TRIGGER_DIRECTORY_NAME = "webui_triggers"
TRIGGER_STATUS_DIRECTORY_NAME = "status"
# Trigger requests and status receipts are operational audit records rather
# than a second long-term history database. Keep enough recent evidence for
# the WebUI/task diagnosis while bounding the shared-volume metadata.
TRIGGER_STATUS_RETENTION_DAYS = 30
TRIGGER_STATUS_MAX_RECORDS = 200
RESTART_REQUEST_DONE_RETENTION_DAYS = 30
RESTART_REQUEST_DONE_MAX_RECORDS = 50
_STATUS_RECORD_NAME_RE = re.compile(r"^(?:[0-9a-f]{32}|rejected_[0-9a-f]{32})\.json$")
_RESTART_DONE_NAME_RE = re.compile(
    r"^restart_worker\.request\.done-\d{8}T\d{6}(?:\d{1,9})?(?:-\d+)?$"
)
_STATUS_OUTPUT_TAIL_LINES = 120
_STATUS_SUMMARY_MAX_CHARS = 420
_URL_RE = re.compile(r"\bhttps?://[^\s<>()\[\]{}\"']+", re.IGNORECASE)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (?P<key>
        [\"']?(?:api[_-]?key|apikey|access[_-]?token|token|secret|password|
        authorization|x[_-]?api[_-]?key|webhook(?:[_-]?url)?)[\"']?
    )
    (?P<separator>\s*[:=]\s*)
    (?P<value>\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)
    """
)
_LOG_PREFIX_RE = re.compile(
    r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\s*\|\s*"
    r"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|\s*[^|]*\|\s*"
)
_STAGE_MARKER_RE = re.compile(r"(?:>{2,}\s*)?阶段\s*\d+\s*[:：]\s*(.+)")
_ERROR_MARKER_RE = re.compile(
    r"(?i)(?:任务失败|程序执行失败|异常终止|发生异常|failed|failure|error|exception|错误|失败)"
)
_MODE_STAGE_LABELS = {
    "daily_research": "每日研究",
    "trend_research": "趋势研究",
    "legacy_import": "旧历史导入",
    "history_data_repair": "历史数据补全",
    "history_omission_scan": "历史遗漏扫描",
    "supplement_run": "补充报告",
    "backfill_run": "过去日报补跑",
}
SUPPORTED_MODES = frozenset(
    {
        "daily_research",
        "trend_research",
        "legacy_import",
        "history_data_repair",
        "history_omission_scan",
        "supplement_run",
        "backfill_run",
    }
)
HISTORY_MAINTENANCE_MODES = frozenset(
    {"legacy_import", "history_data_repair", "history_omission_scan"}
)
NORMAL_TRIGGER_MODES = SUPPORTED_MODES - HISTORY_MAINTENANCE_MODES
# 面板触发的后台作业：不接受任何参数。
_NO_ARGS_MODES = frozenset(
    {"daily_research", "history_data_repair", "history_omission_scan", "supplement_run"}
)
# 最早可补跑的日期（arXiv 上线年份）。
_BACKFILL_EARLIEST = date(1991, 1, 1)
_CATEGORY_RE = re.compile(r"^[A-Za-z0-9.-]{1,64}$")
_MAX_REQUEST_BYTES = 32 * 1024
_MAX_KEYWORDS = 32
_MAX_KEYWORD_LENGTH = 500
_MAX_CATEGORIES = 64
_MAX_RESULTS = 5000
_MAX_ANALYSIS_PROMPT = 8000


class TriggerValidationError(ValueError):
    """Raised when a WebUI trigger request is not safe to execute."""


def trigger_directory(data_dir: Path) -> Path:
    """Return the queue directory shared by WebUI and the worker."""
    return Path(data_dir) / "run" / TRIGGER_DIRECTORY_NAME


def trigger_status_directory(data_dir: Path) -> Path:
    """Return the small, durable status directory for consumed requests."""
    return trigger_directory(data_dir) / TRIGGER_STATUS_DIRECTORY_NAME


def _validate_rotation_value(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _rotate_owned_records(
    directory: Path,
    filename_pattern: re.Pattern[str],
    *,
    max_records: int,
    retention_days: int,
    protected_names: set[str] | None = None,
) -> list[str]:
    """Bound application-owned audit files without touching unrelated files.

    ``max_records=0`` disables only the count cap. ``retention_days=0``
    disables only age expiry. A just-written ``protected_names`` record is
    retained even if a skewed filesystem timestamp would otherwise put it
    outside the selected set.
    """
    max_records = _validate_rotation_value(max_records, name="max_records")
    retention_days = _validate_rotation_value(
        retention_days, name="retention_days"
    )
    if not directory.is_dir():
        return []

    protected = protected_names or set()
    cutoff = time.time() - retention_days * 24 * 60 * 60 if retention_days else None
    entries: list[tuple[float, str, Path]] = []
    removed: list[str] = []
    for path in directory.iterdir():
        if not path.is_file() or not filename_pattern.fullmatch(path.name):
            continue
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and modified_at < cutoff and path.name not in protected:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
            continue
        entries.append((modified_at, path.name, path))

    if max_records:
        protected_count = sum(name in protected for _mtime, name, _path in entries)
        remaining_slots = max(0, max_records - protected_count)
        retained_non_protected = 0
        for _modified_at, name, path in sorted(entries, reverse=True):
            if name in protected:
                continue
            if retained_non_protected < remaining_slots:
                retained_non_protected += 1
                continue
            try:
                path.unlink()
                removed.append(name)
            except OSError:
                pass
    return sorted(removed)


def rotate_trigger_statuses(
    data_dir: Path,
    *,
    max_records: int = TRIGGER_STATUS_MAX_RECORDS,
    retention_days: int = TRIGGER_STATUS_RETENTION_DAYS,
    protected_names: set[str] | None = None,
) -> list[str]:
    """Rotate finished WebUI trigger status receipts by age and count."""
    return _rotate_owned_records(
        trigger_status_directory(Path(data_dir)),
        _STATUS_RECORD_NAME_RE,
        max_records=max_records,
        retention_days=retention_days,
        protected_names=protected_names,
    )


def rotate_restart_request_markers(
    data_dir: Path,
    *,
    max_records: int = RESTART_REQUEST_DONE_MAX_RECORDS,
    retention_days: int = RESTART_REQUEST_DONE_RETENTION_DAYS,
) -> list[str]:
    """Rotate archived worker-restart markers in the shared trigger queue."""
    return _rotate_owned_records(
        trigger_directory(Path(data_dir)),
        _RESTART_DONE_NAME_RE,
        max_records=max_records,
        retention_days=retention_days,
    )


def run_trigger_maintenance(data_dir: Path) -> Dict[str, list[str]]:
    """Run all bounded trigger-file maintenance actions for an app data dir."""
    return {
        "status_removed": rotate_trigger_statuses(data_dir),
        "restart_markers_removed": rotate_restart_request_markers(data_dir),
    }


def sanitize_task_error_summary(value: Any, *, max_chars: int = _STATUS_SUMMARY_MAX_CHARS) -> str:
    """Return a compact operator-facing error without credentials or URLs.

    Trigger status files are read by the WebUI and survive worker restarts.
    They must contain enough context to identify a failed stage without
    becoming a copy of a stack trace, request body, API key, or webhook URL.
    """
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        max_chars = _STATUS_SUMMARY_MAX_CHARS
    text = str(value or "").replace("\x00", " ").strip()
    if not text:
        return ""
    text = _LOG_PREFIX_RE.sub("", text)
    text = _URL_RE.sub("<链接已隐藏>", text)
    text = _BEARER_TOKEN_RE.sub("Bearer <凭据已隐藏>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}<凭据已隐藏>",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _stage_from_output_line(line: str, fallback: str) -> str:
    marker = _STAGE_MARKER_RE.search(line)
    if marker:
        detail = sanitize_task_error_summary(marker.group(1), max_chars=120)
        return f"阶段：{detail}" if detail else fallback
    lowered = line.lower()
    if "[historyrepair]" in lowered:
        return "历史数据补全"
    if "[historyomission]" in lowered:
        return "历史遗漏扫描"
    if "[legacy" in lowered:
        return "旧历史导入"
    if "[backfill" in lowered:
        return "过去日报补跑"
    return fallback


def summarize_trigger_failure_output(mode: str, lines: Sequence[str]) -> str:
    """Extract one sanitized stage-level failure summary from worker output."""
    stage = _MODE_STAGE_LABELS.get(str(mode), "后台任务")
    error = ""
    for raw_line in lines:
        line = sanitize_task_error_summary(raw_line, max_chars=700)
        if not line:
            continue
        stage = _stage_from_output_line(line, stage)
        # Retried warnings and tracebacks are useful in the full log but do
        # not explain the terminal failure on their own.
        lowered = line.lower()
        if "retrying" in lowered or lowered.startswith("traceback"):
            continue
        if _ERROR_MARKER_RE.search(line):
            error = line

    if error:
        if error.startswith(stage) or stage in error:
            return sanitize_task_error_summary(error)
        return sanitize_task_error_summary(f"{stage}：{error}")
    return sanitize_task_error_summary(f"{stage} 未正常完成，请查看运行日志")


def _forward_child_output(child: subprocess.Popen) -> list[str]:
    """Forward child output to the watcher log while retaining a tiny tail."""
    stream = getattr(child, "stdout", None)
    # Test doubles and legacy integrations may expose a mock/None stream.
    # Preserve their old wait-only behaviour instead of assuming a real pipe.
    if not isinstance(stream, io.TextIOBase):
        return []

    tail: deque[str] = deque(maxlen=_STATUS_OUTPUT_TAIL_LINES)
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            sys.stdout.write(line)
            sys.stdout.flush()
            tail.append(line)
    finally:
        try:
            stream.close()
        except OSError:
            pass
    return list(tail)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one JSON document without exposing a partially written request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        os.chmod(path, 0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_text_list(
    value: Any,
    *,
    field: str,
    max_count: int,
    max_length: int,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise TriggerValidationError(f"{field} must be a non-empty list")
    if len(value) > max_count:
        raise TriggerValidationError(f"{field} contains too many values")

    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TriggerValidationError(f"{field} entries must be strings")
        normalized = item.strip()
        if not normalized or len(normalized) > max_length or "\x00" in normalized:
            raise TriggerValidationError(f"{field} contains an invalid value")
        values.append(normalized)
    return values


def _validate_optional_date(value: Any, field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise TriggerValidationError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise TriggerValidationError(f"{field} must be an ISO date") from exc


def _validate_backfill_date(value: Any, field: str) -> str:
    """Validate one past-date queue item accepted by ``backfill_run``."""
    if not isinstance(value, str) or not value.strip():
        raise TriggerValidationError(f"{field} must be an ISO date (YYYY-MM-DD)")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise TriggerValidationError(
            f"{field} must be an ISO date (YYYY-MM-DD)"
        ) from exc
    if parsed >= date.today():
        raise TriggerValidationError(f"{field} must be in the past")
    if parsed < _BACKFILL_EARLIEST:
        raise TriggerValidationError(f"{field} is unreasonably old")
    return parsed.isoformat()


def _validate_request_id(value: Any) -> str:
    if not isinstance(value, str):
        raise TriggerValidationError("request_id must be a UUID")
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError) as exc:
        raise TriggerValidationError("request_id must be a UUID") from exc


def validate_trigger_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a normalized trigger payload or raise before executing anything."""
    if not isinstance(payload, Mapping):
        raise TriggerValidationError("trigger payload must be an object")
    if payload.get("schema_version") != TRIGGER_SCHEMA_VERSION:
        raise TriggerValidationError("unsupported trigger schema version")

    mode = payload.get("mode")
    if mode not in SUPPORTED_MODES:
        raise TriggerValidationError(f"unsupported trigger mode: {mode!r}")

    args = payload.get("args", {})
    if not isinstance(args, Mapping):
        raise TriggerValidationError("trigger args must be an object")

    normalized: Dict[str, Any] = {
        "schema_version": TRIGGER_SCHEMA_VERSION,
        "request_id": _validate_request_id(payload.get("request_id")),
        "created_at": str(payload.get("created_at", "")),
        "mode": mode,
        "args": {},
    }
    # A retry remains an ordinary durable request, but retaining the previous
    # request ID lets the history-maintenance UI replace a resolved failure
    # with its retry instead of leaving a permanent stale error row behind.
    retry_of = payload.get("retry_of")
    if retry_of not in (None, ""):
        normalized["retry_of"] = _validate_request_id(retry_of)

    if mode in _NO_ARGS_MODES:
        if args:
            raise TriggerValidationError(f"{mode} does not accept trigger arguments")
        return normalized

    if mode == "legacy_import":
        unexpected = set(args).difference({"full_repair"})
        if unexpected:
            raise TriggerValidationError("legacy_import contains unsupported arguments")
        # Empty arguments retain compatibility with queued requests created by
        # earlier v4 releases and let the worker use its saved configuration.
        # The current WebUI always sends an explicit value so a click honors
        # an unsaved toggle state as well.
        if "full_repair" not in args:
            return normalized
        enabled = args.get("full_repair")
        if not isinstance(enabled, bool):
            raise TriggerValidationError("legacy_import.full_repair must be a boolean")
        normalized["args"] = {"full_repair": enabled}
        return normalized

    if mode == "backfill_run":
        allowed = {"target_date", "date_from", "date_to"}
        unexpected = set(args).difference(allowed)
        if unexpected:
            raise TriggerValidationError("backfill_run contains unsupported arguments")

        target_date = args.get("target_date")
        date_from = args.get("date_from")
        date_to = args.get("date_to")
        has_target = target_date not in (None, "")
        has_range = date_from not in (None, "") or date_to not in (None, "")
        if has_target and has_range:
            raise TriggerValidationError(
                "backfill_run cannot mix target_date with date_from/date_to"
            )
        if has_target:
            normalized["args"] = {
                "target_date": _validate_backfill_date(target_date, "target_date")
            }
            return normalized
        if not has_range:
            raise TriggerValidationError(
                "backfill_run requires target_date or date_from/date_to"
            )
        start = _validate_backfill_date(date_from, "date_from")
        end = _validate_backfill_date(date_to, "date_to")
        if start > end:
            raise TriggerValidationError("date_from must not be after date_to")
        normalized["args"] = {"date_from": start, "date_to": end}
        return normalized

    keywords = _validate_text_list(
        args.get("keywords"),
        field="keywords",
        max_count=_MAX_KEYWORDS,
        max_length=_MAX_KEYWORD_LENGTH,
    )
    categories_raw = args.get("categories", [])
    if not isinstance(categories_raw, list) or len(categories_raw) > _MAX_CATEGORIES:
        raise TriggerValidationError("categories must be a bounded list")
    categories: list[str] = []
    for category in categories_raw:
        if not isinstance(category, str) or not _CATEGORY_RE.fullmatch(category.strip()):
            raise TriggerValidationError("categories contains an invalid category")
        categories.append(category.strip())

    sort_order = args.get("sort_order", "ascending")
    if sort_order not in {"ascending", "descending"}:
        raise TriggerValidationError("sort_order must be ascending or descending")

    max_results = args.get("max_results")
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise TriggerValidationError("max_results must be an integer")
    if not 1 <= max_results <= _MAX_RESULTS:
        raise TriggerValidationError(f"max_results must be between 1 and {_MAX_RESULTS}")

    date_from = _validate_optional_date(args.get("date_from"), "date_from")
    date_to = _validate_optional_date(args.get("date_to"), "date_to")
    if date_from and date_to and date_from > date_to:
        raise TriggerValidationError("date_from must not be after date_to")

    analysis_prompt = args.get("analysis_prompt", "")
    if analysis_prompt is None:
        analysis_prompt = ""
    if not isinstance(analysis_prompt, str):
        raise TriggerValidationError("analysis_prompt must be a string")
    analysis_prompt = analysis_prompt.strip()
    if len(analysis_prompt) > _MAX_ANALYSIS_PROMPT:
        raise TriggerValidationError(
            f"analysis_prompt must be at most {_MAX_ANALYSIS_PROMPT} characters"
        )

    normalized["args"] = {
        "keywords": keywords,
        "date_from": date_from,
        "date_to": date_to,
        "categories": categories,
        "sort_order": sort_order,
        "max_results": max_results,
        "analysis_prompt": analysis_prompt,
    }
    return normalized


def build_trigger_payload(
    mode: str,
    *,
    retry_of: str | None = None,
    **args: Any,
) -> Dict[str, Any]:
    """Create a normalized request payload for the WebUI container."""
    payload: Dict[str, Any] = {
        "schema_version": TRIGGER_SCHEMA_VERSION,
        "request_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "args": args,
    }
    if retry_of:
        payload["retry_of"] = retry_of
    return validate_trigger_payload(payload)


def enqueue_trigger(
    data_dir: Path,
    mode: str,
    *,
    retry_of: str | None = None,
    **args: Any,
) -> Path:
    """Atomically enqueue one validated worker request and return its path."""
    payload = build_trigger_payload(mode, retry_of=retry_of, **args)
    queue_dir = trigger_directory(Path(data_dir))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    request_path = queue_dir / f"{timestamp}_{payload['request_id']}.json"
    _atomic_write_json(request_path, payload)
    return request_path


def read_trigger_payload(request_path: Path) -> Dict[str, Any]:
    """Load and validate a queued request with a strict size limit."""
    request_path = Path(request_path)
    try:
        if request_path.stat().st_size > _MAX_REQUEST_BYTES:
            raise TriggerValidationError("trigger request exceeds size limit")
        with request_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise TriggerValidationError("trigger request is not valid JSON") from exc
    return validate_trigger_payload(payload)


def _history_maintenance_schedule_from_runtime_config() -> tuple[str, str, str]:
    """Read the current scheduler settings without retaining stale config.

    The worker watcher invokes this in a short-lived process before claiming
    an eligible history request.  That deliberately makes a just-saved WebUI
    scheduling change effective on the next poll without a worker restart.
    """
    from utils.config_io import read_config_json
    from utils.history_maintenance import (
        DEFAULT_HISTORY_MAINTENANCE_RUN_MODE,
        DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END,
        DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START,
        resolve_history_maintenance_schedule,
    )

    config = read_config_json()
    history_maintenance = config.get("history_maintenance", {})
    if not isinstance(history_maintenance, Mapping):
        raise ValueError("history_maintenance 配置段必须是对象")
    return resolve_history_maintenance_schedule(
        history_maintenance.get("run_mode", DEFAULT_HISTORY_MAINTENANCE_RUN_MODE),
        history_maintenance.get(
            "time_window_start", DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START
        ),
        history_maintenance.get(
            "time_window_end", DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END
        ),
    )


def _worker_has_active_lock(data_dir: Path) -> bool:
    """Whether a worker operation is currently holding a visible run lock."""
    from utils.run_lock import is_lock_held

    run_dir = Path(data_dir) / "run"
    try:
        lock_paths = sorted(run_dir.glob("*.lock"), key=lambda path: path.name)
    except OSError:
        # An unreadable lock directory is not proof that the backend is idle.
        # Defer history work rather than risking a concurrent SQLite writer.
        return True
    for lock_path in lock_paths:
        try:
            if is_lock_held(lock_path):
                return True
        except OSError:
            return True
    return False


def next_eligible_trigger_request(
    data_dir: Path,
    *,
    now: Optional[datetime] = None,
    history_schedule: tuple[object, object, object] | None = None,
) -> Optional[Path]:
    """Return one queue request that the watcher may claim now.

    Normal research work always takes priority. A queued history task is left
    in place until there are no held worker run locks; a time-window schedule
    additionally waits for its local-time interval. A malformed request is
    returned in filename order so the regular executor can reject and consume
    it instead of leaving an unselectable poison file in the durable queue.
    """
    queue_dir = trigger_directory(Path(data_dir))
    try:
        request_paths = sorted(
            (path for path in queue_dir.glob("*.json") if path.is_file()),
            key=lambda path: path.name,
        )
    except OSError:
        return None
    if not request_paths:
        return None

    requests: list[tuple[Path, Dict[str, Any]]] = []
    for request_path in request_paths:
        try:
            payload = read_trigger_payload(request_path)
        except (OSError, TriggerValidationError, ValueError):
            return request_path
        requests.append((request_path, payload))

    # A normal task submitted after a waiting history operation must not be
    # trapped behind it. Preserve filename order among normal requests.
    for request_path, payload in requests:
        if payload["mode"] in NORMAL_TRIGGER_MODES:
            return request_path

    # All remaining validated requests are historical maintenance. Defer all
    # of them while any visible worker operation owns a lock, including a
    # cron-launched keyword maintenance task that is not represented by this
    # WebUI queue.
    if _worker_has_active_lock(Path(data_dir)):
        return None

    if history_schedule is None:
        run_mode, window_start, window_end = (
            _history_maintenance_schedule_from_runtime_config()
        )
    else:
        from utils.history_maintenance import resolve_history_maintenance_schedule

        run_mode, window_start, window_end = resolve_history_maintenance_schedule(
            *history_schedule
        )
    if run_mode == "time_window":
        from utils.history_maintenance import history_maintenance_window_is_open

        if not history_maintenance_window_is_open(
            now or datetime.now(), window_start, window_end
        ):
            return None
    return requests[0][0]


def build_main_command(payload: Mapping[str, Any], project_root: Path) -> list[str]:
    """Build a list-only command; untrusted request text is never shell-expanded."""
    request = validate_trigger_payload(payload)
    command = [sys.executable, str(Path(project_root) / "main.py"), "--mode", request["mode"]]
    if request["mode"] == "legacy_import":
        if "full_repair" in request["args"]:
            command.append(
                "--legacy-full-repair"
                if request["args"]["full_repair"]
                else "--no-legacy-full-repair"
            )
    if request["mode"] == "backfill_run":
        args = request["args"]
        if args.get("target_date"):
            command.extend(["--target-date", args["target_date"]])
        else:
            command.extend(["--date-from", args["date_from"], "--date-to", args["date_to"]])
    if request["mode"] == "trend_research":
        args = request["args"]
        command.extend(["--keywords", *args["keywords"]])
        if args["date_from"]:
            command.extend(["--date-from", args["date_from"]])
        if args["date_to"]:
            command.extend(["--date-to", args["date_to"]])
        if args["categories"]:
            command.extend(["--categories", *args["categories"]])
        command.extend(["--sort-order", args["sort_order"], "--max-results", str(args["max_results"])])
        if args.get("analysis_prompt"):
            command.extend(["--analysis-prompt", args["analysis_prompt"]])
    return command


def _status_receipt_args(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only retry-safe maintenance options in a durable status receipt.

    A trigger receipt is readable by the panel for several weeks.  It must
    never become a generic copy of a user request (trend prompts can be long
    and may contain private research context).  Legacy import's boolean mode
    switch is the sole option required to make its retry deterministic.
    """
    if payload.get("mode") != "legacy_import":
        return {}
    args = payload.get("args")
    if not isinstance(args, Mapping) or not isinstance(args.get("full_repair"), bool):
        return {}
    return {"full_repair": args["full_repair"]}


def _write_status(data_dir: Path, payload: Mapping[str, Any], state: str, **details: Any) -> Path:
    safe_details = dict(details)
    for key in ("error", "error_summary"):
        if key in safe_details:
            safe_details[key] = sanitize_task_error_summary(safe_details[key])
    status_payload: Dict[str, Any] = {
        "request_id": payload["request_id"],
        "mode": payload["mode"],
        "args": _status_receipt_args(payload),
        "created_at": payload.get("created_at", ""),
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **safe_details,
    }
    if payload.get("retry_of"):
        status_payload["retry_of"] = payload["retry_of"]
    status_path = trigger_status_directory(data_dir) / f"{payload['request_id']}.json"
    _atomic_write_json(status_path, status_payload)
    # Rotation follows the atomic replacement so the current receipt is never
    # lost even if filesystem timestamps on older records are skewed.
    rotate_trigger_statuses(data_dir, protected_names={status_path.name})
    return status_path


def _write_pid_file(pid_file: Path, pid: int) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = pid_file.with_name(f".{pid_file.name}.{uuid.uuid4().hex}.tmp")
    try:
        # Include a process-start token so another namespace cannot mistake a
        # recycled numeric PID for this worker child.  Local UI code uses this
        # only for status; the worker still owns all lifecycle decisions.
        started_at = datetime.now(timezone.utc).isoformat()
        temporary_path.write_text(
            f"PID={pid}, started={started_at}\n", encoding="utf-8"
        )
        os.replace(temporary_path, pid_file)
    finally:
        temporary_path.unlink(missing_ok=True)


def _remove_own_pid_file(pid_file: Optional[Path], pid: Optional[int]) -> None:
    if pid_file is None or pid is None:
        return
    try:
        content = pid_file.read_text(encoding="utf-8").strip()
        if content == str(pid) or re.search(rf"(?:^|\b)PID={re.escape(str(pid))}(?:\b|,)", content):
            pid_file.unlink(missing_ok=True)
    except OSError:
        pass


def stop_request_directory(data_dir: Path) -> Path:
    """Stop requests are small JSON files under ``<data>/run/stop_requests``."""
    directory = Path(data_dir) / "run" / "stop_requests"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def request_stop(data_dir: Path, pid: int) -> Path:
    """Atomically ask the worker to stop the run owning ``pid`` (best effort)."""
    target = stop_request_directory(data_dir) / f"stop_{int(pid)}.json"
    _atomic_write_json(
        target,
        {
            "schema_version": 1,
            "pid": int(pid),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return target


def _monitor_stop_requests(
    child: subprocess.Popen,
    data_dir: Path,
    *,
    poll_seconds: float = 2.0,
) -> None:
    """Watch the shared stop-request directory and SIGTERM the child on match.

    Runs as a daemon thread next to ``child.wait()``.  ``main.py`` maps
    SIGTERM to its interrupt path, so the pipeline records an interrupted
    state and its durable queue keeps already-completed stages.  The consumed
    request is removed; requests for other PIDs are left for their owners.
    """
    directory = stop_request_directory(data_dir)
    while child.poll() is None:
        try:
            for request in directory.glob("stop_*.json"):
                try:
                    payload = json.loads(request.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if payload.get("pid") == child.pid:
                    request.unlink(missing_ok=True)
                    print(
                        f"[webui-trigger] Stop requested for PID {child.pid}; sending SIGTERM",
                        file=sys.stderr,
                    )
                    child.send_signal(signal.SIGTERM)
                    return
        except OSError:
            pass
        time.sleep(poll_seconds)


def execute_trigger_request(
    request_path: Path,
    *,
    project_root: Optional[Path] = None,
    pid_file: Optional[Path] = None,
) -> int:
    """Execute one claimed request and persist a terminal status for the UI.

    ``request_path`` is expected to have been atomically renamed from ``.json``
    to ``.running`` by the worker watcher.  The request is removed only after a
    terminal status has been written, so a completed action has visible audit
    evidence without leaving queue files to be executed twice.
    """
    request_path = Path(request_path)
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    data_dir = request_path.parent.parent.parent
    payload: Optional[Dict[str, Any]] = None
    child: Optional[subprocess.Popen] = None
    output_tail: list[str] = []

    try:
        payload = read_trigger_payload(request_path)
        main_path = root / "main.py"
        if not main_path.is_file():
            raise RuntimeError(f"worker entrypoint is unavailable: {main_path}")

        command = build_main_command(payload, root)
        started_at = datetime.now(timezone.utc).isoformat()
        _write_status(
            data_dir,
            payload,
            "running",
            command=command,
            started_at=started_at,
        )
        child = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        stop_monitor = threading.Thread(
            target=_monitor_stop_requests,
            args=(child, data_dir),
            daemon=True,
            name=f"stop-monitor-{child.pid}",
        )
        stop_monitor.start()
        if pid_file is not None:
            _write_pid_file(Path(pid_file), child.pid)
        output_tail = _forward_child_output(child)
        return_code = child.wait()
        if return_code == 0:
            state = "succeeded"
        elif return_code == 130:
            # main.py maps SIGTERM to its interrupt path; distinguish it from
            # a genuine failure so the UI can explain what happened.
            state = "interrupted"
        elif return_code == 75:
            # run_lock: the same task was already active, so nothing ran.
            state = "skipped_busy"
        else:
            state = "failed"
        details: Dict[str, Any] = {
            "return_code": return_code,
            "command": command,
            "started_at": started_at,
        }
        if state == "failed":
            details["error_summary"] = summarize_trigger_failure_output(
                payload["mode"], output_tail
            )
        elif state == "interrupted":
            details["error_summary"] = "任务已中断；未完成工作已保留供后续重试"
        _write_status(data_dir, payload, state, **details)
        return return_code
    except KeyboardInterrupt:
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if payload is not None:
            _write_status(data_dir, payload, "interrupted", return_code=130)
        return 130
    except Exception as exc:
        if payload is not None:
            _write_status(
                data_dir,
                payload,
                "rejected",
                error_summary=sanitize_task_error_summary(str(exc)),
            )
        else:
            # A malformed request has no trustworthy request_id.  Keep a small
            # status record for diagnostics, then consume it so the watcher
            # cannot spin forever on the same invalid input.  Keep it outside
            # the queue directory: a ``*.json`` record next to the request
            # would itself be mistaken for another request by the watcher.
            error_path = trigger_status_directory(data_dir) / f"rejected_{uuid.uuid4().hex}.json"
            _atomic_write_json(
                error_path,
                {
                    "state": "rejected",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "error_summary": sanitize_task_error_summary(str(exc)),
                },
            )
            rotate_trigger_statuses(data_dir, protected_names={error_path.name})
        print(f"[webui-trigger] Request failed: {exc}", file=sys.stderr)
        return 1
    finally:
        _remove_own_pid_file(Path(pid_file) if pid_file is not None else None, child.pid if child else None)
        request_path.unlink(missing_ok=True)


def _parse_cli(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one validated WebUI trigger request")
    parser.add_argument(
        "request_path",
        type=Path,
        nargs="?",
        help="Claimed .running request file",
    )
    parser.add_argument("--pid-file", type=Path, default=None, help="Shared current worker PID file")
    parser.add_argument(
        "--maintain-trigger-files",
        action="store_true",
        help="Rotate bounded trigger status and restart-marker audit files",
    )
    parser.add_argument(
        "--next-eligible-request",
        action="store_true",
        help="Print the next request currently eligible for the worker watcher",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Application data directory used with watcher maintenance commands",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_cli(argv)
    if args.maintain_trigger_files and args.next_eligible_request:
        raise SystemExit(
            "--maintain-trigger-files and --next-eligible-request cannot be combined"
        )
    if args.maintain_trigger_files:
        if args.request_path is not None or args.data_dir is None:
            raise SystemExit(
                "--maintain-trigger-files requires --data-dir and no request path"
            )
        run_trigger_maintenance(args.data_dir)
        return 0
    if args.next_eligible_request:
        if args.request_path is not None or args.data_dir is None:
            raise SystemExit(
                "--next-eligible-request requires --data-dir and no request path"
            )
        try:
            request_path = next_eligible_trigger_request(args.data_dir)
        except (OSError, ValueError) as exc:
            print(f"[webui-trigger] 无法读取历史维护调度配置: {exc}", file=sys.stderr)
            return 2
        if request_path is not None:
            print(request_path)
        return 0
    if args.request_path is None:
        raise SystemExit(
            "request_path is required unless a watcher maintenance command is used"
        )
    return execute_trigger_request(args.request_path, pid_file=args.pid_file)


if __name__ == "__main__":
    sys.exit(main())
