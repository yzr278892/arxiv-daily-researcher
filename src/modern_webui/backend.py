"""Shared read/write operations for the lightweight management WebUI.

The module deliberately uses the same configuration files, SQLite ledger, and
durable Worker trigger queue as CLI and scheduled jobs.  It never creates a
second settings store or background process.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import inspect
import json
import mimetypes
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

from utils.backup import (
    LOCAL_BACKUP_RETENTION_DAYS,
    LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    create_backup,
    export_backup_zip,
    list_local_backups,
    restore_backup_archive,
)
from utils.config_io import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_ENV_PATH,
    _resolve_project_relative_config_path,
    build_config_dict,
    ensure_runtime_config_path,
    flatten_config_dict,
    read_config_json,
    read_env,
    validate_llm_connection,
    validate_mineru_connection,
    validate_openalex_connection,
    validate_semantic_scholar_connection,
    validate_smtp_connection,
    write_config_json,
    write_env,
)
from utils.daily_research_store import DailyResearchStore
from utils.run_lock import DatabaseRestoreBusyError, database_restore_activity_gate, is_lock_held
from utils.source_registry import (
    OPENALEX_JOURNAL_CATALOG,
    OPENALEX_JOURNAL_TYPE,
    builtin_extra_source_definitions,
    source_display_names,
)
from utils.webdav_sync import WebDAVSync
from utils.webui_trigger import (
    SUPPORTED_MODES,
    enqueue_trigger,
    read_trigger_payload,
    request_stop,
    sanitize_task_error_summary,
    trigger_directory,
    trigger_status_directory,
)
from utils.history_maintenance import (
    DEFAULT_HISTORY_MAINTENANCE_RUN_MODE,
    DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END,
    DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START,
    resolve_history_maintenance_schedule,
)
from modern_webui.arxiv_categories import ARXIV_CATEGORIES, format_arxiv_category


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_REPORTS_DIR = DEFAULT_DATA_DIR / "reports"
DEFAULT_DB_RELATIVE_PATH = Path("daily_research") / "daily_research.db"
LOGS_DIR = PROJECT_ROOT / "logs"
TREND_PROMPT_TEMPLATES_PATH = DEFAULT_DATA_DIR / "trend_prompt_templates.json"
DEFAULT_TREND_PROMPT_TEMPLATE_NAME = "综合分析"
BUILTIN_TREND_PROMPT_TEMPLATES: dict[str, str] = {
    DEFAULT_TREND_PROMPT_TEMPLATE_NAME: """请对以下论文集合进行综合趋势分析，用 Markdown 格式输出，包含以下五个部分：

## 1. 热点话题
识别 3-5 个主要研究子方向；每个子方向列出代表性论文（论文标识 + 标题），评估热度（高/中/低）和趋势（上升/稳定/下降）。

## 2. 时间演变
将时间段分为前期和后期，概述研究重心的变化，标注关键转折点和代表性论文。

## 3. 核心研究者
列出论文数量最多的 Top-5 研究者及其主要研究方向，简要描述主要合作团队。

## 4. 研究空白与机会
指出覆盖较少的方向和潜在的交叉研究机会（2-3 个即可）。

## 5. 方法论趋势
列出最常见的研究方法（Top-5），标注新兴方法。

要求：
- 分析必须基于提供的论文数据，不要引用外部论文
- 每个部分简洁明了，避免冗长论述
- 提及论文时标注论文标识""",
    "研究脉络综述": """请基于本次收集到的论文，写一份面向研究者的趋势综述。

按以下结构组织：研究问题与范围、关键进展（按主题或时间线）、代表性方法与结果、不同路线之间的联系、已知局限，以及下一步值得跟踪的问题。只使用论文中可核实的信息；证据不足时明确说明，不要补充未给出的实验结果、结论或引用。""",
    "方法与证据比较": """请比较本次论文采用的方法与证据强度，帮助研究者快速判断每条路线的成熟度。

说明每类方法解决的核心问题、使用的理论/实验/数据证据、主要优势和局限、可复现性或验证条件，以及不同论文之间可直接比较与不可直接比较的部分。最后给出简洁的对比结论。仅依据已提供的论文信息，不推断未报告的指标。""",
    "前沿机会与风险": """请提炼本次论文反映出的研究机会、技术瓶颈和潜在风险。

先概括共识与分歧，再区分近期可验证的问题和中长期方向；对每项机会或风险说明它对应的论文证据、依赖条件和不确定性。最后给出不超过五条优先跟踪建议。避免把推测写成事实，也不要添加论文未支持的应用前景。""",
}
HISTORY_MODES = frozenset({"legacy_import", "history_data_repair", "history_omission_scan"})
OPERATING_MODES = frozenset({"daily_research", "backfill_run", "trend_research"})
LOCK_NAMES = (
    "daily_research.lock",
    "legacy_import.lock",
    "history_data_repair.lock",
    "history_omission_scan.lock",
    "supplement_run.lock",
    "backfill_run.lock",
)
# Lock names are also used for status presentation.  Trend jobs intentionally
# use parameterized filenames (``trend_research_<hash>.lock``), which are
# matched by prefix rather than appearing in the fixed compatibility tuple.
_LOCK_KIND_PREFIXES = {
    "daily": ("daily_research.lock", "supplement_run.lock"),
    "past": ("backfill_run.lock",),
    "trend": ("trend_research_",),
    "history": (
        "legacy_import.lock",
        "history_data_repair.lock",
        "history_omission_scan.lock",
    ),
}
_STOPPABLE_TASK_KINDS = frozenset(_LOCK_KIND_PREFIXES)
_LIVE_LOG_PREFIXES = {
    "daily_research.lock": ("daily_", "cron_", "startup_"),
    "legacy_import.lock": ("legacy_import_",),
    "history_data_repair.lock": ("history_data_repair_",),
    "history_omission_scan.lock": ("history_omission_scan_",),
    "supplement_run.lock": ("supplement_run_", "supplement_"),
    "backfill_run.lock": ("backfill_run_", "backfill_"),
}
MODE_LABELS = {
    "daily": "每日研究",
    "daily_research": "每日研究",
    "backfill": "过去日报",
    "backfill_run": "过去日报",
    "trend": "趋势任务",
    "trend_research": "趋势任务",
    "legacy_import": "旧版本历史导入",
    "history_data_repair": "历史数据补全",
    "history_omission_scan": "历史遗漏扫描",
    "supplement": "补充报告",
    "supplement_run": "补充报告",
}
PHASE_LABELS = {
    "prepare": "准备运行",
    "scan": "扫描数据源",
    "score": "评分筛选",
    "analyze": "深度分析",
    "report": "生成报告",
    "legacy_import": "导入旧历史",
    "legacy_history": "读取历史记录",
    "legacy_keywords": "整理关键词",
    "legacy_reports": "读取历史报告",
    "legacy_write": "写入 SQLite",
    "legacy_backlog": "整理补充任务",
    "legacy_scan": "扫描遗漏论文",
    "legacy_supplement": "生成补充报告",
    "history_repair": "补全历史数据",
    "history_omission_scan": "扫描历史遗漏",
    "history_omission_week": "生成周补充报告",
}
HISTORY_TASK_LABELS = {
    "legacy_import": "旧版本历史导入",
    "history_data_repair": "历史数据补全",
    "history_omission_scan": "历史遗漏扫描",
}
HISTORY_RUN_KINDS = {
    "legacy_import": "legacy_import",
    "history_data_repair": "history_data_repair",
    "history_omission_scan": "history_omission_scan",
}
_LIVE_TASK_STATES = frozenset({"queued", "starting", "running"})
_RETRYABLE_TASK_STATES = frozenset(
    {"failed", "rejected", "interrupted", "skipped_busy"}
)
_TRIGGER_STALE_AFTER_SECONDS = 30

# Secrets never leave the server.  An empty form input therefore keeps the
# existing value; explicit clearing is available through ``clear_env``.
SECRET_ENV_FIELDS = frozenset(
    {
        "CHEAP_LLM__API_KEY",
        "SMART_LLM__API_KEY",
        "MINERU_API_KEY",
        "OPENALEX_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "SMTP_PASSWORD",
        "WECHAT_WEBHOOK_URL",
        "DINGTALK_WEBHOOK_URL",
        "DINGTALK_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "SLACK_WEBHOOK_URL",
        "GENERIC_WEBHOOK_URL",
        "WEBDAV_PASSWORD",
    }
)
PUBLIC_ENV_FIELDS = frozenset(
    {
        "CHEAP_LLM__BASE_URL",
        "CHEAP_LLM__MODEL_NAME",
        "CHEAP_LLM__TEMPERATURE",
        "SMART_LLM__BASE_URL",
        "SMART_LLM__MODEL_NAME",
        "SMART_LLM__TEMPERATURE",
        "ENABLE_OPENALEX",
        "ENABLE_SEMANTIC_SCHOLAR_TLDR",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_FROM",
        "SMTP_TO",
        "SMTP_USE_TLS",
        "TELEGRAM_CHAT_ID",
        "WEBDAV_URL",
        "WEBDAV_USERNAME",
    }
)
WRITABLE_ENV_FIELDS = SECRET_ENV_FIELDS | PUBLIC_ENV_FIELDS
_CONFIG_FIELDS = frozenset(inspect.signature(build_config_dict).parameters)
_PID_RE = re.compile(r"(?:^|\b)PID=(\d+)(?:\b|,)")

# The panel handles many short, read-only requests in one long-lived process.
# Parsing the commented JSON5 runtime config and recreating ``DailyResearchStore``
# for each one dominated page-load time on a populated SQLite database.  Keep
# only in-process, file-backed caches here: external edits are noticed through
# the runtime config file signature, while explicit UI writes/restores clear
# their relevant entries immediately.
_RUNTIME_CACHE_LOCK = RLock()
_FLAT_CONFIG_CACHE: dict[str, Any] | None = None
_FLAT_CONFIG_CACHE_SIGNATURE: tuple[Path, int | None, int | None, int | None] | None = None
_STORE_CACHE: dict[Path, DailyResearchStore] = {}


class ModernWebUIError(ValueError):
    """An expected, safe error to expose to an authenticated operator."""


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_json_value(value: Any, *, depth: int = 0) -> None:
    """Reject oversized/non-JSON setting payloads before they reach a file."""
    if depth > 5:
        raise ModernWebUIError("配置层级过深。")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > 12_000 or "\x00" in value:
            raise ModernWebUIError("配置字段长度无效。")
        return
    if isinstance(value, list):
        if len(value) > 500:
            raise ModernWebUIError("配置列表过长。")
        for item in value:
            _ensure_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100:
            raise ModernWebUIError("配置对象过大。")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 100:
                raise ModernWebUIError("配置字段名无效。")
            _ensure_json_value(item, depth=depth + 1)
        return
    raise ModernWebUIError("配置包含不支持的数据类型。")


def _runtime_config_signature() -> tuple[Path, int | None, int | None, int | None]:
    """Return a cheap change token for the live portable configuration."""
    path = ensure_runtime_config_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return path, None, None, None
    return path, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _invalidate_runtime_caches(*, clear_config: bool = True, clear_store: bool = True) -> None:
    """Drop process-local read caches after an operation changes their source."""
    global _FLAT_CONFIG_CACHE, _FLAT_CONFIG_CACHE_SIGNATURE
    with _RUNTIME_CACHE_LOCK:
        if clear_config:
            _FLAT_CONFIG_CACHE = None
            _FLAT_CONFIG_CACHE_SIGNATURE = None
        if clear_store:
            _STORE_CACHE.clear()


def flat_config() -> dict[str, Any]:
    """Read the flattened runtime config, reusing it until the file changes.

    A copy is returned so a caller cannot accidentally mutate the shared cache
    and affect another authenticated browser request.
    """
    global _FLAT_CONFIG_CACHE, _FLAT_CONFIG_CACHE_SIGNATURE
    signature = _runtime_config_signature()
    with _RUNTIME_CACHE_LOCK:
        if (
            _FLAT_CONFIG_CACHE is not None
            and _FLAT_CONFIG_CACHE_SIGNATURE == signature
        ):
            return deepcopy(_FLAT_CONFIG_CACHE)

    raw = read_config_json(signature[0])
    flattened = flatten_config_dict(raw) if isinstance(raw, dict) else {}
    with _RUNTIME_CACHE_LOCK:
        _FLAT_CONFIG_CACHE = flattened
        _FLAT_CONFIG_CACHE_SIGNATURE = signature
    return deepcopy(flattened)


def configured_data_dir(flat: Mapping[str, Any] | None = None) -> Path:
    values = flat or flat_config()
    try:
        return _resolve_project_relative_config_path(
            values.get("data_dir", "data"), label="paths.data_dir"
        )
    except (TypeError, ValueError):
        return DEFAULT_DATA_DIR


def configured_db_path(flat: Mapping[str, Any] | None = None) -> Path:
    values = flat or flat_config()
    raw = values.get("daily_research_db_path")
    if isinstance(raw, str) and raw.strip():
        try:
            return _resolve_project_relative_config_path(
                raw, label="daily_research.db_path"
            )
        except ValueError:
            pass
    return configured_data_dir(values) / DEFAULT_DB_RELATIVE_PATH


def configured_reports_dir(flat: Mapping[str, Any] | None = None) -> Path:
    values = flat or flat_config()
    raw = values.get("reports")
    if isinstance(raw, str) and raw.strip():
        try:
            return _resolve_project_relative_config_path(raw, label="paths.reports")
        except ValueError:
            pass
    return configured_data_dir(values) / "reports"


def open_store(
    flat: Mapping[str, Any] | None = None, *, create: bool = False
) -> DailyResearchStore | None:
    """Open the shared history store without changing ordinary empty-state UX.

    Read-only pages deliberately keep showing their existing ``no database``
    message until the worker has produced data.  A report's in-card preference
    controls are different: the UI initialises the small SQLite ledger on
    first use so an archived report can be marked before a daily run.  The
    explicit ``create`` flag keeps those two behaviours aligned without
    accidentally creating a database merely by opening a dashboard page.
    """
    path = configured_db_path(flat)
    if not create and not path.is_file():
        with _RUNTIME_CACHE_LOCK:
            _STORE_CACHE.pop(path, None)
        return None
    with _RUNTIME_CACHE_LOCK:
        cached = _STORE_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        store = DailyResearchStore(path)
    except Exception:
        return None
    with _RUNTIME_CACHE_LOCK:
        # Concurrent first requests can race through the short constructor.
        # Keep one shared instance; its actual SQLite connections remain
        # per-operation, so this does not share a connection between threads.
        return _STORE_CACHE.setdefault(path, store)


def public_settings() -> dict[str, Any]:
    """Return configuration plus redacted environment values for the UI."""
    env = read_env()
    return {
        "config": flat_config(),
        "env": {key: str(env.get(key) or "") for key in PUBLIC_ENV_FIELDS},
        "secrets": {key: bool(str(env.get(key) or "").strip()) for key in SECRET_ENV_FIELDS},
        "builtin_sources": [
            {
                "type": OPENALEX_JOURNAL_TYPE,
                "code": "prl",
                "display_name": OPENALEX_JOURNAL_CATALOG["prl"]["display_name"],
                "full_name": OPENALEX_JOURNAL_CATALOG["prl"]["full_name"],
                "issn": list(OPENALEX_JOURNAL_CATALOG["prl"]["issn"]),
            },
            *builtin_extra_source_definitions(),
        ],
        # Keep both modern selectors on the same complete arXiv catalog as
        # the old presentation layer.  The display label is sent by the server so
        # category additions cannot silently diverge between two UIs.
        "arxiv_categories": [
            {"code": code, "label": format_arxiv_category(code)}
            for code in ARXIV_CATEGORIES
        ],
    }


def save_settings(
    config_updates: Mapping[str, Any] | None,
    env_updates: Mapping[str, Any] | None,
    clear_env: Iterable[object] | None = None,
) -> dict[str, Any]:
    """Persist a bounded partial update without exposing or dropping secrets."""
    if config_updates is not None and not isinstance(config_updates, Mapping):
        raise ModernWebUIError("配置更新必须是对象。")
    if env_updates is not None and not isinstance(env_updates, Mapping):
        raise ModernWebUIError("环境变量更新必须是对象。")
    current_flat = flat_config()
    incoming_config = config_updates or {}
    unknown = set(incoming_config).difference(_CONFIG_FIELDS)
    if unknown:
        raise ModernWebUIError("包含不支持的配置字段：" + ", ".join(sorted(unknown)))
    for key, value in incoming_config.items():
        _ensure_json_value(value)
        current_flat[key] = value

    # build_config_dict is deliberately the one portable round-trip path used
    # by the existing panel. It preserves validation and normalizes legacy
    # source definitions, backup policies and safe project-relative paths.
    config_args = {key: current_flat[key] for key in _CONFIG_FIELDS if key in current_flat}
    try:
        write_config_json(build_config_dict(**config_args))
        # The next response must reflect a just-saved value (including a
        # changed data/database path), rather than waiting for a filesystem
        # signature check on a stale in-process object.
        _invalidate_runtime_caches()
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(str(exc)) from exc

    current_env = read_env()
    incoming_env = env_updates or {}
    unknown_env = set(incoming_env).difference(WRITABLE_ENV_FIELDS)
    if unknown_env:
        raise ModernWebUIError("包含不支持的环境变量字段。")
    for key, value in incoming_env.items():
        if not isinstance(value, (str, int, float, bool)):
            raise ModernWebUIError("环境变量值必须是文本、数字或开关。")
        text = str(value)
        if len(text) > 12_000 or "\x00" in text:
            raise ModernWebUIError("环境变量值长度无效。")
        # A blank secret field is the safe default: retain the saved key.
        if key in SECRET_ENV_FIELDS and not text:
            continue
        current_env[key] = text.lower() if isinstance(value, bool) else text
    requested_clears = set(clear_env or [])
    if not requested_clears.issubset(SECRET_ENV_FIELDS):
        raise ModernWebUIError("只能清除受管理的密钥字段。")
    for key in requested_clears:
        current_env[key] = ""
    try:
        write_env(current_env)
    except OSError as exc:
        raise ModernWebUIError(f"保存环境变量失败：{exc}") from exc
    return public_settings()


def request_worker_restart() -> None:
    marker = trigger_directory(DEFAULT_DATA_DIR) / "restart_worker.request"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"requested_at={datetime.now().isoformat()}\n", encoding="utf-8"
        )
    except OSError as exc:
        raise ModernWebUIError(f"无法写入重启请求：{exc}") from exc


def _read_lock_pid(path: Path) -> int | None:
    try:
        match = _PID_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(match.group(1)) if match else None


def active_locks(flat: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return every currently held worker lock from both supported data roots.

    Most modes use a fixed name, while trend research deliberately derives a
    parameter-specific ``trend_research_<hash>.lock``.  Enumerating only a
    small static list made an active trend task invisible to the modern panel
    and could offer a conflicting launch button.  The Streamlit panel scans
    the run directory, so keep the same behaviour here.
    """
    data_dir = configured_data_dir(flat)
    results: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for directory in (data_dir / "run", DEFAULT_DATA_DIR / "run"):
        try:
            paths = sorted(directory.glob("*.lock"), key=lambda item: item.name)
        except OSError:
            continue
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            try:
                held = path.exists() and is_lock_held(path)
            except OSError:
                held = path.exists()
            if held:
                results.append({"name": path.name, "pid": _read_lock_pid(path)})
    return results


def _is_container_webui() -> bool:
    """Whether this presentation process is the standalone Docker UI.

    The compatibility panel uses the same distinction: a source checkout can
    remove an abandoned local trigger, while a container must leave that
    request on the shared volume for the worker/watcher to inspect.
    """
    return not (PROJECT_ROOT / "main.py").is_file()


def trigger_queue_state(
    active: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Describe the oldest queued worker request without exposing its data.

    A request waiting behind an active worker is normal, even if it is old.
    It becomes stale only after the watcher has had time to pick it up and no
    worker lock can explain the wait.  This mirrors the Streamlit run manager
    and prevents a stale trigger from looking like a permanently active task.
    """
    queue_dir = trigger_directory(DEFAULT_DATA_DIR)
    try:
        queued = [path for path in queue_dir.glob("*.json") if path.is_file()]
    except OSError:
        queued = []
    oldest_mtime: float | None = None
    for path in queued:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        oldest_mtime = mtime if oldest_mtime is None else min(oldest_mtime, mtime)
    if oldest_mtime is None:
        return {
            "pending": False,
            "stale": False,
            "age_seconds": None,
            "can_clear": False,
            "container_managed": _is_container_webui(),
        }
    age_seconds = max(0, int(time.time() - oldest_mtime))
    locks = list(active) if active is not None else active_locks()
    stale = age_seconds > _TRIGGER_STALE_AFTER_SECONDS and not locks
    return {
        "pending": not stale,
        "stale": stale,
        "age_seconds": age_seconds,
        "can_clear": stale and not _is_container_webui(),
        "container_managed": _is_container_webui(),
    }


def clear_stale_triggers() -> dict[str, int]:
    """Remove only an abandoned local trigger queue after revalidation.

    Docker requests are worker-owned files on a shared volume.  Removing them
    from a WebUI container can race the watcher, so operators are directed to
    inspect/restart the research container there instead.
    """
    if _is_container_webui():
        raise ModernWebUIError("Docker 部署请保留请求并检查或重启研究容器。")
    locks = active_locks()
    state = trigger_queue_state(locks)
    if not state["stale"]:
        raise ModernWebUIError("当前没有可清除的过期请求。")
    queue_dir = trigger_directory(DEFAULT_DATA_DIR)
    removed = 0
    try:
        paths = [path for path in queue_dir.glob("*.json") if path.is_file()]
    except OSError as exc:
        raise ModernWebUIError(f"无法读取任务队列：{exc}") from exc
    for path in paths:
        # Do not race a newly submitted request that appeared between the
        # stale-state check and this controlled cleanup.
        try:
            if time.time() - path.stat().st_mtime <= _TRIGGER_STALE_AFTER_SECONDS:
                continue
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ModernWebUIError(f"清除过期请求失败：{exc}") from exc
    return {"removed": removed}


def _locks_for_kind(locks: Iterable[Mapping[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Filter active lock metadata for one operation page."""
    prefixes = _LOCK_KIND_PREFIXES.get(kind, ())
    rows: list[dict[str, Any]] = []
    for lock in locks:
        name = str(lock.get("name") or "")
        if any(name == prefix or name.startswith(prefix) for prefix in prefixes):
            rows.append(dict(lock))
    return rows


def _lock_kind(name: object) -> str | None:
    """Map a lock filename to the presentation/control task kind."""
    value = str(name or "")
    for kind in _LOCK_KIND_PREFIXES:
        if _locks_for_kind(({"name": value},), kind):
            return kind
    return None


def _mode_kind(mode: object) -> str | None:
    """Map a durable trigger mode to the matching lock/control kind."""
    normalized = str(mode or "").strip().lower()
    if normalized in {"daily", "daily_research", "supplement", "supplement_run"}:
        return "daily"
    if normalized in {"backfill", "backfill_run"}:
        return "past"
    if normalized in {"trend", "trend_research"}:
        return "trend"
    if normalized in HISTORY_MODES:
        return "history"
    return None


def _is_history_lock(lock: Mapping[str, Any]) -> bool:
    """Whether a lock belongs exclusively to idle-time history maintenance."""
    return bool(_locks_for_kind((lock,), "history"))


def _label_for_lock(name: str) -> str:
    if name.startswith("trend_research_"):
        return MODE_LABELS["trend_research"]
    mapping = {
        "daily_research.lock": "daily_research",
        "supplement_run.lock": "supplement_run",
        "backfill_run.lock": "backfill_run",
        "legacy_import.lock": "legacy_import",
        "history_data_repair.lock": "history_data_repair",
        "history_omission_scan.lock": "history_omission_scan",
    }
    return MODE_LABELS.get(mapping.get(name, ""), "正在运行")


def _newest_log_with_prefixes(prefixes: tuple[str, ...]) -> Path | None:
    """Return the newest matching local run log without exposing a path."""
    if not LOGS_DIR.is_dir():
        return None
    try:
        matches = [
            path
            for path in LOGS_DIR.rglob("*.log")
            if path.is_file() and path.name.lower().startswith(prefixes)
        ]
        return max(matches, key=lambda path: path.stat().st_mtime) if matches else None
    except OSError:
        return None


def _read_log_tail_lines(
    path: Path,
    *,
    max_lines: int,
    chunk_size: int = 32 * 1024,
    max_bytes: int = 256 * 1024,
) -> tuple[list[str], bool, int | None]:
    """Read a bounded tail without loading an entire long-running log.

    The status cards poll while work is active, and a full ``read_text`` on a
    multi-megabyte task log turns each small refresh into unnecessary disk I/O
    and allocation.  Reading backwards keeps the final lines available with
    a fixed memory budget.  An exact hidden-line count is only known when the
    whole file fit in that bounded read; callers use a clear generic marker
    otherwise.
    """
    visible_limit = max(1, int(max_lines))
    block_size = max(1, int(chunk_size))
    byte_limit = max(block_size, int(max_bytes))
    chunks: list[bytes] = []
    newline_count = 0
    bytes_read = 0
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        while (
            position > 0
            and newline_count <= visible_limit
            and bytes_read < byte_limit
        ):
            size = min(block_size, position, byte_limit - bytes_read)
            if size <= 0:
                break
            position -= size
            handle.seek(position)
            block = handle.read(size)
            chunks.append(block)
            bytes_read += len(block)
            newline_count += block.count(b"\n")
    lines = b"".join(reversed(chunks)).decode("utf-8", errors="replace").splitlines()
    truncated = position > 0 or len(lines) > visible_limit
    skipped = len(lines) - visible_limit if position == 0 and len(lines) > visible_limit else None
    return lines[-visible_limit:], truncated, skipped


def _live_log_tail(locks: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Build the bounded live-log payload used by a running status card."""
    selected: Path | None = None
    for lock in locks:
        name = str(lock.get("name") or "")
        prefixes = _LIVE_LOG_PREFIXES.get(name)
        if prefixes is None and name.startswith("trend_research_"):
            prefixes = ("trend_",)
        if prefixes:
            selected = _newest_log_with_prefixes(prefixes)
            if selected is not None:
                break
    if selected is None:
        return None
    try:
        relative = selected.relative_to(LOGS_DIR).as_posix()
        max_lines = 15
        visible, truncated, skipped = _read_log_tail_lines(
            selected, max_lines=max_lines
        )
    except (OSError, ValueError):
        return None
    # Status cards refresh frequently while a task is active.  Fifteen recent
    # lines give useful progress and failure context without turning the
    # dashboard into a second full log viewer.
    if truncated:
        marker = (
            f"… 已隐藏较早的 {skipped} 行 …"
            if skipped is not None
            else f"… 已隐藏较早的内容，仅显示最后 {max_lines} 行 …"
        )
        visible.insert(0, marker)
    return {
        "name": relative,
        "content": "\n".join(visible),
        "truncated": truncated,
    }


def _read_status_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("mode") not in SUPPORTED_MODES:
        return None
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        return None
    return {
        "request_id": request_id,
        "mode": str(raw["mode"]),
        "created_at": str(raw.get("created_at") or ""),
        "started_at": str(raw.get("started_at") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
        "state": str(raw.get("state") or "unknown"),
        "issue": sanitize_task_error_summary(raw.get("error_summary") or raw.get("error")),
        "args": raw.get("args") if isinstance(raw.get("args"), dict) else {},
        "retry_of": str(raw.get("retry_of") or "").strip(),
    }


def task_records(modes: Iterable[str] | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
    """Combine durable queue entries and worker receipts into one safe list."""
    allowed = set(modes or SUPPORTED_MODES)
    allowed.intersection_update(SUPPORTED_MODES)
    queue_dir = trigger_directory(DEFAULT_DATA_DIR)
    records: dict[str, dict[str, Any]] = {}
    try:
        for path in queue_dir.glob("*.json"):
            payload = read_trigger_payload(path)
            if payload.get("mode") not in allowed:
                continue
            request_id = str(payload["request_id"])
            records[request_id] = {
                "request_id": request_id,
                "mode": str(payload["mode"]),
                "created_at": str(payload.get("created_at") or ""),
                "started_at": "",
                "updated_at": "",
                "state": "queued",
                "issue": "",
                "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                "retry_of": str(payload.get("retry_of") or "").strip(),
            }
        for path in queue_dir.glob("*.running"):
            payload = read_trigger_payload(path)
            if payload.get("mode") not in allowed:
                continue
            request_id = str(payload["request_id"])
            records[request_id] = {
                "request_id": request_id,
                "mode": str(payload["mode"]),
                "created_at": str(payload.get("created_at") or ""),
                "started_at": "",
                "updated_at": "",
                "state": "starting",
                "issue": "",
                "args": payload.get("args") if isinstance(payload.get("args"), dict) else {},
                "retry_of": str(payload.get("retry_of") or "").strip(),
            }
    except (OSError, ValueError):
        pass
    status_dir = trigger_status_directory(DEFAULT_DATA_DIR)
    try:
        paths = sorted(status_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        paths = []
    for path in paths:
        record = _read_status_file(path)
        if record is None or record["mode"] not in allowed:
            continue
        # A live queue entry has precedence over an older receipt with the
        # same ID. Once the watcher has written its ``running`` receipt,
        # however, keep that stronger state while the ``.running`` hand-off
        # file remains present for the lifetime of the child process.
        existing = records.get(record["request_id"])
        if (
            existing is None
            or existing["state"] == "queued"
            or (existing["state"] == "starting" and record["state"] == "running")
        ):
            records[record["request_id"]] = record
    rows = list(records.values())
    rows.sort(key=lambda item: (item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return rows[: max(1, min(int(limit), 500))]


def _latest_record(modes: Iterable[str]) -> dict[str, Any] | None:
    rows = task_records(modes, limit=100)
    return rows[0] if rows else None


def run_status(kind: str = "daily") -> dict[str, Any]:
    """Return the durable state for a modern run page without starting work."""
    flat = flat_config()
    locks = active_locks(flat)
    trigger = trigger_queue_state(locks)
    mode_map = {
        "daily": {"daily_research", "supplement_run"},
        "past": {"backfill_run"},
        "trend": {"trend_research"},
        "history": set(HISTORY_MODES),
    }
    wanted = mode_map.get(kind, mode_map["daily"])
    records = task_records(wanted)
    live_records = [row for row in records if row["state"] in {"queued", "starting", "running"}]
    # The watcher accepts one trigger at a time.  Daily and trend launchers
    # therefore follow the Streamlit guard and wait until any just-submitted
    # request is handed to a worker; past-date jobs remain queueable behind a
    # running job by design.
    all_live_records = [
        row
        for row in task_records(SUPPORTED_MODES)
        if row["state"] in {"queued", "starting", "running"}
    ]
    relevant_locks = _locks_for_kind(locks, kind)
    # The Streamlit daily landing page is an operational overview.  It keeps
    # idle-time history work out of sight, but it does show another active
    # research task (for example a past-date run) when that task prevents a
    # new daily run from starting.  Without this broader visible set the
    # modern page could misleadingly say “空闲” while its start button was
    # disabled.
    visible_locks = (
        [lock for lock in locks if not _is_history_lock(lock)]
        if kind == "daily"
        else relevant_locks
    )
    visible_live_records = (
        [
            row
            for row in all_live_records
            if row["mode"] not in HISTORY_MODES
        ]
        if kind == "daily"
        else live_records
    )
    store = open_store(flat)
    progress = None
    queue: dict[str, Any] = {}
    backfill: dict[str, Any] = {}
    if store is not None:
        try:
            progress = store.active_run_progress()
            queue = store.count_pending_papers()
            backfill = store.backfill_queue_summary()
        except Exception:
            progress = None

    progress_kind = str((progress or {}).get("run_kind") or "")
    progress_matches = (
        # Match Streamlit's daily operational card: an active ordinary run
        # remains visible there even when it is a queued past-date or trend
        # task.  History maintenance stays on its dedicated page.
        (kind == "daily" and progress_kind not in HISTORY_MODES and bool(progress_kind))
        or (kind == "past" and progress_kind in {"backfill", "backfill_run"})
        or (kind == "history" and progress_kind in HISTORY_MODES)
        or (kind == "trend" and progress_kind in {"trend", "trend_research"})
    )
    # The stop control belongs to the one task represented by this status
    # card.  A daily overview can intentionally show a running backfill or
    # trend job, so do not assume that the page kind itself owns the process.
    stop_kind: str | None = None
    if progress_matches:
        stop_kind = _mode_kind(progress_kind)
        task = {
            "state": "running",
            "label": MODE_LABELS.get(progress_kind, "正在运行"),
            "phase": PHASE_LABELS.get(str(progress.get("phase") or ""), str(progress.get("phase") or "处理中")),
            "detail": sanitize_task_error_summary(progress.get("detail"), max_chars=260),
            "current": progress.get("current"),
            "total": progress.get("total"),
            "started_at": progress.get("started_at"),
            "counters": {
                "registered": int(progress.get("registered") or 0),
                "scored": int(progress.get("scored") or 0),
                "analyzed": int(progress.get("analyzed") or 0),
                "completed": int(progress.get("completed") or 0),
                "failed": int(progress.get("failed") or 0),
            },
        }
    elif trigger["stale"]:
        task = {
            "state": "stale",
            "label": "等待工作进程的请求已过期",
            "phase": "请检查研究容器或清除本地过期请求",
            "detail": "",
            "current": None,
            "total": None,
            "started_at": "",
        }
    elif visible_live_records:
        latest = visible_live_records[0]
        stop_kind = _mode_kind(latest["mode"])
        task = {
            "state": latest["state"],
            "label": MODE_LABELS.get(latest["mode"], latest["mode"]),
            "phase": "等待工作进程接手" if latest["state"] != "running" else "正在运行",
            "detail": latest.get("issue") or "",
            "current": None,
            "total": None,
            "started_at": latest.get("created_at") or "",
        }
    elif visible_locks:
        primary = visible_locks[0]
        stop_kind = _lock_kind(primary.get("name"))
        task = {
            "state": "running",
            "label": _label_for_lock(str(primary.get("name") or "")),
            "phase": "正在运行，等待进度写入",
            "detail": "",
            "current": None,
            "total": None,
            "started_at": "",
        }
    else:
        latest = _latest_record(wanted)
        if latest and latest["state"] in {"failed", "rejected", "interrupted", "skipped_busy"}:
            task = {
                "state": latest["state"],
                "label": "上次任务未完成",
                "phase": "请查看问题摘要后重试",
                "detail": latest.get("issue") or "",
                "current": None,
                "total": None,
                "started_at": latest.get("updated_at") or "",
            }
        else:
            task = {
                "state": "idle",
                "label": "空闲",
                "phase": "可以开始任务",
                "detail": "",
                "current": None,
                "total": None,
                "started_at": "",
            }
    active = bool(
        (visible_live_records and not trigger["stale"])
        or progress_matches
        or visible_locks
    )
    stop_locks = _locks_for_kind(locks, stop_kind) if stop_kind else []
    can_stop = bool(
        active
        and stop_kind in _STOPPABLE_TASK_KINDS
        and any(isinstance(lock.get("pid"), int) for lock in stop_locks)
    )
    if kind == "past":
        # A date range is a durable queue request.  As in the Streamlit
        # panel, it may be placed behind an already-running worker task; only
        # the short trigger hand-off window is held back to avoid writing a
        # confusing burst of requests before the watcher has claimed one.
        can_start = not trigger["stale"] and not any(
            row["state"] in {"queued", "starting"} for row in all_live_records
        )
    elif kind == "daily":
        can_start = not bool(trigger["stale"] or locks or all_live_records)
    elif kind == "trend":
        # Trend analysis uses its own parameterized lock and only shares the
        # legacy-import activity gate with the daily workflow.  It therefore
        # remains valid to queue a trend request while an ordinary daily or
        # past-date run is executing (the trigger watcher keeps FIFO order).
        # Match the Streamlit guard: hold the button only during a short
        # trigger hand-off, or while a trend job itself is already active.
        trigger_handoff_pending = any(
            row["state"] in {"queued", "starting"} for row in all_live_records
        )
        can_start = not bool(
            trigger["stale"] or trigger_handoff_pending or relevant_locks or live_records
        )
    else:
        # History maintenance is intentionally allowed to enter the durable
        # idle-time queue behind normal research, but duplicate history work
        # remains disabled until its preceding request has finished.
        can_start = not bool(live_records)
    # The compatibility panel intentionally keeps history-maintenance status
    # out of the normal daily/previous-date cards.  Its locks still take part
    # in launch safety above, but operators inspect their details only from
    # the dedicated History Maintenance page.
    display_locks = locks if kind == "history" else [
        lock for lock in locks if not _is_history_lock(lock)
    ]
    return {
        "task": task,
        "is_active": active,
        "can_start": can_start,
        "stop_kind": stop_kind,
        "can_stop": can_stop,
        "queue": {
            "pending": int(queue.get("total") or 0),
            "retry": int(queue.get("failed_retry") or 0),
        },
        "backfill": backfill,
        "active_locks": display_locks,
        "relevant_locks": relevant_locks,
        "has_relevant_lock": bool(relevant_locks),
        "trigger": trigger,
        "live_log": _live_log_tail(visible_locks) if active else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def enqueue_task(
    mode: str,
    args: Mapping[str, Any] | None = None,
    *,
    retry_of: str | None = None,
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ModernWebUIError("不支持的任务类型。")
    safe_args = dict(args or {})
    _ensure_json_value(safe_args)
    try:
        path = enqueue_trigger(DEFAULT_DATA_DIR, mode, retry_of=retry_of, **safe_args)
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(str(exc)) from exc
    return {"queued": True, "request_id": path.stem.rsplit("_", 1)[-1], "mode": mode}


def stop_active_tasks(kind: str | None = None) -> list[int]:
    """Request a graceful stop for one displayed task scope.

    A modern status card may be rendered while an unrelated task is also
    active (most notably a trend task beside a daily/backfill operation).
    Scope the request to the card's matched lock instead of broadcasting a
    stop request to every Worker PID.  ``None`` preserves the authenticated
    legacy API behaviour for callers that deliberately request all tasks.
    """
    normalized_kind = str(kind or "").strip().lower() or None
    if normalized_kind is not None and normalized_kind not in _STOPPABLE_TASK_KINDS:
        raise ModernWebUIError("不支持该任务的停止操作。")
    records = active_locks()
    selected = (
        _locks_for_kind(records, normalized_kind)
        if normalized_kind is not None
        else records
    )
    pids = [row["pid"] for row in selected if isinstance(row.get("pid"), int)]
    if not pids:
        raise ModernWebUIError("没有可停止的 WebUI 任务。")
    for pid in pids:
        # Triggered children always monitor the Worker-owned shared queue
        # below DEFAULT_DATA_DIR.  A custom database/data path can host run
        # locks, but it does not move the trigger watcher's stop channel.
        request_stop(DEFAULT_DATA_DIR, pid)
    return pids


def history_status() -> dict[str, Any]:
    """Return the focused, durable status model for history maintenance.

    History work deliberately runs through the ordinary worker trigger queue,
    but it is not part of the daily-run panel.  This response mirrors the
    Streamlit history panel: task receipts remain compact and safe, while a
    matching SQLite heartbeat supplies meaningful phase progress for the one
    task currently being processed.
    """
    flat = flat_config()
    schedule = _history_maintenance_schedule(flat)
    store = open_store(flat)
    summary = None
    if store is not None:
        try:
            raw = store.get_app_state("legacy_import_summary")
            parsed = json.loads(raw) if raw else None
            summary = parsed if isinstance(parsed, dict) else None
        except Exception:
            summary = None
    active_progress: Mapping[str, Any] | None = None
    if store is not None:
        try:
            candidate = store.active_run_progress()
            active_progress = candidate if isinstance(candidate, Mapping) else None
        except Exception:
            active_progress = None
    all_records = task_records(HISTORY_MODES)
    retried_request_ids = {
        str(row.get("retry_of") or "").strip()
        for row in all_records
        if str(row.get("retry_of") or "").strip()
    }
    records = [
        _history_task_row(row, active_progress, schedule)
        for row in all_records
        if row["state"] != "succeeded"
        and str(row.get("request_id") or "") not in retried_request_ids
    ]
    return {
        "status": run_status("history"),
        "schedule": {
            "run_mode": schedule[0],
            "time_window_start": schedule[1],
            "time_window_end": schedule[2],
        },
        "last_import": summary,
        "tasks": records,
    }


def _history_maintenance_schedule(flat: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return saved scheduling values for history-page status wording."""
    return resolve_history_maintenance_schedule(
        flat.get("history_maintenance_run_mode", DEFAULT_HISTORY_MAINTENANCE_RUN_MODE),
        flat.get(
            "history_maintenance_time_window_start",
            DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START,
        ),
        flat.get(
            "history_maintenance_time_window_end",
            DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END,
        ),
    )


def _history_task_progress(
    record: Mapping[str, Any],
    progress: Mapping[str, Any] | None,
    schedule: tuple[str, str, str],
) -> str:
    """Turn a receipt plus optional SQLite heartbeat into concise task text."""
    state = str(record.get("state") or "")
    mode = str(record.get("mode") or "")
    if state == "queued":
        if schedule[0] == "time_window":
            return f"等待 {schedule[1]}–{schedule[2]} 时段及后端空闲"
        return "等待后端空闲"
    if state == "starting":
        return "工作进程正在接手任务"
    if state != "running":
        return ""
    if not isinstance(progress, Mapping) or progress.get("run_kind") != HISTORY_RUN_KINDS.get(mode):
        return "等待系统空闲后继续运行"

    phase = PHASE_LABELS.get(
        str(progress.get("phase") or ""), str(progress.get("phase") or "处理中")
    )
    detail = sanitize_task_error_summary(progress.get("detail"), max_chars=160)
    parts = [phase]
    if detail:
        parts.append(detail)
    current = progress.get("current")
    total = progress.get("total")
    if (
        isinstance(current, int)
        and not isinstance(current, bool)
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
    ):
        parts.append(f"{max(0, current)}/{total}")
    return " · ".join(parts)


def _history_task_row(
    record: Mapping[str, Any],
    progress: Mapping[str, Any] | None,
    schedule: tuple[str, str, str],
) -> dict[str, Any]:
    """Provide the UI-only columns used by the history task table."""
    row = dict(record)
    state = str(row.get("state") or "unknown")
    row["label"] = HISTORY_TASK_LABELS.get(str(row.get("mode") or ""), str(row.get("mode") or "未知任务"))
    row["started_at"] = str(row.get("started_at") or "")
    row["completed_at"] = (
        str(row.get("updated_at") or "") if state not in _LIVE_TASK_STATES else ""
    )
    row["progress"] = _history_task_progress(row, progress, schedule)
    row["retryable"] = state in _RETRYABLE_TASK_STATES
    return row


def retry_history_task(request_id: str) -> dict[str, Any]:
    record = next((item for item in task_records(HISTORY_MODES) if item["request_id"] == request_id), None)
    if record is None:
        raise ModernWebUIError("未找到可重试的历史维护任务。")
    if record["state"] not in {"failed", "rejected", "interrupted", "skipped_busy"}:
        raise ModernWebUIError("该历史维护任务当前不能重试。")
    return enqueue_task(
        record["mode"], record.get("args") or {}, retry_of=request_id
    )


def _source_list(store: DailyResearchStore) -> list[str]:
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source FROM daily_papers WHERE source != '' ORDER BY source"
            ).fetchall()
        return [str(row["source"]) for row in rows if row["source"]]
    except Exception:
        return []


def paper_search(filters: Mapping[str, Any]) -> dict[str, Any]:
    store = open_store()
    if store is None:
        return {"available": False, "sources": [], "total": 0, "items": []}
    query = str(filters.get("query") or "")[:500]
    source = str(filters.get("source") or "").strip().lower() or None
    completed_from = str(filters.get("completed_from") or "").strip() or None
    completed_to = str(filters.get("completed_to") or "").strip() or None
    try:
        min_score_raw = filters.get("min_score")
        min_score = float(min_score_raw) if min_score_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise ModernWebUIError("最低分数必须是数字。")
    try:
        limit = max(5, min(int(filters.get("limit", 10)), 100))
        offset = max(0, int(filters.get("offset", 0)))
        result = store.search_papers(
            query=query,
            source=source,
            liked_only=_coerce_bool(filters.get("liked_only")),
            min_score=min_score,
            completed_from=completed_from,
            completed_to=completed_to,
            limit=limit,
            offset=offset,
        )
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(f"检索失败：{exc}") from exc
    return {"available": True, "sources": _source_list(store), **result}


def preferences_summary() -> dict[str, Any]:
    store = open_store()
    if store is None:
        return {"available": False, "counts": {"like": 0, "dislike": 0}, "liked": [], "authors": [], "keywords": []}
    try:
        aggregate = store.aggregate_liked_preferences()
        liked = store.list_preferences(preference="like", limit=500)
        urls = store.liked_paper_urls()
        for row in liked:
            row["url"] = urls.get(
                (str(row.get("source") or ""), str(row.get("paper_id") or ""))
            )
        return {
            "available": True,
            "counts": store.get_preference_counts(),
            "liked": liked,
            "authors": aggregate.get("authors") or [],
            "keywords": store.aggregate_liked_keywords(limit=500),
        }
    except Exception as exc:
        raise ModernWebUIError(f"读取收藏数据失败：{exc}") from exc


def collect_qualified_favorites() -> dict[str, Any]:
    """Add all persisted qualifying papers to 收藏 without changing reader marks."""
    store = open_store()
    if store is None:
        raise ModernWebUIError("SQLite 数据库尚未创建。")
    try:
        return {"ok": True, **store.collect_qualified_favorites()}
    except Exception as exc:
        raise ModernWebUIError(f"收藏通过论文失败：{exc}") from exc


def set_preference(payload: Mapping[str, Any]) -> dict[str, Any]:
    # Match the Streamlit report viewer: preferences are usable for a saved
    # daily report even before a worker run has created the history database.
    store = open_store(create=True)
    if store is None:
        raise ModernWebUIError("SQLite 数据库尚不可用。")
    source = str(payload.get("source") or "").strip().lower()[:100]
    paper_id = str(payload.get("paper_id") or "").strip()[:500]
    title = str(payload.get("title") or paper_id).strip()[:4_000]
    preference = str(payload.get("preference") or "none")
    if not source or not paper_id:
        raise ModernWebUIError("论文来源和标识不能为空。")
    authors = payload.get("authors") if isinstance(payload.get("authors"), list) else []
    categories = payload.get("categories") if isinstance(payload.get("categories"), list) else []
    try:
        store.set_paper_preference(
            source,
            paper_id,
            preference=preference,
            title=title,
            canonical_id=str(payload.get("canonical_id") or "")[:500] or None,
            version=int(payload["version"]) if payload.get("version") not in (None, "") else None,
            authors=[str(item)[:500] for item in authors[:100]],
            categories=[str(item)[:100] for item in categories[:100]],
        )
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(str(exc)) from exc
    return {"ok": True, "preference": preference}


def learned_preference_terms() -> dict[str, list[dict[str, Any]]]:
    store = open_store()
    if store is None:
        return {"keywords": [], "authors": []}
    rows = store.get_learned_preference_terms(limit=500)
    return {
        "keywords": [row for row in rows if row.get("term_type") == "keyword"],
        "authors": [row for row in rows if row.get("term_type") == "author"],
    }


def extracted_keywords() -> list[dict[str, Any]]:
    path = configured_data_dir() / "keywords" / "keywords_cache.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    keywords = raw.get("keywords") if isinstance(raw, dict) else None
    if not isinstance(keywords, dict):
        return []
    rows = []
    for name, weight in keywords.items():
        if not isinstance(name, str) or not isinstance(weight, (int, float)):
            continue
        rows.append({"keyword": name, "weight": float(weight)})
    return sorted(rows, key=lambda item: (-item["weight"], item["keyword"]))


def _read_trend_prompt_templates() -> dict[str, str]:
    """Read the small user-owned template library without failing the UI."""
    try:
        raw = json.loads(TREND_PROMPT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        name.strip(): text.strip()
        for name, text in raw.items()
        if isinstance(name, str)
        and isinstance(text, str)
        and name.strip()
        and len(name.strip()) <= 120
        and len(text.strip()) <= 8_000
    }


def list_trend_prompt_templates() -> list[dict[str, Any]]:
    """Return built-ins followed by saved templates and built-in overrides."""
    stored = _read_trend_prompt_templates()
    rows: list[dict[str, Any]] = []
    for name, default_text in BUILTIN_TREND_PROMPT_TEMPLATES.items():
        overridden = name in stored
        rows.append(
            {
                "name": name,
                "text": stored.get(name, default_text),
                "builtin": True,
                "overridden": overridden,
                "default": name == DEFAULT_TREND_PROMPT_TEMPLATE_NAME,
            }
        )
    rows.extend(
        {
            "name": name,
            "text": text,
            "builtin": False,
            "overridden": False,
            "default": False,
        }
        for name, text in sorted(stored.items(), key=lambda item: item[0].casefold())
        if name not in BUILTIN_TREND_PROMPT_TEMPLATES
    )
    return rows


def _write_trend_prompt_templates(templates: Mapping[str, str]) -> None:
    path = TREND_PROMPT_TEMPLATES_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(templates), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ModernWebUIError(f"保存趋势提示词模板失败：{exc}") from exc


def save_trend_prompt_template(name: object, text: object) -> list[dict[str, Any]]:
    safe_name = str(name or "").strip()
    safe_text = str(text or "").strip()
    if not safe_name:
        raise ModernWebUIError("模板名称不能为空。")
    if len(safe_name) > 120 or any(char in safe_name for char in "\r\n\x00"):
        raise ModernWebUIError("模板名称长度或格式无效。")
    if not safe_text:
        raise ModernWebUIError("模板内容不能为空。")
    if len(safe_text) > 8_000 or "\x00" in safe_text:
        raise ModernWebUIError("模板内容长度或格式无效。")
    templates = _read_trend_prompt_templates()
    custom_templates = [name for name in templates if name not in BUILTIN_TREND_PROMPT_TEMPLATES]
    if (
        safe_name not in templates
        and safe_name not in BUILTIN_TREND_PROMPT_TEMPLATES
        and len(custom_templates) >= 50
    ):
        raise ModernWebUIError("最多保存 50 个趋势提示词模板。")
    templates[safe_name] = safe_text
    _write_trend_prompt_templates(templates)
    return list_trend_prompt_templates()


def delete_trend_prompt_template(name: object) -> list[dict[str, Any]]:
    safe_name = str(name or "").strip()
    templates = _read_trend_prompt_templates()
    if safe_name in BUILTIN_TREND_PROMPT_TEMPLATES:
        if safe_name not in templates:
            raise ModernWebUIError("该内置模板尚未修改，无需恢复。")
        del templates[safe_name]
        _write_trend_prompt_templates(templates)
        return list_trend_prompt_templates()
    if safe_name not in templates:
        raise ModernWebUIError("未找到该趋势提示词模板。")
    del templates[safe_name]
    _write_trend_prompt_templates(templates)
    return list_trend_prompt_templates()


def diagnostics(days: int | None) -> dict[str, Any]:
    if days is not None and days not in {3, 7, 14, 30}:
        raise ModernWebUIError("诊断时间范围无效。")
    store = open_store()
    if store is None:
        return {"available": False, "llm": [], "sources": [], "runs": []}
    try:
        try:
            source_labels = source_display_names(
                flat_config().get("extra_source_definitions", [])
            )
        except (TypeError, ValueError):
            # A hand-edited legacy source declaration must not make the
            # read-only diagnostics page unavailable.  This matches the
            # compatibility-panel fallback while preserving the raw source
            # receipt for operators to inspect.
            source_labels = source_display_names()
        sources = store.get_source_health_for_days(days)
        source_rows = []
        for source, value in sources.items():
            source_rows.append(
                {
                    "source": source,
                    "name": source_labels.get(source, source),
                    "last_status": value.get("last_status"),
                    # Source receipts use scan-specific names in the shared
                    # SQLite store.  Expose a stable generic API field to the
                    # modern UI rather than silently rendering an empty time.
                    "last_event_at": value.get("last_scan_at"),
                    "last_task_kind": value.get("last_task_kind"),
                    "events": value.get("scans_in_window", 0),
                    "succeeded": value.get("succeeded_in_window", 0),
                    "success_rate": value.get("success_rate"),
                    "last_new_candidates": value.get("last_new_candidates"),
                    "last_error": value.get("last_error"),
                    "last_error_at": value.get("last_error_at"),
                }
            )
        source_rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
        return {
            "available": True,
            "llm": store.get_llm_health_by_model(days),
            "sources": source_rows,
            "runs": store.get_recent_operational_runs(limit=None, days=days),
        }
    except Exception as exc:
        raise ModernWebUIError(f"读取运行诊断失败：{exc}") from exc


def _analytics_window(
    range_key: object,
    date_from: object = None,
    date_to: object = None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str, str]:
    """Resolve an analytics picker value into one explicit local-time window."""

    current = (now or datetime.now()).replace(microsecond=0)
    key = str(range_key or "30d").strip().lower()
    if key == "24h":
        return current - timedelta(hours=24), current, "hour", key
    if key == "today":
        return current.replace(hour=0, minute=0, second=0), current, "hour", key
    if key in {"3d", "7d", "14d", "30d"}:
        days = int(key[:-1])
        start = current.replace(hour=0, minute=0, second=0) - timedelta(days=days - 1)
        return start, current, "day", key
    if key != "custom":
        raise ModernWebUIError("用量统计时间范围无效。")
    try:
        start_date = datetime.strptime(str(date_from or ""), "%Y-%m-%d")
        end_date = datetime.strptime(str(date_to or ""), "%Y-%m-%d")
    except ValueError as exc:
        raise ModernWebUIError("自定义时间段需要有效的开始和结束日期。") from exc
    if end_date < start_date:
        raise ModernWebUIError("自定义时间段的结束日期不能早于开始日期。")
    # A date picker describes complete local dates, so an end date is
    # inclusive and the SQL range endpoint is the following midnight.
    end = end_date + timedelta(days=1)
    bucket = "hour" if end - start_date <= timedelta(days=1) else "day"
    return start_date, end, bucket, key


def analytics(
    range_key: object = "30d", date_from: object = None, date_to: object = None
) -> dict[str, Any]:
    start_at, end_at, bucket, normalized_key = _analytics_window(
        range_key, date_from, date_to
    )
    store = open_store()
    if store is None:
        return {
            "available": False,
            "series": [],
            "models": [],
            "summary": {"prompt": 0, "completion": 0, "total": 0, "runs": 0},
            "window": {
                "range": normalized_key,
                "start": start_at.isoformat(),
                "end": end_at.isoformat(),
                "bucket": bucket,
            },
            "heatmap_daily": [],
        }
    try:
        return {
            "available": True,
            "series": store.get_token_usage_series(
                start_at=start_at, end_at=end_at, bucket=bucket
            ),
            "models": store.get_token_usage_by_model_range(
                start_at=start_at, end_at=end_at
            ),
            "summary": store.get_token_usage_summary(
                start_at=start_at, end_at=end_at
            ),
            "window": {
                "range": normalized_key,
                "start": start_at.isoformat(),
                "end": end_at.isoformat(),
                "bucket": bucket,
            },
            # The Streamlit dashboard always renders a one-year activity
            # heatmap independently of the selected trend range.
            "heatmap_daily": store.get_daily_token_totals(days=365),
        }
    except Exception as exc:
        raise ModernWebUIError(f"读取用量统计失败：{exc}") from exc


def _report_sort_key(path: Path, modified_at: float | None = None) -> tuple[int, int, str]:
    stem = path.stem
    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})(?:_(\d+))?", stem)
    if match:
        micro = (match.group(5) or "").ljust(6, "0")
        digits = re.sub(r"\D", "", match.group(1) + match.group(2) + match.group(3) + match.group(4))
        return (1, int(digits + micro), path.name)
    if modified_at is not None:
        return (0, int(modified_at), path.name)
    try:
        return (0, int(path.stat().st_mtime), path.name)
    except OSError:
        return (0, 0, path.name)


def _report_token(path: Path, root: Path) -> str:
    # ``list_reports`` only hands us paths found below ``root``.  Avoid a
    # costly pair of filesystem resolves for every report; this list is read
    # frequently on NAS/WSL bind mounts.  The public token resolver retains
    # its strict resolved-path containment check before serving a file.
    try:
        relative = path.relative_to(root).as_posix().encode("utf-8")
    except ValueError:
        relative = path.resolve().relative_to(root.resolve()).as_posix().encode("utf-8")
    return base64.urlsafe_b64encode(relative).decode("ascii").rstrip("=")


def _report_path(token: str, root: Path) -> Path:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", token):
        raise ModernWebUIError("报告标识无效。")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ModernWebUIError("报告标识无效。") from None
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ModernWebUIError("报告路径无效。") from exc
    if candidate.suffix.lower() != ".html" or not candidate.is_file():
        raise ModernWebUIError("报告文件不存在。")
    return candidate


def list_reports(show_non_arxiv: bool = False) -> dict[str, list[dict[str, Any]]]:
    root = configured_reports_dir()
    groups: dict[str, list[dict[str, Any]]] = {"daily": [], "trend": [], "keyword_trend": []}
    if not root.is_dir():
        return groups
    # Loading config.json (JSON5) once is significant on small NAS devices;
    # source labels are shared by every report row in this response.
    labels = _report_source_labels()
    daily_root = root / "daily_research" / "html"
    if daily_root.is_dir():
        for path in daily_root.rglob("*.html"):
            if not path.is_file():
                continue
            relative = path.relative_to(daily_root)
            source = relative.parts[0].lower() if len(relative.parts) > 1 else (re.match(r"(.+?)_Report_", path.stem, re.I).group(1).lower() if re.match(r"(.+?)_Report_", path.stem, re.I) else "unknown")
            if not show_non_arxiv and source != "arxiv":
                continue
            groups["daily"].append(_report_row(path, root, "daily", source, labels=labels))
    trend_root = root / "trend_research" / "html"
    if trend_root.is_dir():
        for path in trend_root.rglob("*.html"):
            if path.is_file():
                relative = path.relative_to(trend_root)
                source = relative.parts[0] if len(relative.parts) > 1 else "trend"
                groups["trend"].append(_report_row(path, root, "trend", source, labels=labels))
    keyword_root = root / "keyword_trend" / "html"
    if keyword_root.is_dir():
        for path in keyword_root.glob("*.html"):
            if path.is_file():
                groups["keyword_trend"].append(
                    _report_row(path, root, "keyword_trend", "keyword_trend", labels=labels)
                )
    for name, values in groups.items():
        values.sort(key=lambda item: item["sort_key"], reverse=True)
        _disambiguate_report_labels(values)
        for row in values:
            row.pop("sort_key", None)
    return groups


def _report_row(
    path: Path,
    root: Path,
    report_type: str,
    source: str,
    *,
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        size = stat.st_size
        modified_at = stat.st_mtime
    except OSError:
        mtime, size, modified_at = "", 0, None
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", path.stem)
    source_key = str(source or "unknown").strip().lower() or "unknown"
    labels = labels or _report_source_labels()
    return {
        "id": _report_token(path, root),
        "name": path.name,
        "label": _report_label(path, report_type),
        "source": source_key,
        "source_label": labels.get(source_key, source),
        "type": report_type,
        "date": date_match.group(0) if date_match else "",
        "modified_at": mtime,
        "size_bytes": size,
        "metadata": _trend_report_metadata(path) if report_type == "trend" else None,
        "sort_key": _report_sort_key(path, modified_at),
    }


def _report_source_labels() -> dict[str, str]:
    """Use the same configured source names as Streamlit's report browser."""
    try:
        definitions = flat_config().get("extra_source_definitions", [])
        return source_display_names(definitions)
    except (TypeError, ValueError):
        return source_display_names()


def _report_label(path: Path, report_type: str) -> str:
    """Format report labels exactly like the Streamlit select boxes."""
    stem = path.stem
    if report_type == "daily":
        match = re.search(
            r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})(?:_\d+)?$", stem
        )
        if match:
            return f"{match.group(1)}  {match.group(2).replace('-', ':')}"
    elif report_type == "trend":
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", stem)
        if match:
            return f"{match.group(1)} → {match.group(2)}"
    elif report_type == "keyword_trend":
        match = re.search(r"(\d{4}-\d{2}-\d{2})$", stem)
        if match:
            return match.group(1)
    return stem


def _disambiguate_report_labels(rows: list[dict[str, Any]]) -> None:
    """Keep same-source select-box labels unique without exposing noise.

    Daily report filenames keep a microsecond suffix so supplement and normal
    runs never overwrite each other.  The friendly label hides it until two
    reports would otherwise become indistinguishable, exactly as the
    Streamlit browser does.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (str(row.get("type") or ""), str(row.get("source") or ""), str(row.get("label") or ""))
        counts[key] = counts.get(key, 0) + 1

    used: set[tuple[str, str, str]] = set()
    for row in rows:
        label = str(row.get("label") or "")
        key = (str(row.get("type") or ""), str(row.get("source") or ""), label)
        if counts.get(key, 0) <= 1:
            used.add(key)
            continue
        micro = re.search(r"_(\d+)$", str(row.get("name") or "").rsplit(".", 1)[0])
        suffix = f".{micro.group(1)}" if micro else " · duplicate"
        candidate = f"{label}{suffix}"
        duplicate_number = 2
        while (key[0], key[1], candidate) in used:
            candidate = f"{label}{suffix} · {duplicate_number}"
            duplicate_number += 1
        row["label"] = candidate
        used.add((key[0], key[1], candidate))


def _trend_report_metadata(path: Path) -> dict[str, Any] | None:
    """Load the optional trend metadata shown by the Streamlit expander."""
    metadata_path = path.parent.parent.parent / "markdown" / path.parent.name / f"{path.stem}_metadata.json"
    try:
        value = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {
        key: value[key]
        for key in ("keyword", "date_from", "date_to", "total_papers")
        if key in value and isinstance(value[key], (str, int, float))
    }


def _daily_report_source(path: Path, root: Path) -> str:
    """Recover the source used by a daily-report card.

    New reports may be nested below a source directory while v3/v4 arXiv
    reports sit directly in ``daily_research/html``.  Preference records must
    use the same source key as the SQLite delivery ledger, rather than the
    literal ``html`` directory name of an older report.
    """
    daily_root = root / "daily_research" / "html"
    try:
        relative = path.resolve().relative_to(daily_root.resolve())
    except ValueError:
        return "arxiv"
    if len(relative.parts) > 1:
        return relative.parts[0].strip().lower() or "arxiv"
    match = re.match(r"(.+?)_Report_", path.stem, re.IGNORECASE)
    return (match.group(1).strip().lower() if match else "arxiv") or "arxiv"


def report_file(token: str) -> tuple[Path, str]:
    root = configured_reports_dir()
    path = _report_path(token, root)
    return path, mimetypes.guess_type(path.name)[0] or "text/html"


def report_papers(token: str) -> list[dict[str, Any]]:
    """Expose daily-card identities and their stored preference state."""
    path, _ = report_file(token)
    source = _daily_report_source(path, configured_reports_dir())
    try:
        from utils.legacy_history import parse_legacy_report_file

        cards = parse_legacy_report_file(path, source=source)
    except Exception:
        return []
    rows = []
    for card in cards[:500]:
        paper_id = str(card.get("paper_id") or "").strip()
        title = str(card.get("title") or "").strip()
        if paper_id and title:
            rows.append(
                {
                    "source": str(card.get("source") or source).strip().lower(),
                    "paper_id": paper_id,
                    "canonical_id": card.get("canonical_id"),
                    "version": card.get("version"),
                    "title": title,
                    "authors": list(card.get("authors") or []),
                    "categories": [],
                }
            )
    # Creating the tiny local ledger here makes legacy reports immediately
    # markable, matching Streamlit's in-report controls.  This does not add
    # any paper-delivery history; it only stores an explicit user preference.
    store = open_store(create=True)
    preferences = store.get_preference_map(rows) if store is not None and rows else {}
    for row in rows:
        row["preference"] = preferences.get(
            (str(row["source"]), str(row["paper_id"])), "none"
        )
    return rows


def local_backups() -> list[dict[str, Any]]:
    try:
        return list_local_backups(configured_data_dir())
    except Exception as exc:
        raise ModernWebUIError(f"读取本地备份失败：{exc}") from exc


def _configured_webdav_client(
    settings: Mapping[str, Any] | None = None,
    env_values: Mapping[str, Any] | None = None,
    *,
    allow_unconfigured: bool = False,
) -> Any | None:
    """Build a WebDAV client from the current persisted panel values.

    ``config.settings`` is intentionally a long-lived worker snapshot.  The
    modern panel must instead use the just-saved JSON/.env values for a manual
    test, sync, or backup; otherwise an operator can save new credentials and
    still send the operation to the old endpoint until the container restarts.
    ``allow_unconfigured`` is used by local backup: an incomplete optional
    WebDAV setup must never prevent a healthy local archive from being made.
    """
    flat = dict(settings or flat_config())
    if not _coerce_bool(flat.get("webdav_enabled"), False):
        if allow_unconfigured:
            return None
        raise ModernWebUIError("请先启用 WebDAV 同步。")

    env = env_values if env_values is not None else read_env()
    url = str(env.get("WEBDAV_URL") or "").strip()
    username = str(env.get("WEBDAV_USERNAME") or "").strip()
    password = str(env.get("WEBDAV_PASSWORD") or "")
    remote_path = str(
        flat.get("webdav_remote_path") or "/arxiv-daily-researcher/"
    ).strip()
    if not url or not username:
        if allow_unconfigured:
            return None
        raise ModernWebUIError("WebDAV URL 或用户名尚未配置完整。")

    proxy_url = ""
    if _coerce_bool(flat.get("proxy_enabled"), False) and _coerce_bool(
        flat.get("proxy_webdav"), True
    ):
        proxy_url = str(flat.get("proxy_url") or "").strip()
    try:
        return WebDAVSync(
            url=url,
            username=username,
            password=password,
            remote_path=remote_path,
            proxy_url=proxy_url,
        )
    except (ImportError, OSError, TypeError, ValueError) as exc:
        raise ModernWebUIError(f"创建 WebDAV 客户端失败：{exc}") from exc


_WEBDAV_OPERATION_CONFIG_FIELDS = frozenset(
    {
        "webdav_enabled",
        "webdav_remote_path",
        "webdav_sync_configs",
        "webdav_sync_history",
        "webdav_sync_keywords",
        "webdav_sync_reports",
        "proxy_enabled",
        "proxy_webdav",
        "proxy_url",
        "backup_local_retention_days",
        "backup_local_same_day_max_count",
    }
)
_WEBDAV_OPERATION_ENV_FIELDS = frozenset(
    {"WEBDAV_URL", "WEBDAV_USERNAME", "WEBDAV_PASSWORD"}
)


def _webdav_operation_values(
    config_overrides: Mapping[str, Any] | None = None,
    env_overrides: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge bounded, non-persistent form values for a manual WebDAV action.

    Streamlit's WebDAV controls use the live form values for a test, sync or
    local backup; they do not silently click the global Save button first.
    Keep that contract in the modern panel.  The operator can therefore test
    a new endpoint or scope without altering the worker's persisted settings.
    """

    if config_overrides is not None and not isinstance(config_overrides, Mapping):
        raise ModernWebUIError("WebDAV 配置覆盖必须是对象。")
    if env_overrides is not None and not isinstance(env_overrides, Mapping):
        raise ModernWebUIError("WebDAV 凭据覆盖必须是对象。")

    # ``flat_config`` normally builds a fresh mapping, but keep this helper
    # side-effect free even when a caller or test supplies a cached mapping.
    settings = dict(flat_config())
    for key, value in (config_overrides or {}).items():
        if key not in _WEBDAV_OPERATION_CONFIG_FIELDS:
            raise ModernWebUIError("包含不支持的 WebDAV 配置字段。")
        if not isinstance(value, (str, int, float, bool)):
            raise ModernWebUIError("WebDAV 配置值无效。")
        settings[key] = value

    env = {str(key): str(value) for key, value in read_env().items()}
    for key, value in (env_overrides or {}).items():
        if key not in _WEBDAV_OPERATION_ENV_FIELDS:
            raise ModernWebUIError("包含不支持的 WebDAV 凭据字段。")
        if not isinstance(value, (str, int, float, bool)):
            raise ModernWebUIError("WebDAV 凭据值无效。")
        text = str(value)
        if len(text) > 12_000 or "\x00" in text:
            raise ModernWebUIError("WebDAV 凭据长度无效。")
        env[key] = text
    return settings, env


def create_local_backup(
    config_overrides: Mapping[str, Any] | None = None,
    env_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the same local snapshot and optional incremental mirror as Streamlit."""
    settings, env = _webdav_operation_values(config_overrides, env_overrides)
    try:
        webdav_sync = _configured_webdav_client(
            settings, env, allow_unconfigured=True
        )
        result = create_backup(
            configured_data_dir(settings),
            database=configured_db_path(settings),
            retention_days=int(settings.get("backup_local_retention_days", LOCAL_BACKUP_RETENTION_DAYS)),
            same_day_max_count=int(settings.get("backup_local_same_day_max_count", LOCAL_BACKUP_SAME_DAY_MAX_COUNT)),
            webdav_sync=webdav_sync,
        )
        if _coerce_bool(settings.get("webdav_enabled"), False) and webdav_sync is None:
            result["webdav_skipped"] = "credentials_incomplete"
        return result
    except (OSError, ValueError) as exc:
        raise ModernWebUIError(f"创建本地备份失败：{exc}") from exc


def export_database_backup() -> tuple[bytes, str]:
    settings = flat_config()
    try:
        return export_backup_zip(
            configured_data_dir(settings), database=configured_db_path(settings)
        )
    except (OSError, ValueError) as exc:
        raise ModernWebUIError(f"导出备份失败：{exc}") from exc


def restore_database_backup(content: bytes, filename: str) -> dict[str, Any]:
    if not isinstance(content, bytes) or not content or len(content) > 1024 * 1024 * 1024:
        raise ModernWebUIError("备份文件为空或超过 1 GB 限制。")
    safe_name = Path(str(filename or "backup.zip")).name
    if safe_name.lower().split(".")[-1] not in {"zip", "gz", "db"}:
        raise ModernWebUIError("仅支持 zip、gz 或 db 备份文件。")
    settings = flat_config()
    try:
        data_dir = configured_data_dir(settings)
        # A restore swaps the live SQLite file.  Never let it race a worker
        # task that already owns a shared activity gate: return a clear UI
        # error and let the operator retry after the task is idle.
        with database_restore_activity_gate(
            exclusive=True,
            nonblocking=True,
            data_dir=data_dir,
        ):
            result = restore_backup_archive(
                data_dir,
                content,
                safe_name,
                database=configured_db_path(settings),
            )
        # A restore can replace the SQLite file beneath the WebUI. Recreate
        # the lightweight store wrapper so any necessary schema upgrade runs
        # before the next read.
        _invalidate_runtime_caches(clear_config=False)
        return result
    except DatabaseRestoreBusyError as exc:
        raise ModernWebUIError("有运行中的任务正在使用数据库，请等待任务完成后再恢复备份。") from exc
    except (OSError, ValueError) as exc:
        raise ModernWebUIError(f"导入备份失败：{exc}") from exc


def export_configuration() -> tuple[bytes, str]:
    """Export config and .env exactly like the compatibility panel."""
    import io
    import zipfile

    files = [("config.json", DEFAULT_CONFIG_PATH), (".env", DEFAULT_ENV_PATH)]
    present = [(name, path) for name, path in files if path.is_file()]
    if not present:
        raise ModernWebUIError("未找到可导出的配置文件。")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in present:
            archive.write(path, name)
    return buffer.getvalue(), "arxiv_researcher_config.zip"


def webdav_operation(
    operation: str,
    config_overrides: Mapping[str, Any] | None = None,
    env_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings, env = _webdav_operation_values(config_overrides, env_overrides)
    client = _configured_webdav_client(settings, env)
    try:
        if operation == "test":
            return {"ok": bool(client.test_connection())}
        if operation == "upload":
            return {
                "ok": True,
                "result": client.sync_all(
                    direction="upload",
                    include_reports=_coerce_bool(settings.get("webdav_sync_reports"), False),
                    include_configs=_coerce_bool(settings.get("webdav_sync_configs"), True),
                    include_history=_coerce_bool(settings.get("webdav_sync_history"), True),
                    include_keywords=_coerce_bool(settings.get("webdav_sync_keywords"), True),
                ),
            }
        if operation == "download":
            return {
                "ok": True,
                "result": client.sync_all(
                    direction="download",
                    include_reports=_coerce_bool(settings.get("webdav_sync_reports"), False),
                    include_configs=_coerce_bool(settings.get("webdav_sync_configs"), True),
                    include_history=_coerce_bool(settings.get("webdav_sync_history"), True),
                    include_keywords=_coerce_bool(settings.get("webdav_sync_keywords"), True),
                ),
            }
    except Exception as exc:
        raise ModernWebUIError(f"WebDAV {operation} 失败：{exc}") from exc
    raise ModernWebUIError("不支持的 WebDAV 操作。")


def connection_test(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Run a one-off external connection test using the submitted secret.

    Nothing from this endpoint is persisted. This mirrors the Streamlit test
    buttons and avoids making a user press Save just to validate a new key.
    """
    settings = read_env()
    values = dict(payload)
    kind = str(kind)
    try:
        if kind == "cheap_llm":
            ok, message = validate_llm_connection(
                str(values.get("api_key") or settings.get("CHEAP_LLM__API_KEY") or ""),
                str(values.get("base_url") or settings.get("CHEAP_LLM__BASE_URL") or ""),
                str(values.get("model") or settings.get("CHEAP_LLM__MODEL_NAME") or ""),
            )
        elif kind == "smart_llm":
            ok, message = validate_llm_connection(
                str(values.get("api_key") or settings.get("SMART_LLM__API_KEY") or ""),
                str(values.get("base_url") or settings.get("SMART_LLM__BASE_URL") or ""),
                str(values.get("model") or settings.get("SMART_LLM__MODEL_NAME") or ""),
            )
        elif kind == "mineru":
            ok, message = validate_mineru_connection(
                str(values.get("api_key") or settings.get("MINERU_API_KEY") or "")
            )
        elif kind == "openalex":
            ok, message = validate_openalex_connection(
                str(values.get("api_key") or settings.get("OPENALEX_API_KEY") or "")
            )
        elif kind == "semantic_scholar":
            ok, message = validate_semantic_scholar_connection(
                str(values.get("api_key") or settings.get("SEMANTIC_SCHOLAR_API_KEY") or "")
            )
        elif kind == "smtp":
            # A blank form value means the operator left the switch at its
            # documented default.  It must not silently turn TLS off merely
            # because optional environment fields are redacted as empty in
            # the modern settings payload.
            tls_value = values.get("use_tls")
            if tls_value in (None, ""):
                tls_value = settings.get("SMTP_USE_TLS") or "true"
            ok, message = validate_smtp_connection(
                str(values.get("host") or settings.get("SMTP_HOST") or ""),
                int(values.get("port") or settings.get("SMTP_PORT") or 587),
                str(values.get("user") or settings.get("SMTP_USER") or ""),
                str(values.get("password") or settings.get("SMTP_PASSWORD") or ""),
                _coerce_bool(tls_value, True),
            )
        else:
            raise ModernWebUIError("不支持的连接测试。")
    except (TypeError, ValueError) as exc:
        raise ModernWebUIError(f"连接测试参数无效：{exc}") from exc
    return {"ok": bool(ok), "message": sanitize_task_error_summary(message, max_chars=600)}


def _log_category(name: str) -> str:
    """Classify logs with the same three buckets as the Streamlit viewer."""
    lowered = name.lower()
    if lowered.startswith(("system", "arxiv_researcher")):
        return "system"
    if lowered.startswith(("manual_", "legacy_import_", "history_data_repair_", "history_omission_scan_", "supplement_", "backfill_", "daily_", "cron_", "startup_")):
        return "run"
    return "other"


def _log_group(name: str) -> str:
    category = _log_category(name)
    if category == "system":
        return "系统日志"
    if category == "run":
        return "运行日志"
    return "其他日志"


def list_logs() -> list[dict[str, Any]]:
    if not LOGS_DIR.is_dir():
        return []
    rows = []
    try:
        paths = [path for path in LOGS_DIR.rglob("*.log") if path.is_file()]
    except OSError:
        paths = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        relative = path.relative_to(LOGS_DIR).as_posix()
        rows.append(
            {
                "id": base64.urlsafe_b64encode(relative.encode("utf-8")).decode("ascii").rstrip("="),
                "name": relative,
                "group": _log_group(path.name),
                "category": _log_category(path.name),
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "size_bytes": stat.st_size,
            }
        )
    rows.sort(key=lambda row: row["modified_at"], reverse=True)
    return rows[:500]


def read_log(token: str, *, max_lines: int = 300) -> dict[str, Any]:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", token):
        raise ModernWebUIError("日志标识无效。")
    try:
        relative = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise ModernWebUIError("日志标识无效。") from None
    path = (LOGS_DIR / relative).resolve()
    try:
        path.relative_to(LOGS_DIR.resolve())
    except ValueError as exc:
        raise ModernWebUIError("日志路径无效。") from exc
    if path.suffix != ".log" or not path.is_file():
        raise ModernWebUIError("日志文件不存在。")
    max_lines = max(100, min(int(max_lines), 5_000))
    try:
        selected, truncated, skipped = _read_log_tail_lines(path, max_lines=max_lines)
    except OSError as exc:
        raise ModernWebUIError(f"读取日志失败：{exc}") from exc
    if truncated:
        marker = (
            f"… 已隐藏较早的 {skipped} 行 …"
            if skipped is not None
            else f"… 已隐藏较早的内容，仅显示最后 {max_lines} 行 …"
        )
        selected.insert(0, marker)
    return {"name": relative, "content": "\n".join(selected), "truncated": truncated}
