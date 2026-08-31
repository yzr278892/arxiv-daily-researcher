"""
Config I/O module - shared read/write logic for .env and runtime/config.json.

Used by: src/utils/setup_wizard.py, src/modern_webui/app.py
"""

import errno
import json
import json5
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from utils.source_registry import (
    CORE_SOURCE_CODES,
    definitions_for_builtin_codes,
    validate_source_definitions,
)

# ==================== Path Constants ====================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
RUNTIME_CONFIG_DIR = PROJECT_ROOT / "runtime"
DEFAULT_CONFIG_PATH = RUNTIME_CONFIG_DIR / "config.json"
LEGACY_CONFIG_PATH = PROJECT_ROOT / "configs" / "config.json"
CONFIG_EXAMPLE_PATH = PROJECT_ROOT / "configs" / "config.example.json"
ENV_EXAMPLE_PATH = PROJECT_ROOT / ".env.example"
# A single-file ``.env`` bind mount is writable even when its container parent
# directory is not.  In that layout a sibling ``/app/.env.bak`` cannot be
# created by the mapped NAS user, so retain the backup in an application-owned
# writable volume instead.
DEFAULT_ENV_BACKUP_DIR = PROJECT_ROOT / "data" / "config_backups"


class ConfigMigrationError(RuntimeError):
    """The one-time legacy configuration migration could not finish safely."""


def runtime_config_path(project_root: Optional[Path] = None) -> Path:
    """Return the ignored, writable config location for one installation."""
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    return root / "runtime" / "config.json"


def legacy_config_path(project_root: Optional[Path] = None) -> Path:
    """Return the v4.1-and-earlier configuration location."""
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    return root / "configs" / "config.json"

# These are the only file-location fields accepted from portable config.json
# exports.  Keeping the rule here as well as in config.Settings means WebUI
# saves and WebDAV restores reject an unsafe document *before* it becomes the
# live config file.  It also keeps the thin WebUI image independent of the
# full worker's settings imports.
_PROJECT_RELATIVE_PATH_FIELDS = {
    ("paths", "data_dir"),
    ("paths", "reference_pdfs"),
    ("paths", "reports"),
    ("paths", "downloaded_pdfs"),
    ("paths", "history_file"),
    ("paths", "history_dir"),
    ("keyword_tracker", "database", "path"),
    ("daily_research", "db_path"),
}


def _resolve_project_relative_config_path(value: object, *, label: str) -> Path:
    """Validate a portable config path without creating it or following writes."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空项目相对路径")
    raw_path = Path(value.strip())
    if raw_path.is_absolute():
        raise ValueError(f"{label} 必须是项目相对路径，不能使用绝对路径")
    if any(part == ".." for part in raw_path.parts):
        raise ValueError(f"{label} 不能包含父目录遍历（..）")
    if any(part in {"", "."} for part in raw_path.parts):
        raise ValueError(f"{label} 包含无效路径段")
    root = PROJECT_ROOT.resolve()
    candidate = (root / raw_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于项目目录内") from exc
    return candidate


def validate_config_document(config: object) -> Dict[str, Any]:
    """Validate the safety-critical shape of a portable config.json document.

    This intentionally preserves unknown forward-compatible keys.  It checks
    only the structure that could change filesystem boundaries or cause a
    seemingly valid configuration to save a schedule the worker cannot run.
    Runtime type validation remains authoritative in ``Settings`` at worker
    startup and fails closed there too.
    """
    if not isinstance(config, dict):
        raise ValueError("config.json 根节点必须是 JSON 对象")

    for *section_path, field_name in _PROJECT_RELATIVE_PATH_FIELDS:
        current: object = config
        valid_parent = True
        for section in section_path:
            if not isinstance(current, dict):
                valid_parent = False
                break
            if section not in current:
                valid_parent = False
                break
            current = current[section]
        if not valid_parent:
            continue
        if not isinstance(current, dict):
            label = ".".join((*section_path, field_name))
            raise ValueError(f"{label} 所在配置段必须是对象")
        if field_name in current:
            label = ".".join((*section_path, field_name))
            _resolve_project_relative_config_path(current[field_name], label=label)

    webdav = config.get("webdav")
    if webdav is not None:
        if not isinstance(webdav, dict):
            raise ValueError("webdav 配置段必须是对象")
        if webdav.get("sync_mode") == "scheduled":
            from utils.webdav_sync import validate_cron_schedule

            validate_cron_schedule(str(webdav.get("cron_schedule", "")))

    daily_research = config.get("daily_research")
    if daily_research is not None:
        if not isinstance(daily_research, dict):
            raise ValueError("daily_research 配置段必须是对象")
        limit = daily_research.get("max_papers_per_run", 0)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(
                "daily_research.max_papers_per_run 必须是非负整数（0 表示不限）"
            )
    history_maintenance = config.get("history_maintenance")
    if history_maintenance is not None:
        if not isinstance(history_maintenance, dict):
            raise ValueError("history_maintenance 配置段必须是对象")
        limit = history_maintenance.get("max_papers_per_run", 0)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(
                "history_maintenance.max_papers_per_run 必须是非负整数（0 表示不限）"
            )
    favorites = config.get("favorites")
    if favorites is not None:
        if not isinstance(favorites, dict):
            raise ValueError("favorites 配置段必须是对象")
        auto_favorite = favorites.get("auto_favorite_qualified_papers", True)
        if not isinstance(auto_favorite, bool):
            raise ValueError(
                "favorites.auto_favorite_qualified_papers 必须是布尔值"
            )
    backup = config.get("backup")
    if backup is not None:
        if not isinstance(backup, dict):
            raise ValueError("backup 配置段必须是对象")
        from utils.backup import (
            validate_local_backup_retention_days,
            validate_local_backup_same_day_max_count,
        )

        if "local_retention_days" in backup:
            validate_local_backup_retention_days(backup["local_retention_days"])
        if "same_day_max_count" in backup:
            validate_local_backup_same_day_max_count(backup["same_day_max_count"])
    legacy_history = config.get("legacy_history")
    if legacy_history is not None:
        if not isinstance(legacy_history, dict):
            raise ValueError("legacy_history 配置段必须是对象")
        if "full_repair_enabled" in legacy_history and not isinstance(
            legacy_history["full_repair_enabled"], bool
        ):
            raise ValueError("legacy_history.full_repair_enabled 必须是布尔值")
    keyword_tracker = config.get("keyword_tracker")
    if keyword_tracker is not None:
        if not isinstance(keyword_tracker, dict):
            raise ValueError("keyword_tracker 配置段必须是对象")
        normalization = keyword_tracker.get("normalization", {})
        if normalization is None:
            normalization = {}
        if not isinstance(normalization, dict):
            raise ValueError("keyword_tracker.normalization 必须是对象")
        if "llm_role" in normalization and str(normalization["llm_role"]).strip().lower() not in {"cheap", "smart"}:
            raise ValueError("keyword_tracker.normalization.llm_role 必须是 cheap 或 smart")
    data_sources = config.get("data_sources")
    if data_sources is not None:
        if not isinstance(data_sources, dict):
            raise ValueError("data_sources 配置段必须是对象")
        extra_sources = data_sources.get("extra_sources", {})
        if extra_sources is None:
            extra_sources = {}
        if not isinstance(extra_sources, dict):
            raise ValueError("data_sources.extra_sources 必须是对象")
        if not isinstance(extra_sources.get("enabled", False), bool):
            raise ValueError("data_sources.extra_sources.enabled 必须是布尔值")
        validate_source_definitions(extra_sources.get("definitions", []))
    return config

# ==================== Data Source Options ====================

ALL_DATA_SOURCES = list(CORE_SOURCE_CODES)

# ==================== LLM Provider Presets ====================

LLM_PROVIDERS = {
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "cheap_model": "gpt-4o-mini",
        "smart_model": "gpt-4o",
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "cheap_model": "deepseek-chat",
        "smart_model": "deepseek-chat",
    },
    "Ollama (Local)": {
        "base_url": "http://127.0.0.1:11434/v1",
        "cheap_model": "qwen2.5:7b",
        "smart_model": "qwen2.5:14b",
    },
    "Zhipu AI": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "cheap_model": "glm-4-flash",
        "smart_model": "glm-4",
    },
    "Custom": {
        "base_url": "",
        "cheap_model": "",
        "smart_model": "",
    },
}

# ==================== .env Field Definitions ====================

ENV_FIELDS = [
    # (key, label, is_secret, default_value)
    # LLM - Cheap
    ("CHEAP_LLM__API_KEY", "Low-cost LLM API Key", True, ""),
    ("CHEAP_LLM__BASE_URL", "Low-cost LLM Base URL", False, "https://api.openai.com/v1"),
    ("CHEAP_LLM__MODEL_NAME", "Low-cost LLM Model", False, "gpt-4o-mini"),
    ("CHEAP_LLM__TEMPERATURE", "Low-cost LLM Temperature", False, "0.3"),
    # LLM - Smart
    ("SMART_LLM__API_KEY", "High-perf LLM API Key", True, ""),
    ("SMART_LLM__BASE_URL", "High-perf LLM Base URL", False, "https://api.openai.com/v1"),
    ("SMART_LLM__MODEL_NAME", "High-perf LLM Model", False, "gpt-4o"),
    ("SMART_LLM__TEMPERATURE", "High-perf LLM Temperature", False, "0.3"),
    # Third-party APIs
    ("ENABLE_OPENALEX", "Enable OpenAlex", False, "true"),
    ("OPENALEX_API_KEY", "OpenAlex API Key", True, ""),
    ("ENABLE_SEMANTIC_SCHOLAR_TLDR", "Enable Semantic Scholar TLDR", False, "true"),
    ("SEMANTIC_SCHOLAR_API_KEY", "Semantic Scholar API Key", True, ""),
    ("MINERU_API_KEY", "MinerU API Key", True, ""),
    # SMTP
    ("SMTP_HOST", "SMTP Host", False, ""),
    ("SMTP_PORT", "SMTP Port", False, "587"),
    ("SMTP_USER", "SMTP User", False, ""),
    ("SMTP_PASSWORD", "SMTP Password", True, ""),
    ("SMTP_FROM", "SMTP From Address", False, ""),
    ("SMTP_TO", "SMTP To Addresses", False, ""),
    ("SMTP_USE_TLS", "SMTP Use TLS", False, "true"),
    # Webhooks
    ("WECHAT_WEBHOOK_URL", "WeChat Work Webhook URL", True, ""),
    ("DINGTALK_WEBHOOK_URL", "DingTalk Webhook URL", True, ""),
    ("DINGTALK_SECRET", "DingTalk Secret", True, ""),
    ("TELEGRAM_BOT_TOKEN", "Telegram Bot Token", True, ""),
    ("TELEGRAM_CHAT_ID", "Telegram Chat ID", False, ""),
    ("SLACK_WEBHOOK_URL", "Slack Webhook URL", True, ""),
    ("GENERIC_WEBHOOK_URL", "Generic Webhook URL", True, ""),
]

# ==================== Config JSON Section Comments ====================

SECTION_COMMENTS = {
    "data_sources": "Data Source Configuration",
    "run_lock": "Run Lock Configuration",
    "target_domains": "ArXiv Target Domain Configuration",
    "keywords": "Keyword Configuration",
    "scoring_settings": "Scoring Configuration",
    "paths": "Path Configuration",
    "keyword_tracker": "Keyword Trend Tracking Configuration",
    "notifications": "Notification Configuration",
    "retry": "Retry Configuration",
    "logging": "Logging Configuration",
    "concurrency": "Concurrency Configuration",
    "pdf_parser": "PDF Parser Configuration",
    "report_settings": "Report Configuration",
    "auto_update": "Release Update Notification Configuration",
    "token_tracking": "Token Tracking Configuration",
    "proxy": "Network Proxy Configuration",
    "webdav": "WebDAV Sync Configuration",
    "backup": "Database Backup Configuration",
    "legacy_history": "Legacy History Import Configuration",
    "trend_research": "Trend Research Mode Configuration",
}


# ==================== .env Read / Write ====================


def _restore_owner(path: Path, stat_result) -> None:
    """Keep bind-mounted configuration writable by its original host owner."""
    if stat_result is None:
        return
    try:
        os.chown(path, stat_result.st_uid, stat_result.st_gid)
    except (AttributeError, OSError):
        # Non-root local users cannot chown, and Windows has no POSIX chown.
        # The write itself remains valid in both cases.
        pass


def _is_permission_or_readonly_error(exc: OSError) -> bool:
    """Whether an I/O failure is caused by an unwritable bind-mount parent."""
    return isinstance(exc, PermissionError) or exc.errno in {
        errno.EACCES,
        errno.EPERM,
        errno.EROFS,
    }


def _rewrite_file_in_place(path: Path, content: str) -> None:
    """Safely flush a replacement when a single-file bind mount blocks rename."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _backup_env_file(path: Path) -> None:
    """Create an env backup without requiring its bind-mount parent to be writable.

    Docker commonly mounts only ``.env`` at ``/app/.env``.  The mapped runtime
    user can update that file, but cannot create its traditional sibling
    ``/app/.env.bak`` because ``/app`` belongs to the image.  The normal
    sibling location remains preferred; only the default project env path gets
    a durable fallback under ``data/config_backups``.
    """
    source_stat = path.stat()
    candidates = [path.parent / ".env.bak"]
    if path == DEFAULT_ENV_PATH:
        candidates.append(DEFAULT_ENV_BACKUP_DIR / ".env.bak")

    for backup_path in candidates:
        try:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
            _restore_owner(backup_path, source_stat)
            os.chmod(backup_path, 0o600)
            return
        except OSError as exc:
            if not _is_permission_or_readonly_error(exc):
                raise

    # The configuration target itself may still be a writable single-file
    # bind mount.  Do not make first-time setup impossible merely because a
    # backup directory is unavailable; the complete replacement is held in
    # memory and written with fsync below.


def _atomic_write_text(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
    preserve_existing_mode: bool = False,
) -> None:
    """Replace a configuration file without partial writes or ownership drift.

    ``os.replace`` is required for atomicity, but in a root Docker container it
    would otherwise change a bind-mounted host file to root ownership.  Preserve
    the target owner (or parent-directory owner for a new file) before replacing
    it so users can still edit and export their own configuration afterwards.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_stat = path.stat()
    except OSError:
        existing_stat = None
    try:
        owner_stat = existing_stat or path.parent.stat()
    except OSError:
        owner_stat = None
    desired_mode = (
        (existing_stat.st_mode & 0o777)
        if preserve_existing_mode and existing_stat is not None
        else mode
    )
    temporary_path = None
    try:
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
                os.chmod(temporary_path, desired_mode)
                _restore_owner(temporary_path, owner_stat)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            # A writable single-file bind mount has no writable parent in the
            # container.  It cannot support an adjacent atomic temp file, but
            # it can still be updated safely enough with a flushed in-place
            # write.  New files retain the normal atomic requirement.
            if existing_stat is None or not _is_permission_or_readonly_error(exc):
                raise
            _rewrite_file_in_place(path, content)
            return
        try:
            os.replace(temporary_path, path)
        except OSError as exc:
            if exc.errno not in (errno.EBUSY, errno.EPERM, errno.EXDEV):
                raise
            # Single-file bind mounts (e.g. Docker ``-v ./.env:/app/.env``)
            # reject rename() onto the mount point with EBUSY.  Rewrite the
            # mounted file in place instead; the inode survives, so the host
            # ownership and mode stay untouched.
            _rewrite_file_in_place(path, content)
        else:
            temporary_path = None
            os.chmod(path, desired_mode)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_env(path: Optional[Path] = None) -> Dict[str, str]:
    """Read .env file into a flat dict. Skips comments and blank lines."""
    if path is None:
        path = DEFAULT_ENV_PATH
    path = Path(path)
    result = {}
    if not path.exists():
        return result

    content = path.read_text(encoding="utf-8")
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            # Remove surrounding quotes if present
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            result[key] = value
    return result


_DEPRECATED_ENV_KEYS = frozenset({"OPENALEX_EMAIL"})


def write_env(values: Dict[str, str], path: Optional[Path] = None) -> None:
    """
    Write .env file using .env.example as structural template.

    Active keys are written uncommented, empty keys stay commented.
    Creates a ``.env.bak`` backup before writing. For a single-file Docker
    bind mount whose parent is read-only to the runtime user, the backup is
    retained under ``data/config_backups/.env.bak`` instead.
    """
    if path is None:
        path = DEFAULT_ENV_PATH
    path = Path(path)
    # ``mailto`` no longer participates in OpenAlex authentication or quota
    # allocation.  Drop a legacy value on the next normal panel/wizard save
    # instead of perpetuating a misleading personal-data setting.
    values = {key: value for key, value in values.items() if key not in _DEPRECATED_ENV_KEYS}

    # Backup existing
    if path.exists():
        _backup_env_file(path)

    # If .env.example exists, use it as template
    if ENV_EXAMPLE_PATH.exists():
        template = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    else:
        # Fallback: just write all values flat
        lines = []
        for key, value in values.items():
            if value:
                lines.append(f"{key}={value}")
        _atomic_write_text(path, "\n".join(lines) + "\n")
        return

    written_keys = set()
    output_lines = []

    for line in template.splitlines():
        stripped = line.strip()

        # Blank or pure comment lines - preserve as-is
        if not stripped:
            output_lines.append(line)
            continue

        if stripped.startswith("#"):
            # Check if this is a commented-out KEY=VALUE
            comment_body = stripped.lstrip("#").strip()
            if "=" in comment_body:
                potential_key = comment_body.split("=", 1)[0].strip()
                if potential_key in values and values[potential_key]:
                    output_lines.append(f"{potential_key}={values[potential_key]}")
                    written_keys.add(potential_key)
                    continue
            output_lines.append(line)
            continue

        # Active KEY=VALUE line
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                val = values[key]
                if val:
                    output_lines.append(f"{key}={val}")
                else:
                    output_lines.append(f"# {key}=")
                written_keys.add(key)
                continue
            output_lines.append(line)
            continue

        output_lines.append(line)

    # Append any values not in template
    extra = {k: v for k, v in values.items() if k not in written_keys and v}
    if extra:
        output_lines.append("")
        output_lines.append("# Additional configuration")
        for key, val in extra.items():
            output_lines.append(f"{key}={val}")

    _atomic_write_text(path, "\n".join(output_lines) + "\n")


# ==================== config.json Read / Write ====================


def ensure_runtime_config_path(project_root: Optional[Path] = None) -> Path:
    """Return the runtime config path, copying a legacy file once when needed.

    ``configs/config.json`` was the live configuration path through v4.1.
    Keeping it as a read-only compatibility source lets an existing Docker or
    NAS deployment upgrade without manual file moves. The copy is made only
    when ``runtime/config.json`` does not exist; the legacy file is retained,
    so a failed or interrupted upgrade never removes the operator's only
    configuration. Subsequent reads and writes use the ignored runtime path.
    """
    destination = runtime_config_path(project_root)
    if destination.exists():
        if not destination.is_file():
            raise ConfigMigrationError(f"运行配置路径不是普通文件：{destination}")
        return destination

    source = legacy_config_path(project_root)
    if not source.exists():
        return destination
    if not source.is_file():
        raise ConfigMigrationError(f"旧配置路径不是普通文件：{source}")

    try:
        source_text = source.read_text(encoding="utf-8")
        source_mode = source.stat().st_mode & 0o777
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Worker and WebUI can start together. They may both reach this
        # branch, but both copy the same legacy document; a second existence
        # check avoids replacing a file already created by the other process.
        if not destination.exists():
            _atomic_write_text(
                destination,
                source_text,
                mode=source_mode or 0o600,
            )
    except (OSError, UnicodeError) as exc:
        raise ConfigMigrationError(
            f"无法把旧配置迁移到运行目录：{source} -> {destination}"
        ) from exc

    if not destination.is_file():
        raise ConfigMigrationError(f"运行配置迁移后不可读取：{destination}")
    return destination


def read_config_json(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read and validate config.json using json5 (supports comments)."""
    if path is None:
        path = ensure_runtime_config_path()
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return validate_config_document(json5.load(f))


def _indent_value(value_str: str, indent_level: int = 2) -> str:
    """Indent all lines of a multi-line JSON value except the first."""
    lines = value_str.split("\n")
    if len(lines) <= 1:
        return value_str
    prefix = " " * indent_level
    result = [lines[0]]
    for line in lines[1:]:
        result.append(prefix + line)
    return "\n".join(result)


def _extract_comment_blocks(text: str) -> List[Tuple[List[str], str]]:
    """Collect full-line ``//`` comment blocks with their anchor member key.

    An anchor is the ``"key":`` prefix of the first JSON member line that
    follows the block.  Blocks without such an anchor (trailing file
    comments) are dropped: there is nothing stable to reattach them to.
    """
    blocks: List[Tuple[List[str], str]] = []
    current: List[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("//"):
            current.append(stripped)
            continue
        if not stripped:
            continue
        match = re.match(r'^("[^"]+"\s*:)', stripped)
        if current and match:
            blocks.append((current, match.group(1)))
        current = []
    return blocks


def _reinject_comments(original_text: str, new_text: str) -> str:
    """Carry hand-written comments from the original file into new output.

    Section headers generated by :data:`SECTION_COMMENTS` are already part of
    the new text; blocks whose first line already appears verbatim are skipped
    so generated headers are never duplicated.  For anchors that repeat
    (``"enabled":`` in several sections) occurrences are consumed in document
    order, matching how the deterministic writer emits keys.
    """
    blocks = _extract_comment_blocks(original_text)
    if not blocks:
        return new_text
    new_lines = new_text.splitlines()
    existing = {line.strip() for line in new_lines}
    used_positions: Dict[str, List[int]] = {}

    def next_anchor_line(prefix: str) -> Optional[int]:
        positions = used_positions.setdefault(
            prefix,
            [
                idx
                for idx, line in enumerate(new_lines)
                if line.strip().startswith(prefix)
            ],
        )
        return positions.pop(0) if positions else None

    for comment_lines, prefix in blocks:
        if comment_lines[0] in existing:
            continue
        idx = next_anchor_line(prefix)
        if idx is None:
            continue
        indent = new_lines[idx][: len(new_lines[idx]) - len(new_lines[idx].lstrip())]
        injected = [f"{indent}{comment}" for comment in comment_lines]
        new_lines[idx:idx] = injected
        # Positions after this index shifted by the inserted block length.
        for other in used_positions.values():
            shifted = [
                pos + len(injected) if pos >= idx else pos for pos in other
            ]
            other[:] = shifted
        existing.update(comment_lines)
    return "\n".join(new_lines) + ("\n" if new_text.endswith("\n") else "")


def write_config_json(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    """
    Write config.json with section comment headers.

    Hand-written full-line comments from the existing file are carried over by
    re-anchoring them to their original keys. Creates .json.bak backup before
    writing.
    """
    if path is None:
        path = ensure_runtime_config_path()
    path = Path(path)

    # Validate before creating a backup.  An unsafe imported/edited document
    # must leave the current live config and its backup untouched.
    validate_config_document(config)

    # Keep the UI from saving a schedule that the worker cannot interpret.
    # Import lazily: config_io is also deliberately usable in the thin WebUI
    # image, where importing the full worker settings module is undesirable.
    webdav = config.get("webdav")
    if isinstance(webdav, dict) and webdav.get("sync_mode") == "scheduled":
        from utils.webdav_sync import validate_cron_schedule

        webdav["cron_schedule"] = validate_cron_schedule(
            str(webdav.get("cron_schedule", ""))
        )

    # Backup existing
    original_text = None
    if path.exists():
        original_text = path.read_text(encoding="utf-8")
        backup_path = path.with_suffix(".json.bak")
        source_stat = path.stat()
        shutil.copy2(path, backup_path)
        _restore_owner(backup_path, source_stat)

    lines = ["{"]
    keys = list(config.keys())

    for i, key in enumerate(keys):
        # Add section comment header
        if key in SECTION_COMMENTS:
            if i > 0:
                lines.append("")
            lines.append(f"  // {'=' * 50}")
            lines.append(f"  // {SECTION_COMMENTS[key]}")
            lines.append(f"  // {'=' * 50}")

        value_str = json.dumps(config[key], indent=2, ensure_ascii=False)
        indented = _indent_value(value_str, indent_level=2)
        comma = "," if i < len(keys) - 1 else ""
        lines.append(f'  "{key}": {indented}{comma}')

    lines.append("}")

    new_text = "\n".join(lines) + "\n"
    if original_text is not None:
        new_text = _reinject_comments(original_text, new_text)

    _atomic_write_text(
        path,
        new_text,
        mode=0o644,
        preserve_existing_mode=True,
    )


# ==================== Config Structure Builders ====================


def _normalize_weighted_entries(
    raw: object,
    *,
    name_key: str,
    value_key: str,
    label: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Validate ordered name/value entries used by the modern configuration UI.

    The durable JSON keeps the old list-and-default-value fields beside these
    entries for older tools.  This normalizer is intentionally strict for new
    entries so a malformed hand edit cannot silently change scoring.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{label}必须是列表")
    if len(raw) > limit:
        raise ValueError(f"{label}最多允许 {limit} 条")
    rows: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{label}中的每一项必须是对象")
        name = item.get(name_key)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}名称不能为空")
        safe_name = name.strip()
        if len(safe_name) > 500 or "\x00" in safe_name:
            raise ValueError(f"{label}名称长度或格式无效")
        normalized_name = safe_name.casefold()
        if normalized_name in seen:
            raise ValueError(f"{label}不能重复：{safe_name}")
        value = item.get(value_key)
        if isinstance(value, bool):
            raise ValueError(f"{label}数值必须是非负数字")
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}数值必须是非负数字") from exc
        if not math.isfinite(numeric_value) or numeric_value < 0:
            raise ValueError(f"{label}数值必须是非负数字")
        rows.append({name_key: safe_name, value_key: numeric_value})
        seen.add(normalized_name)
    return rows


def build_config_dict(
    max_results: Optional[int] = None,
    max_results_per_source: Optional[Dict[str, int]] = None,
    enabled_sources: Optional[List[str]] = None,
    journals: Optional[List[str]] = None,
    extra_sources_enabled: bool = False,
    extra_source_definitions: Optional[List[Dict[str, Any]]] = None,
    reports_by_source: bool = True,
    arxiv_fetch_timeout_seconds: int = 180,
    arxiv_announcement_lookback_grace_days: int = 2,
    huggingface_papers_availability_lag_days: int = 2,
    huggingface_papers_lookback_grace_days: int = 2,
    huggingface_papers_request_timeout_seconds: int = 30,
    huggingface_papers_request_interval_seconds: float = 0.25,
    domains: Optional[List[str]] = None,
    primary_keywords: Optional[List[str]] = None,
    primary_keyword_weight: float = 1.0,
    primary_keyword_entries: Optional[List[Dict[str, Any]]] = None,
    enable_reference_extraction: bool = False,
    max_reference_keywords: int = 10,
    similarity_threshold: float = 0.75,
    ref_weight_high: float = 1.0,
    ref_count_high: int = 3,
    ref_weight_medium: float = 0.2,
    ref_count_medium: int = 5,
    ref_weight_low: float = 0.1,
    ref_count_low: int = 2,
    research_context: str = "",
    max_score_per_keyword: int = 10,
    enable_author_bonus: bool = False,
    expert_authors: Optional[List[str]] = None,
    author_bonus_points: float = 5.0,
    author_bonus_entries: Optional[List[Dict[str, Any]]] = None,
    passing_score_base: float = 5.0,
    passing_score_weight_coefficient: float = 3.0,
    score_strategy: str = "core_relevance_v2",
    core_relevance_threshold: float = 6.0,
    core_keyword_min_score: float = 7.0,
    reference_ranking_weight: float = 0.25,
    learned_weight_dampening: float = 0.5,
    learned_term_weight_cap: float = 2.0,
    score_strategy_explicit: bool = True,
    include_all_in_report: bool = True,
    keyword_tracker_enabled: bool = True,
    keyword_db_path: str = "data/keywords/keywords.db",
    keyword_normalization_enabled: bool = True,
    keyword_normalization_batch_size: int = 25,
    keyword_normalization_llm_role: str = "cheap",
    keyword_trend_default_days: int = 30,
    keyword_chart_top_n: int = 15,
    keyword_trend_top_n: int = 5,
    keyword_report_enabled: bool = True,
    keyword_report_frequency: str = "weekly",
    notifications_enabled: bool = False,
    notify_on_success: bool = True,
    notify_on_failure: bool = True,
    notify_attach_reports: bool = False,
    notification_top_n: int = 5,
    notify_email_enabled: bool = False,
    notify_wechat_enabled: bool = False,
    notify_dingtalk_enabled: bool = False,
    notify_telegram_enabled: bool = False,
    notify_slack_enabled: bool = False,
    notify_generic_webhook_enabled: bool = False,
    retry_max_attempts: int = 3,
    retry_min_wait: int = 2,
    retry_max_wait: int = 30,
    run_lock_max_age_hours: int = 12,
    log_rotation_type: str = "time",
    log_keep_days: int = 30,
    concurrency_enabled: bool = False,
    concurrency_workers: int = 3,
    llm_request_pool_enabled: bool = True,
    llm_requests_per_minute: int = 30,
    llm_request_pool_log_slow_wait_seconds: float = 5.0,
    llm_timeout_seconds: float = 300.0,
    llm_sdk_max_retries: int = 1,
    llm_retry_max_attempts: int = 5,
    llm_retry_min_wait: int = 5,
    llm_retry_max_wait: int = 120,
    daily_research_persistence_enabled: bool = True,
    daily_research_db_path: str = "data/daily_research/daily_research.db",
    daily_max_papers_per_run: int = 200,
    auto_favorite_qualified_papers: bool = True,
    history_maintenance_max_papers_per_run: int = 200,
    daily_run_time: str = "12:00",
    daily_enable_deep_analysis: bool = True,
    legacy_import_full_repair_enabled: bool = False,
    data_dir: str = "data",
    reference_pdfs: str = "data/reference_pdfs",
    reports: str = "data/reports",
    downloaded_pdfs: str = "data/downloaded_pdfs",
    history_dir: str = "data/history",
    history_file: Optional[str] = None,
    pdf_parser_mode: str = "pymupdf",
    mineru_model_version: str = "pipeline",
    mineru_poll_interval: int = 3,
    mineru_poll_timeout: int = 300,
    pdf_download_max_bytes: int = 50 * 1024 * 1024,
    enable_html_report: bool = True,
    enable_markdown_report: bool = True,
    auto_update_enabled: bool = True,
    token_tracking_enabled: bool = True,
    trend_default_date_range_days: int = 365,
    trend_max_results: int = 500,
    trend_sort_order: str = "ascending",
    trend_report_position: str = "end",
    trend_generate_tldr: bool = True,
    trend_tldr_batch_size: int = 10,
    trend_output_formats: Optional[List[str]] = None,
    trend_enabled_skills: Optional[List[str]] = None,
    trend_analysis_prompt: str = "",
    proxy_enabled: bool = False,
    proxy_url: str = "",
    proxy_no_proxy: str = "localhost,127.0.0.1",
    proxy_arxiv: bool = True,
    proxy_openalex: bool = False,
    proxy_huggingface_papers: bool = False,
    proxy_semantic_scholar: bool = False,
    proxy_llm_api: bool = False,
    proxy_notifications: bool = False,
    proxy_webdav: bool = True,
    proxy_update_check: bool = False,
    webdav_enabled: bool = False,
    webdav_remote_path: str = "/arxiv-researcher/",
    webdav_sync_mode: str = "manual",
    webdav_cron_schedule: str = "0 23 * * *",
    webdav_sync_configs: bool = True,
    webdav_sync_history: bool = True,
    webdav_sync_keywords: bool = True,
    webdav_sync_reports: bool = False,
    backup_enabled: bool = True,
    backup_local_retention_days: int = 7,
    backup_local_same_day_max_count: int = 0,
) -> Dict[str, Any]:
    """Build a nested config.json dict from flat parameters."""

    if (
        isinstance(daily_max_papers_per_run, bool)
        or not isinstance(daily_max_papers_per_run, int)
        or daily_max_papers_per_run < 0
    ):
        raise ValueError(
            "daily_research.max_papers_per_run 必须是非负整数（0 表示不限）"
        )
    if (
        isinstance(history_maintenance_max_papers_per_run, bool)
        or not isinstance(history_maintenance_max_papers_per_run, int)
        or history_maintenance_max_papers_per_run < 0
    ):
        raise ValueError(
            "history_maintenance.max_papers_per_run 必须是非负整数（0 表示不限）"
        )
    if not isinstance(auto_favorite_qualified_papers, bool):
        raise ValueError("favorites.auto_favorite_qualified_papers 必须是布尔值")

    if not isinstance(daily_run_time, str) or not re.fullmatch(
        r"\d{1,2}:\d{2}", str(daily_run_time).strip()
    ):
        raise ValueError("daily_research.run_time 必须是 HH:MM 格式")
    _rt_hour, _rt_minute = (int(part) for part in daily_run_time.strip().split(":"))
    if not (0 <= _rt_hour <= 23 and 0 <= _rt_minute <= 59):
        raise ValueError("daily_research.run_time 超出有效时间范围")
    daily_run_time = f"{_rt_hour:02d}:{_rt_minute:02d}"

    if not isinstance(extra_sources_enabled, bool):
        raise ValueError("extra_sources_enabled 必须是布尔值")
    keyword_normalization_llm_role = str(keyword_normalization_llm_role or "cheap").strip().lower()
    if keyword_normalization_llm_role not in {"cheap", "smart"}:
        raise ValueError("keyword_tracker.normalization.llm_role 必须是 cheap 或 smart")
    from utils.backup import (
        validate_local_backup_retention_days,
        validate_local_backup_same_day_max_count,
    )

    backup_local_retention_days = validate_local_backup_retention_days(
        backup_local_retention_days
    )
    backup_local_same_day_max_count = validate_local_backup_same_day_max_count(
        backup_local_same_day_max_count
    )
    raw_enabled = enabled_sources if enabled_sources is not None else ["arxiv"]
    if not isinstance(raw_enabled, list):
        raise ValueError("enabled_sources 必须是列表")
    normalized_enabled = []
    legacy_extra_codes = []
    for source in [*raw_enabled, *(journals or [])]:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("enabled_sources 中的数据源代码必须是非空字符串")
        source_code = source.strip().lower()
        if source_code in CORE_SOURCE_CODES:
            if source_code not in normalized_enabled:
                normalized_enabled.append(source_code)
        elif source_code not in legacy_extra_codes:
            legacy_extra_codes.append(source_code)

    definitions_were_explicit = extra_source_definitions is not None
    normalized_definitions = validate_source_definitions(extra_source_definitions or [])
    definition_codes = {item["code"] for item in normalized_definitions}
    missing_legacy_codes = [code for code in legacy_extra_codes if code not in definition_codes]
    if missing_legacy_codes and definitions_were_explicit:
        raise ValueError(
            "enabled_sources 包含未定义的额外来源: "
            + ", ".join(missing_legacy_codes)
        )
    if missing_legacy_codes:
        normalized_definitions.extend(definitions_for_builtin_codes(missing_legacy_codes))
        # Calls made with the old flat contract have no explicit definitions;
        # infer the former built-in selections once. In the v4 contract an
        # explicit False always wins, even if stale source codes remain in the
        # incoming enabled list.
        extra_sources_enabled = True
    if not definitions_were_explicit and "prl" in normalized_enabled:
        # PRL was a selectable top-level source before the v4 extra-source
        # switch existed.  Retain that old API contract exactly once; WebUI
        # saves always supply an explicit definitions list, so their explicit
        # False still wins.
        extra_sources_enabled = True

    # A checked extra-source switch without a selected source has no runtime
    # effect. Persist it as false so the configuration, worker status and
    # future WebUI reload all agree. PRL is special only because it remains a
    # reserved core code for compatibility while being shown under the extra
    # source group in the UI.
    has_selected_extra_source = bool(normalized_definitions) or "prl" in normalized_enabled
    extra_sources_enabled = bool(extra_sources_enabled and has_selected_extra_source)
    if not extra_sources_enabled:
        normalized_enabled = [
            source for source in normalized_enabled if source != "prl"
        ]
    if extra_sources_enabled:
        # The enabled list is intentionally a complete, portable source scope:
        # a worker launched without the WebUI can reproduce exactly which
        # declarative sources were selected.  Definitions themselves remain
        # available even when the extra-source switch is later turned off.
        normalized_enabled.extend(
            item["code"]
            for item in normalized_definitions
            if item["code"] not in normalized_enabled
        )

    primary_entries = _normalize_weighted_entries(
        primary_keyword_entries
        if primary_keyword_entries is not None
        else [
            {"keyword": keyword, "weight": primary_keyword_weight}
            for keyword in (primary_keywords or [])
        ],
        name_key="keyword",
        value_key="weight",
        label="主关键词",
    )
    author_entries = _normalize_weighted_entries(
        author_bonus_entries
        if author_bonus_entries is not None
        else [
            {"author": author, "points": author_bonus_points}
            for author in (expert_authors or [])
        ],
        name_key="author",
        value_key="points",
        label="作者加分",
    )

    config = {
        "data_sources": {
            # Preserve an explicit empty list so the worker can reject it
            # visibly.  Replacing it with a hidden default changes a user's
            # intended source scope at save time.
            "enabled": normalized_enabled,
            "extra_sources": {
                "enabled": extra_sources_enabled,
                "definitions": normalized_definitions,
            },
            "reports_by_source": reports_by_source,
            "arxiv": {
                "fetch_timeout_seconds": arxiv_fetch_timeout_seconds,
                "announcement_lookback_grace_days": arxiv_announcement_lookback_grace_days,
            },
            # Hugging Face Papers is an optional curated supplementary feed,
            # never a replacement for arXiv category completeness.
            "huggingface_papers": {
                "availability_lag_days": huggingface_papers_availability_lag_days,
                "lookback_grace_days": huggingface_papers_lookback_grace_days,
                "request_timeout_seconds": huggingface_papers_request_timeout_seconds,
                "request_interval_seconds": huggingface_papers_request_interval_seconds,
            },
        },
        "run_lock": {
            "max_age_hours": run_lock_max_age_hours,
        },
        "target_domains": {
            "domains": domains if domains is not None else ["quant-ph"],
        },
        "keywords": {
            "primary_keywords": {
                "weight": primary_keyword_weight,
                "keywords": [entry["keyword"] for entry in primary_entries],
                "entries": primary_entries,
            },
            "enable_reference_extraction": enable_reference_extraction,
            "reference_keywords_config": {
                "max_keywords": max_reference_keywords,
                "similarity_threshold": similarity_threshold,
                "weight_distribution": {
                    "high_importance": {
                        "weight": ref_weight_high,
                        "count": ref_count_high,
                    },
                    "medium_importance": {
                        "weight": ref_weight_medium,
                        "count": ref_count_medium,
                    },
                    "low_importance": {
                        "weight": ref_weight_low,
                        "count": ref_count_low,
                    },
                },
            },
            "research_context": research_context,
        },
        "scoring_settings": {
            "keyword_relevance_score": {
                "max_score_per_keyword": max_score_per_keyword,
            },
            "author_bonus": {
                "enabled": enable_author_bonus,
                "expert_authors": [entry["author"] for entry in author_entries],
                "bonus_points": author_bonus_points,
                "entries": author_entries,
            },
            "passing_score_formula": {
                "base_score": passing_score_base,
                "weight_coefficient": passing_score_weight_coefficient,
            },
            "include_all_in_report": include_all_in_report,
        },
        "paths": {
            "data_dir": data_dir,
            "reference_pdfs": reference_pdfs,
            "reports": reports,
            "downloaded_pdfs": downloaded_pdfs,
            "history_dir": history_dir,
        },
        "keyword_tracker": {
            "enabled": keyword_tracker_enabled,
            "database": {
                "path": keyword_db_path,
            },
            "normalization": {
                "enabled": keyword_normalization_enabled,
                "batch_size": keyword_normalization_batch_size,
                "llm_role": keyword_normalization_llm_role,
            },
            "trend_view": {
                "default_days": keyword_trend_default_days,
            },
            "charts": {
                "bar_chart": {"top_n": keyword_chart_top_n},
                "trend_chart": {"top_n": keyword_trend_top_n},
            },
            "report": {
                "enabled": keyword_report_enabled,
                "frequency": keyword_report_frequency,
            },
        },
        "notifications": {
            "enabled": notifications_enabled,
            "on_success": notify_on_success,
            "on_failure": notify_on_failure,
            "attach_reports": notify_attach_reports,
            "top_n": notification_top_n,
            "channels": {
                "email": {"enabled": notify_email_enabled},
                "wechat_work": {"enabled": notify_wechat_enabled},
                "dingtalk": {"enabled": notify_dingtalk_enabled},
                "telegram": {"enabled": notify_telegram_enabled},
                "slack": {"enabled": notify_slack_enabled},
                "generic_webhook": {"enabled": notify_generic_webhook_enabled},
            },
        },
        "retry": {
            "max_attempts": retry_max_attempts,
            "min_wait": retry_min_wait,
            "max_wait": retry_max_wait,
        },
        "logging": {
            "rotation_type": log_rotation_type,
            "keep_days": log_keep_days,
        },
        "concurrency": {
            "enabled": concurrency_enabled,
            "workers": concurrency_workers,
        },
        "llm_request_pool": {
            "enabled": llm_request_pool_enabled,
            "requests_per_minute": llm_requests_per_minute,
            "log_slow_wait_seconds": llm_request_pool_log_slow_wait_seconds,
        },
        "llm": {
            "timeout_seconds": llm_timeout_seconds,
            "sdk_max_retries": llm_sdk_max_retries,
            "retry_max_attempts": llm_retry_max_attempts,
            "retry_min_wait": llm_retry_min_wait,
            "retry_max_wait": llm_retry_max_wait,
        },
        "daily_research": {
            "enable_deep_analysis": daily_enable_deep_analysis,
            "max_papers_per_run": daily_max_papers_per_run,
            "run_time": daily_run_time,
            "db_path": daily_research_db_path,
        },
        "favorites": {
            "auto_favorite_qualified_papers": auto_favorite_qualified_papers,
        },
        "history_maintenance": {
            "max_papers_per_run": history_maintenance_max_papers_per_run,
        },
        "legacy_history": {
            "full_repair_enabled": legacy_import_full_repair_enabled,
        },
        "pdf_parser": {
            "mode": pdf_parser_mode,
            "mineru_model_version": mineru_model_version,
            "poll_interval": mineru_poll_interval,
            "poll_timeout": mineru_poll_timeout,
            "download_max_bytes": pdf_download_max_bytes,
        },
        "report_settings": {
            "enable_html_report": enable_html_report,
            "enable_markdown_report": enable_markdown_report,
        },
        "auto_update": {
            "enabled": auto_update_enabled,
        },
        "token_tracking": {
            "enabled": token_tracking_enabled,
        },
        "proxy": {
            "enabled": proxy_enabled,
            "url": proxy_url,
            "no_proxy": proxy_no_proxy,
            "scope": {
                "arxiv": proxy_arxiv,
                "openalex": proxy_openalex,
                "huggingface_papers": proxy_huggingface_papers,
                "semantic_scholar": proxy_semantic_scholar,
                "llm_api": proxy_llm_api,
                "notifications": proxy_notifications,
                "webdav": proxy_webdav,
                "update_check": proxy_update_check,
            },
        },
        "webdav": {
            "enabled": webdav_enabled,
            "remote_path": webdav_remote_path,
            "sync_mode": webdav_sync_mode,
            "cron_schedule": webdav_cron_schedule,
            "sync_configs": webdav_sync_configs,
            "sync_history": webdav_sync_history,
            "sync_keywords": webdav_sync_keywords,
            "sync_reports": webdav_sync_reports,
        },
        "backup": {
            "enabled": backup_enabled,
            "local_retention_days": backup_local_retention_days,
            "same_day_max_count": backup_local_same_day_max_count,
        },
        "trend_research": {
            "default_date_range_days": trend_default_date_range_days,
            "max_results": trend_max_results,
            "sort_order": trend_sort_order,
            "report_position": trend_report_position,
            "generate_tldr": trend_generate_tldr,
            "tldr_batch_size": trend_tldr_batch_size,
            "output_formats": trend_output_formats or ["markdown", "html"],
            # 综合分析是趋势研究的固定阶段。保留旧键以兼容旧版本配置，
            # 但不再允许空列表关闭它。
            "enabled_skills": ["comprehensive_analysis"],
            "analysis_prompt": trend_analysis_prompt or "",
        },
    }

    # Preserve a pre-V2 configuration's missing strategy on a UI/wizard
    # round-trip.  The absence intentionally means legacy compatibility; do
    # not silently flip an established installation to V2 merely by saving.
    if score_strategy_explicit:
        config["scoring_settings"]["strategy"] = {
            "id": score_strategy,
            "core_relevance_threshold": core_relevance_threshold,
            "core_keyword_min_score": core_keyword_min_score,
            "reference_ranking_weight": reference_ranking_weight,
            "learned_weight_dampening": learned_weight_dampening,
            "learned_term_weight_cap": learned_term_weight_cap,
        }

    # ``history_file`` was used by older configurations alongside
    # ``history_dir``.  It is not edited by a current WebUI widget, but a
    # save from any other tab must never silently discard it.
    if history_file is not None:
        config["paths"]["history_file"] = history_file

    return config


def flatten_config_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert nested config.json structure into flat dict for UI display.

    Returns a dict with descriptive keys matching build_config_dict parameters.
    """
    flat = {}

    # Data sources
    ds = config.get("data_sources", {})
    raw_enabled = ds.get("enabled", ["arxiv"])
    raw_journals = ds.get("journals", [])
    if not isinstance(raw_enabled, list):
        raw_enabled = []
    if not isinstance(raw_journals, list):
        raw_journals = []
    flat["enabled_sources"] = [
        source.strip().lower()
        for source in raw_enabled
        if isinstance(source, str) and source.strip()
    ]
    flat["journals"] = [
        source.strip().lower()
        for source in raw_journals
        if isinstance(source, str) and source.strip()
    ]
    has_extra_sources = "extra_sources" in ds
    extra_sources = ds.get("extra_sources", {})
    if not isinstance(extra_sources, dict):
        extra_sources = {}
    definitions = validate_source_definitions(extra_sources.get("definitions", []))
    definition_codes = {item["code"] for item in definitions}
    legacy_codes = []
    for source in [*raw_enabled, *raw_journals]:
        if isinstance(source, str):
            source_code = source.strip().lower()
            if source_code and source_code not in CORE_SOURCE_CODES and source_code not in definition_codes:
                legacy_codes.append(source_code)
                definition_codes.add(source_code)
    if legacy_codes and not has_extra_sources:
        definitions.extend(definitions_for_builtin_codes(legacy_codes))
    configured_prl = any(
        isinstance(source, str) and source.strip().lower() == "prl"
        for source in [*raw_enabled, *raw_journals]
    )
    requested_extra_sources = (
        bool(extra_sources.get("enabled", False))
        if has_extra_sources
        else bool(legacy_codes or configured_prl)
    )
    flat["extra_sources_enabled"] = bool(
        requested_extra_sources and (bool(definitions) or configured_prl)
    )
    core_enabled = []
    for source in [*raw_enabled, *raw_journals]:
        if isinstance(source, str):
            source_code = source.strip().lower()
            if source_code == "arxiv" and source_code not in core_enabled:
                core_enabled.append(source_code)
            elif (
                source_code == "prl"
                and flat["extra_sources_enabled"]
                and source_code not in core_enabled
            ):
                core_enabled.append(source_code)
    flat["enabled_sources"] = core_enabled
    if flat["extra_sources_enabled"]:
        for definition in definitions:
            code = definition["code"]
            if code not in flat["enabled_sources"]:
                flat["enabled_sources"].append(code)
    flat["extra_source_definitions"] = definitions
    flat["reports_by_source"] = ds.get("reports_by_source", True)
    flat["arxiv_fetch_timeout_seconds"] = ds.get("arxiv", {}).get("fetch_timeout_seconds", 180)
    flat["arxiv_announcement_lookback_grace_days"] = ds.get("arxiv", {}).get(
        "announcement_lookback_grace_days", 2
    )
    hf_papers = ds.get("huggingface_papers", {})
    if not isinstance(hf_papers, dict):
        hf_papers = {}
    flat["huggingface_papers_availability_lag_days"] = hf_papers.get(
        "availability_lag_days", 2
    )
    flat["huggingface_papers_lookback_grace_days"] = hf_papers.get(
        "lookback_grace_days", 2
    )
    flat["huggingface_papers_request_timeout_seconds"] = hf_papers.get(
        "request_timeout_seconds", 30
    )
    flat["huggingface_papers_request_interval_seconds"] = hf_papers.get(
        "request_interval_seconds", 0.25
    )

    # Run lock
    rl = config.get("run_lock", {})
    flat["run_lock_max_age_hours"] = rl.get("max_age_hours", 12)

    # Target domains
    td = config.get("target_domains", {})
    flat["domains"] = td.get("domains", ["quant-ph"])

    # Keywords
    kw = config.get("keywords", {})
    pk = kw.get("primary_keywords", {})
    if not isinstance(pk, dict):
        pk = {}
    legacy_primary_weight = pk.get("weight", 1.0)
    stored_primary_entries = pk.get("entries")
    if isinstance(stored_primary_entries, list):
        try:
            primary_entries = _normalize_weighted_entries(
                stored_primary_entries,
                name_key="keyword",
                value_key="weight",
                label="主关键词",
            )
        except ValueError:
            # Reading a hand-edited document remains best-effort for the WebUI
            # inspection page. The worker performs the authoritative fail-closed
            # validation when it loads the same document.
            primary_entries = []
    else:
        primary_entries = [
            {"keyword": keyword, "weight": legacy_primary_weight}
            for keyword in pk.get("keywords", [])
            if isinstance(keyword, str) and keyword.strip()
        ]
    flat["primary_keyword_entries"] = primary_entries
    flat["primary_keywords"] = [entry["keyword"] for entry in primary_entries]
    flat["primary_keyword_weight"] = legacy_primary_weight
    flat["enable_reference_extraction"] = kw.get("enable_reference_extraction", False)

    ref = kw.get("reference_keywords_config", {})
    flat["max_reference_keywords"] = ref.get("max_keywords", 10)
    flat["similarity_threshold"] = ref.get("similarity_threshold", 0.75)

    wd = ref.get("weight_distribution", {})
    hi = wd.get("high_importance", {})
    flat["ref_weight_high"] = hi.get("weight", 1.0)
    flat["ref_count_high"] = hi.get("count", 3)
    mi = wd.get("medium_importance", {})
    flat["ref_weight_medium"] = mi.get("weight", 0.2)
    flat["ref_count_medium"] = mi.get("count", 5)
    lo = wd.get("low_importance", {})
    flat["ref_weight_low"] = lo.get("weight", 0.1)
    flat["ref_count_low"] = lo.get("count", 2)
    flat["research_context"] = kw.get("research_context", "")

    # Scoring
    sc = config.get("scoring_settings", {})
    krs = sc.get("keyword_relevance_score", {})
    flat["max_score_per_keyword"] = krs.get("max_score_per_keyword", 10)
    ab = sc.get("author_bonus", {})
    if not isinstance(ab, dict):
        ab = {}
    flat["enable_author_bonus"] = ab.get("enabled", False)
    legacy_author_points = ab.get("bonus_points", 5.0)
    stored_author_entries = ab.get("entries")
    if isinstance(stored_author_entries, list):
        try:
            author_entries = _normalize_weighted_entries(
                stored_author_entries,
                name_key="author",
                value_key="points",
                label="作者加分",
            )
        except ValueError:
            author_entries = []
    else:
        author_entries = [
            {"author": author, "points": legacy_author_points}
            for author in ab.get("expert_authors", [])
            if isinstance(author, str) and author.strip()
        ]
    flat["author_bonus_entries"] = author_entries
    flat["expert_authors"] = [entry["author"] for entry in author_entries]
    flat["author_bonus_points"] = legacy_author_points
    ps = sc.get("passing_score_formula", {})
    flat["passing_score_base"] = ps.get("base_score", 5.0)
    flat["passing_score_weight_coefficient"] = ps.get("weight_coefficient", 3.0)
    strategy = sc.get("strategy")
    if isinstance(strategy, dict):
        flat["score_strategy_explicit"] = True
        flat["score_strategy"] = strategy.get("id", "core_relevance_v2")
        flat["core_relevance_threshold"] = strategy.get("core_relevance_threshold", 6.0)
        flat["core_keyword_min_score"] = strategy.get("core_keyword_min_score", 7.0)
        flat["reference_ranking_weight"] = strategy.get("reference_ranking_weight", 0.25)
        flat["learned_weight_dampening"] = strategy.get("learned_weight_dampening", 0.5)
        flat["learned_term_weight_cap"] = strategy.get("learned_term_weight_cap", 2.0)
    else:
        # Absence is a meaningful legacy policy rather than an invitation to
        # silently reinterpret an existing user's historical threshold.
        flat["score_strategy_explicit"] = False
        flat["score_strategy"] = "legacy_weighted_keyword_v1"
        flat["core_relevance_threshold"] = 6.0
        flat["core_keyword_min_score"] = 7.0
        flat["reference_ranking_weight"] = 0.25
        flat["learned_weight_dampening"] = 0.5
        flat["learned_term_weight_cap"] = 2.0
    flat["include_all_in_report"] = sc.get("include_all_in_report", True)

    # Paths have no dedicated visible controls today (apart from the daily
    # SQLite path in Advanced).  They still need to participate in the flat
    # save contract: otherwise pressing “保存所有更改” on an unrelated tab
    # replaces a user's custom data/report/history roots with hard-coded
    # defaults.
    paths = config.get("paths", {})
    if not isinstance(paths, dict):
        paths = {}
    flat["data_dir"] = paths.get("data_dir", "data")
    flat["reference_pdfs"] = paths.get("reference_pdfs", "data/reference_pdfs")
    flat["reports"] = paths.get("reports", "data/reports")
    flat["downloaded_pdfs"] = paths.get(
        "downloaded_pdfs", "data/downloaded_pdfs"
    )
    flat["history_dir"] = paths.get("history_dir", "data/history")
    flat["history_file"] = paths.get("history_file")

    # Keyword tracker
    kt = config.get("keyword_tracker", {})
    flat["keyword_tracker_enabled"] = kt.get("enabled", True)
    flat["keyword_db_path"] = kt.get("database", {}).get("path", "data/keywords/keywords.db")
    norm = kt.get("normalization", {})
    flat["keyword_normalization_enabled"] = norm.get("enabled", True)
    flat["keyword_normalization_batch_size"] = norm.get("batch_size", 25)
    flat["keyword_normalization_llm_role"] = norm.get("llm_role", "cheap")
    flat["keyword_trend_default_days"] = kt.get("trend_view", {}).get("default_days", 30)
    charts = kt.get("charts", {})
    flat["keyword_chart_top_n"] = charts.get("bar_chart", {}).get("top_n", 15)
    flat["keyword_trend_top_n"] = charts.get("trend_chart", {}).get("top_n", 5)
    rpt = kt.get("report", {})
    flat["keyword_report_enabled"] = rpt.get("enabled", True)
    flat["keyword_report_frequency"] = rpt.get("frequency", "weekly")

    # Notifications
    nt = config.get("notifications", {})
    flat["notifications_enabled"] = nt.get("enabled", False)
    flat["notify_on_success"] = nt.get("on_success", True)
    flat["notify_on_failure"] = nt.get("on_failure", True)
    flat["notify_attach_reports"] = nt.get("attach_reports", False)
    flat["notification_top_n"] = nt.get("top_n", 5)
    ch = nt.get("channels", {})
    flat["notify_email_enabled"] = ch.get("email", {}).get("enabled", False)
    flat["notify_wechat_enabled"] = ch.get("wechat_work", {}).get("enabled", False)
    flat["notify_dingtalk_enabled"] = ch.get("dingtalk", {}).get("enabled", False)
    flat["notify_telegram_enabled"] = ch.get("telegram", {}).get("enabled", False)
    flat["notify_slack_enabled"] = ch.get("slack", {}).get("enabled", False)
    flat["notify_generic_webhook_enabled"] = ch.get("generic_webhook", {}).get("enabled", False)

    # Retry
    rt = config.get("retry", {})
    flat["retry_max_attempts"] = rt.get("max_attempts", 3)
    flat["retry_min_wait"] = rt.get("min_wait", 2)
    flat["retry_max_wait"] = rt.get("max_wait", 30)

    # Logging
    lg = config.get("logging", {})
    flat["log_rotation_type"] = lg.get("rotation_type", "time")
    flat["log_keep_days"] = lg.get("keep_days", 30)

    # Concurrency
    cc = config.get("concurrency", {})
    flat["concurrency_enabled"] = cc.get("enabled", False)
    flat["concurrency_workers"] = cc.get("workers", 3)

    # LLM request pool
    lp = config.get("llm_request_pool", {})
    flat["llm_request_pool_enabled"] = lp.get("enabled", True)
    flat["llm_requests_per_minute"] = lp.get("requests_per_minute", 30)
    flat["llm_request_pool_log_slow_wait_seconds"] = lp.get("log_slow_wait_seconds", 5.0)

    # LLM timeout / retry hardening
    llm = config.get("llm", {})
    flat["llm_timeout_seconds"] = llm.get("timeout_seconds", 300.0)
    flat["llm_sdk_max_retries"] = llm.get("sdk_max_retries", 1)
    flat["llm_retry_max_attempts"] = llm.get("retry_max_attempts", 5)
    flat["llm_retry_min_wait"] = llm.get("retry_min_wait", 5)
    flat["llm_retry_max_wait"] = llm.get("retry_max_wait", 120)

    # Daily research
    dr = config.get("daily_research", {})
    flat["daily_enable_deep_analysis"] = dr.get("enable_deep_analysis", True)
    # Kept in the flat compatibility contract so older callers may still pass
    # the argument back to build_config_dict; SQLite itself is no longer
    # optional and new config files omit the obsolete switch.
    flat["daily_research_persistence_enabled"] = True
    flat["daily_max_papers_per_run"] = dr.get("max_papers_per_run", 200)
    flat["daily_run_time"] = dr.get("run_time", "12:00")
    flat["daily_research_db_path"] = dr.get(
        "db_path", "data/daily_research/daily_research.db"
    )

    favorites = config.get("favorites", {})
    if not isinstance(favorites, dict):
        favorites = {}
    flat["auto_favorite_qualified_papers"] = favorites.get(
        "auto_favorite_qualified_papers", True
    )

    # Older config files controlled historical maintenance with the daily
    # cap. Keep that effective value when they are displayed/saved once.
    history_maintenance = config.get("history_maintenance", {})
    if not isinstance(history_maintenance, dict):
        history_maintenance = {}
    flat["history_maintenance_max_papers_per_run"] = history_maintenance.get(
        "max_papers_per_run", flat["daily_max_papers_per_run"]
    )

    # Legacy history import
    legacy_history = config.get("legacy_history", {})
    if not isinstance(legacy_history, dict):
        legacy_history = {}
    flat["legacy_import_full_repair_enabled"] = legacy_history.get(
        "full_repair_enabled", False
    )

    # PDF parser
    pp = config.get("pdf_parser", {})
    flat["pdf_parser_mode"] = pp.get("mode", "pymupdf")
    flat["mineru_model_version"] = pp.get("mineru_model_version", "pipeline")
    flat["mineru_poll_interval"] = pp.get("poll_interval", 3)
    flat["mineru_poll_timeout"] = pp.get("poll_timeout", 300)
    flat["pdf_download_max_bytes"] = pp.get("download_max_bytes", 50 * 1024 * 1024)

    # Report
    rs = config.get("report_settings", {})
    flat["enable_html_report"] = rs.get("enable_html_report", True)
    flat["enable_markdown_report"] = rs.get("enable_markdown_report", True)

    # Auto-update
    au = config.get("auto_update", {})
    flat["auto_update_enabled"] = au.get("enabled", True)

    # Token tracking
    tt = config.get("token_tracking", {})
    flat["token_tracking_enabled"] = tt.get("enabled", True)

    # Proxy
    px = config.get("proxy", {})
    flat["proxy_enabled"] = px.get("enabled", False)
    flat["proxy_url"] = px.get("url", "")
    flat["proxy_no_proxy"] = px.get("no_proxy", "localhost,127.0.0.1")
    px_scope = px.get("scope", {})
    flat["proxy_arxiv"] = px_scope.get("arxiv", True)
    flat["proxy_openalex"] = px_scope.get("openalex", False)
    flat["proxy_huggingface_papers"] = px_scope.get("huggingface_papers", False)
    flat["proxy_semantic_scholar"] = px_scope.get("semantic_scholar", False)
    flat["proxy_llm_api"] = px_scope.get("llm_api", False)
    flat["proxy_notifications"] = px_scope.get("notifications", False)
    flat["proxy_webdav"] = px_scope.get("webdav", True)
    flat["proxy_update_check"] = px_scope.get("update_check", False)

    # WebDAV
    wd = config.get("webdav", {})
    flat["webdav_enabled"] = wd.get("enabled", False)
    flat["webdav_remote_path"] = wd.get("remote_path", "/arxiv-daily-researcher/")
    flat["webdav_sync_mode"] = wd.get("sync_mode", "after_report")
    flat["webdav_cron_schedule"] = wd.get("cron_schedule", "0 23 * * *")
    flat["webdav_sync_configs"] = wd.get("sync_configs", True)
    flat["webdav_sync_history"] = wd.get("sync_history", True)
    flat["webdav_sync_keywords"] = wd.get("sync_keywords", True)
    flat["webdav_sync_reports"] = wd.get("sync_reports", False)

    # Database backup
    bk = config.get("backup", {})
    flat["backup_enabled"] = bk.get("enabled", True)
    try:
        from utils.backup import (
            LOCAL_BACKUP_RETENTION_DAYS,
            LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
            validate_local_backup_retention_days,
            validate_local_backup_same_day_max_count,
        )

        flat["backup_local_retention_days"] = validate_local_backup_retention_days(
            bk.get("local_retention_days", LOCAL_BACKUP_RETENTION_DAYS)
        )
        flat["backup_local_same_day_max_count"] = (
            validate_local_backup_same_day_max_count(
                bk.get("same_day_max_count", LOCAL_BACKUP_SAME_DAY_MAX_COUNT)
            )
        )
    except (ImportError, ValueError):
        # A manually edited invalid value should not prevent the WebUI from
        # opening; show the safe default so the next save repairs it.
        flat["backup_local_retention_days"] = 7
        flat["backup_local_same_day_max_count"] = 0

    # Trend research
    tr = config.get("trend_research", {})
    flat["trend_default_date_range_days"] = tr.get("default_date_range_days", 365)
    flat["trend_max_results"] = tr.get("max_results", 500)
    flat["trend_sort_order"] = tr.get("sort_order", "ascending")
    flat["trend_report_position"] = tr.get("report_position", "end")
    flat["trend_generate_tldr"] = tr.get("generate_tldr", True)
    flat["trend_tldr_batch_size"] = tr.get("tldr_batch_size", 10)
    flat["trend_output_formats"] = tr.get("output_formats", ["markdown", "html"])
    # v4.1 起综合分析始终执行；对旧配置中的空数组同样返回规范值。
    flat["trend_enabled_skills"] = ["comprehensive_analysis"]
    flat["trend_analysis_prompt"] = tr.get("analysis_prompt", "")

    return flat


# ==================== Validation ====================


def validate_llm_connection(api_key: str, base_url: str, model_name: str) -> Tuple[bool, str]:
    """Test LLM connection with a minimal request. Returns (success, message)."""
    if not api_key or not base_url or not model_name:
        return False, "API Key, Base URL, and Model Name are all required."

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=15)
        # 直接调用而非 worker 的请求池：WebUI 镜像不携带 worker 的 config 栈，
        # 而一次性的连通性测试也不需要并发限流。
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
        )
        return True, f"Connection successful! Model: {response.model}"
    except ImportError:
        return False, "openai package not installed. Cannot test connection."
    except Exception as e:
        return False, f"Connection failed: {e}"


def validate_smtp_connection(
    host: str, port: int, user: str, password: str, use_tls: bool = True
) -> Tuple[bool, str]:
    """Test SMTP connection. Returns (success, message)."""
    if not host or not user:
        return False, "SMTP host and user are required."

    try:
        import smtplib

        if use_tls:
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(host, port, timeout=10)

        if user and password:
            server.login(user, password)
        server.quit()
        return True, "SMTP connection successful!"
    except Exception as e:
        return False, f"SMTP connection failed: {e}"


def validate_mineru_connection(api_key: str) -> Tuple[bool, str]:
    """
    验证 MinerU API Token 有效性。

    MinerU v4 API 没有独立的账户信息接口，通过向 /extract/task 提交一个
    无效 URL（空字符串），根据返回的错误码区分"token 无效"与"参数错误"：
    - HTTP 401/403 或 code=A0202/A0211 → Token 无效/已过期
    - code=-500/-10002 等参数错误 → Token 有效（服务器认证通过，只是参数不合法）
    - code=0 → 不应出现（空 URL 不会被接受）

    参数:
        api_key: MinerU API Token

    返回:
        Tuple[bool, str]: (是否成功, 详细信息)
    """
    if not api_key or api_key.strip() == "":
        return False, "MinerU API Key 为空，请先填写 Token。"

    try:
        import urllib.request as urlreq
        import urllib.error as urlerr
        import json as _json

        url = "https://mineru.net/api/v4/extract/task"
        # 提交一个空 URL 的探测请求：token 有效时服务器会返回参数错误；token 无效时返回认证错误
        payload = _json.dumps({"url": "", "model_version": "pipeline"}).encode("utf-8")
        req = urlreq.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlreq.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8")
                data = _json.loads(body)
        except urlerr.HTTPError as http_err:
            if http_err.code in (401, 403):
                return False, "❌ Token 无效或已过期（HTTP 401/403），请检查 MINERU_API_KEY。"
            # 其他 HTTP 错误（如 400）也可能携带 JSON body
            try:
                body = http_err.read().decode("utf-8")
                data = _json.loads(body)
            except Exception:
                return False, f"❌ 无法连接 MinerU API: HTTP {http_err.code}"

        code = str(data.get("code", ""))
        msg = data.get("msg") or data.get("message") or ""

        # Token 认证失败的错误码
        if code in ("A0202", "A0211"):
            detail = "Token 已过期，请重新申请" if code == "A0211" else "Token 错误，请检查 MINERU_API_KEY"
            return False, f"❌ {detail}（code={code}）"

        # 参数错误（-500、-10002 等）说明 Token 本身是有效的，只是 URL 为空被拒
        # code=0 理论上不应出现（空 URL 无法提交成功）
        # 其他任何响应也视为"能够到达服务器且认证通过"
        return True, (
            "✅ MinerU Token 有效，API 连接正常。\n"
            "（注：MinerU 暂未提供账户信息查询接口，无法显示过期时间和剩余额度）"
        )

    except Exception as e:
        return False, f"⚠️ 无法连接 MinerU API: {e}"


def validate_openalex_connection(api_key: str) -> Tuple[bool, str]:
    """Test OpenAlex with one inexpensive, read-only API request.

    OpenAlex officially accepts either an ``Authorization: Bearer`` header or
    an ``api_key`` query parameter.  Use the header here so a diagnostic
    request never puts the credential in a URL that a proxy/logger could
    retain.  The normal source uses the same header-based form.
    """
    import urllib.error as urlerr
    import urllib.parse as urlparse
    import urllib.request as urlreq

    clean_key = (api_key or "").strip()
    params = {"per-page": "1", "select": "id"}

    request = urlreq.Request(
        "https://api.openalex.org/works?" + urlparse.urlencode(params),
        headers={
            "Accept": "application/json",
            "User-Agent": "ArxivDailyResearcher/2.0",
            **({"Authorization": f"Bearer {clean_key}"} if clean_key else {}),
        },
    )
    try:
        with urlreq.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                return False, "OpenAlex 返回了无法识别的响应，未确认连接。"
            remaining = response.headers.get("X-RateLimit-Remaining")
            if clean_key:
                suffix = f"，当前日额度剩余 {remaining}" if remaining else ""
                return True, f"✅ OpenAlex API Key 有效，连接正常{suffix}。"
            return True, "✅ OpenAlex API 可访问（当前未配置 API Key，使用匿名额度）。"
    except urlerr.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "❌ OpenAlex API Key 无效、已撤销或无权访问。"
        if exc.code == 429:
            return False, "⚠️ OpenAlex 已达到请求速率或今日额度，请稍后再试或检查 API Key 配额。"
        return False, f"❌ OpenAlex API 返回 HTTP {exc.code}。"
    except urlerr.URLError as exc:
        return False, f"⚠️ 无法连接 OpenAlex API: {getattr(exc, 'reason', exc)}"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"⚠️ OpenAlex 连接测试失败: {exc}"


def validate_semantic_scholar_connection(api_key: str) -> Tuple[bool, str]:
    """Test Semantic Scholar with a single known-paper metadata request."""
    import urllib.error as urlerr
    import urllib.request as urlreq

    clean_key = (api_key or "").strip()
    request = urlreq.Request(
        "https://api.semanticscholar.org/graph/v1/paper/ARXIV:1706.03762?fields=title",
        headers={
            "Accept": "application/json",
            "User-Agent": "ArxivDailyResearcher/2.0",
            **({"x-api-key": clean_key} if clean_key else {}),
        },
    )
    try:
        with urlreq.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("title"), str):
                return False, "Semantic Scholar 返回了无法识别的响应，未确认连接。"
            if clean_key:
                return True, "✅ Semantic Scholar API Key 有效，连接正常。"
            return True, "✅ Semantic Scholar API 可访问（当前未配置 API Key，使用共享匿名额度）。"
    except urlerr.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "❌ Semantic Scholar API Key 无效、已撤销或无权访问。"
        if exc.code == 429:
            return False, (
                "⚠️ Semantic Scholar 当前被限流。无 Key 时这是共享匿名额度；"
                "有 Key 时初始配额为每秒 1 次；应用会自动限速，请稍后重试或检查 Key 状态。"
            )
        return False, f"❌ Semantic Scholar API 返回 HTTP {exc.code}。"
    except urlerr.URLError as exc:
        return False, f"⚠️ 无法连接 Semantic Scholar API: {getattr(exc, 'reason', exc)}"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"⚠️ Semantic Scholar 连接测试失败: {exc}"
