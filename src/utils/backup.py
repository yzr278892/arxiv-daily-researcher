"""gzip-compressed SQLite backups with local rotation and WebDAV mirroring.

The daily-research database is the sole durable authority for the pending
queue, reader preferences and usage statistics. A backup takes a consistent
snapshot through SQLite's backup API (never a raw copy of a live WAL file)
and compresses it before any network transfer. Local copies can cap the
number of snapshots made today (zero keeps all of them), then compact each
earlier calendar day to its newest snapshot before applying the configured
age window (seven days by default; zero keeps the compacted daily history
forever).
The WebDAV mirror is
incremental: an archive is uploaded only when the database content actually
changed since the last upload, and remote copies are never deleted, so the
remote directory is a permanent archive of every content state.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import zipfile
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKUP_PREFIX = "daily_research_"
BACKUP_SUFFIX = ".db.gz"
LOCAL_BACKUP_RETENTION_DAYS = 7
MIN_LOCAL_BACKUP_RETENTION_DAYS = 0
LOCAL_BACKUP_SAME_DAY_MAX_COUNT = 0
MIN_LOCAL_BACKUP_SAME_DAY_MAX_COUNT = 0
_GZIP_CHUNK_BYTES = 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_BACKUP_NAME_RE = re.compile(r"^daily_research_(\d{8}_\d{6})(?:_(\d+))?\.db\.gz$")
_UPLOAD_STATE_FILENAME = "webdav_upload_state.json"
_MAX_RESTORED_DATABASE_BYTES = 4 * 1024 * 1024 * 1024

_backup_lock = threading.Lock()


def validate_local_backup_retention_days(value: Any) -> int:
    """Return a safe local-backup retention window in whole days.

    ``0`` explicitly disables age expiry; dated archives from earlier days
    still compact to one newest copy per day. Positive values are age windows
    in days; no artificial upper bound is imposed. WebDAV retention remains
    intentionally unlimited and is unaffected.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_LOCAL_BACKUP_RETENTION_DAYS
    ):
        raise ValueError(
            "backup.local_retention_days 必须是 "
            f"大于或等于 {MIN_LOCAL_BACKUP_RETENTION_DAYS} 的整数（0 表示不按天数过期）"
        )
    return value


def validate_local_backup_same_day_max_count(value: Any) -> int:
    """Return a valid cap for snapshots created during the current day.

    ``0`` deliberately means unlimited, preserving the existing default
    behaviour. Positive values retain the newest N current-day snapshots.
    Earlier days are always compacted to one archive independently of this
    setting, and there is no artificial maximum for an operator-provided cap.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < MIN_LOCAL_BACKUP_SAME_DAY_MAX_COUNT
    ):
        raise ValueError(
            "backup.same_day_max_count 必须是 "
            f"大于或等于 {MIN_LOCAL_BACKUP_SAME_DAY_MAX_COUNT} 的整数"
            "（0 表示保留当天全部备份）"
        )
    return value


def backups_directory(data_dir: Path) -> Path:
    """Return the backup directory under the given data directory."""
    return Path(data_dir) / "backups"


def database_path(data_dir: Path) -> Path:
    """Return the canonical daily-research database path."""
    return Path(data_dir) / "daily_research" / "daily_research.db"


def _backup_database_path(data_dir: Path, database: Optional[Path]) -> Path:
    """Resolve an explicit configured SQLite path or the legacy default."""
    return Path(database) if database is not None else database_path(data_dir)


def list_local_backups(data_dir: Path) -> List[Dict[str, Any]]:
    """List rotated backup files newest-first with size and mtime."""
    directory = backups_directory(data_dir)
    if not directory.is_dir():
        return []
    entries = []
    for item in directory.iterdir():
        if not item.is_file() or not item.name.startswith(BACKUP_PREFIX):
            continue
        if not item.name.endswith(BACKUP_SUFFIX):
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": item.name,
                "path": str(item),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
            }
        )
    entries.sort(key=lambda entry: entry["name"], reverse=True)
    return entries


def _create_consistent_snapshot(database: Path, target: Path) -> None:
    source_conn = sqlite3.connect(str(database))
    snapshot_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(snapshot_conn)
    finally:
        snapshot_conn.close()
        source_conn.close()


def _compress_file(source: Path, target: Path) -> None:
    with open(source, "rb") as raw, gzip.open(target, "wb", compresslevel=6) as packed:
        while True:
            chunk = raw.read(_GZIP_CHUNK_BYTES)
            if not chunk:
                break
            packed.write(chunk)


def _backup_timestamp(name: str) -> Optional[datetime]:
    """Return the creation timestamp encoded in a backup file name."""
    match = _BACKUP_NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _backup_order_key(name: str) -> Optional[tuple[datetime, int, str]]:
    """Return filename order for choosing the newest same-day snapshot."""
    match = _BACKUP_NAME_RE.match(name)
    stamp = _backup_timestamp(name)
    if match is None or stamp is None:
        return None
    # A suffixed file is created after the base name in the same second.
    sequence = int(match.group(2) or 1)
    return stamp, sequence, name


def _rotate_local(
    directory: Path,
    retention_days: int,
    same_day_max_count: int = LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    *,
    now: Optional[datetime] = None,
) -> List[str]:
    """Prune expired archives and compact local snapshots by calendar day.

    The current calendar day keeps every snapshot by default; when
    ``same_day_max_count`` is positive, it instead keeps only its newest N
    archives. For yesterday and earlier, only the most recent dated archive
    of each calendar day remains. The configured age window is applied first;
    ``retention_days=0`` disables only age expiry, not per-day compaction.
    Files without a parseable timestamp (and future-dated files) are left
    untouched rather than guessed at or deleted.
    """
    retention_days = validate_local_backup_retention_days(retention_days)
    same_day_max_count = validate_local_backup_same_day_max_count(
        same_day_max_count
    )
    rotation_now = now or datetime.now()
    cutoff = (
        rotation_now - timedelta(days=retention_days)
        if retention_days > 0
        else None
    )
    removed = []
    current_day_entries: List[tuple[tuple[datetime, int, str], Path]] = []
    prior_day_entries: Dict[date, List[tuple[tuple[datetime, int, str], Path]]] = {}

    for item in directory.iterdir():
        if not item.is_file() or not item.name.endswith(BACKUP_SUFFIX):
            continue
        stamp = _backup_timestamp(item.name)
        if stamp is None:
            continue
        if cutoff is not None and stamp < cutoff:
            try:
                os.remove(item)
                removed.append(item.name)
            except OSError:
                continue
            continue
        if stamp.date() == rotation_now.date():
            key = _backup_order_key(item.name)
            if key is not None:
                current_day_entries.append((key, item))
            continue
        if stamp.date() > rotation_now.date():
            continue
        key = _backup_order_key(item.name)
        if key is not None:
            prior_day_entries.setdefault(stamp.date(), []).append((key, item))

    if same_day_max_count > 0:
        current_day_entries.sort(key=lambda entry: entry[0], reverse=True)
        for _key, item in current_day_entries[same_day_max_count:]:
            try:
                os.remove(item)
                removed.append(item.name)
            except OSError:
                continue

    for entries in prior_day_entries.values():
        entries.sort(key=lambda entry: entry[0], reverse=True)
        for _key, item in entries[1:]:
            try:
                os.remove(item)
                removed.append(item.name)
            except OSError:
                continue

    removed.sort()
    return removed


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as raw:
        while True:
            chunk = raw.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _upload_state_path(directory: Path) -> Path:
    return directory / _UPLOAD_STATE_FILENAME


def _load_upload_state(directory: Path) -> Dict[str, str]:
    try:
        data = json.loads(_upload_state_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_upload_state(directory: Path, state: Dict[str, str]) -> None:
    _upload_state_path(directory).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _remote_backup_names(webdav_sync: Any, remote_dir: str) -> Optional[List[str]]:
    """Return remote backup names, or ``None`` when the directory cannot be read."""
    try:
        entries = webdav_sync.client.list(remote_dir)
    except Exception:
        return None
    names = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "")
        else:
            name = str(entry or "")
        name = name.strip().strip("/").rsplit("/", 1)[-1]
        if name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX):
            names.append(name)
    return sorted(names, reverse=True)


def _upload_incremental_remote(
    webdav_sync: Any,
    local_path: Path,
    snapshot_hash: str,
    state_directory: Path,
    logger: Any = None,
) -> Dict[str, Any]:
    """Mirror one backup to WebDAV incrementally; best-effort.

    The upload is skipped when the remote already holds this exact file name
    or when the database content hash is unchanged since the last successful
    upload. Remote files are never deleted — the WebDAV directory is a
    permanent archive, not a rotation.
    """
    result: Dict[str, Any] = {
        "uploaded": False,
        "remote_path": None,
        "skipped_reason": None,
    }
    remote_dir = webdav_sync._remote("data/backups")
    if not webdav_sync._ensure_remote_dir(remote_dir + "/"):
        raise RuntimeError("无法创建 WebDAV 备份目录")
    remote_file = f"{remote_dir}/{local_path.name}"

    remote_names = _remote_backup_names(webdav_sync, remote_dir)
    if remote_names is not None and local_path.name in remote_names:
        # A previous run uploaded this archive but crashed before recording
        # the state; treat it as mirrored instead of re-uploading.
        result["skipped_reason"] = "already_on_remote"
        result["remote_path"] = remote_file
        return result

    state = _load_upload_state(state_directory)
    recorded_remote_name = state.get("remote_name")
    if (
        state.get("hash") == snapshot_hash
        and recorded_remote_name
        # A temporary list failure must not turn a healthy incremental backup
        # into a duplicate upload.  When listing succeeds, however, verify
        # that the last recorded remote archive still exists; otherwise a
        # manually deleted/lost WebDAV file must be repaired.
        and (remote_names is None or recorded_remote_name in remote_names)
    ):
        result["skipped_reason"] = "content_unchanged"
        result["remote_path"] = recorded_remote_name
        return result

    webdav_sync.client.upload_file(remote_file, str(local_path))
    _save_upload_state(
        state_directory,
        {"hash": snapshot_hash, "remote_name": local_path.name},
    )
    result["uploaded"] = True
    result["remote_path"] = remote_file
    return result


def create_backup(
    data_dir: Path,
    *,
    database: Optional[Path] = None,
    retention_days: int = LOCAL_BACKUP_RETENTION_DAYS,
    same_day_max_count: int = LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    webdav_sync: Any = None,
    logger: Any = None,
) -> Dict[str, Any]:
    """Create one compressed backup and compact/rotate local copies.

    ``webdav_sync`` may be any object exposing the small WebDAVSync surface
    (``client.upload_file``/``client.list``, ``_remote``, ``_ensure_remote_dir``);
    passing ``None`` keeps the backup local-only. An upload failure never
    discards the local snapshot. ``database`` lets installations that keep
    their SQLite file outside the conventional ``data_dir/daily_research``
    location snapshot the exact configured database while still storing local
    archives under ``data_dir/backups``. ``same_day_max_count`` caps only the
    current day's local snapshots; ``0`` retains all of them.
    """
    retention_days = validate_local_backup_retention_days(retention_days)
    same_day_max_count = validate_local_backup_same_day_max_count(
        same_day_max_count
    )
    if _backup_lock.locked():
        return {"created": False, "reason": "backup_already_running"}

    selected_database = _backup_database_path(data_dir, database)
    if not selected_database.exists():
        return {"created": False, "reason": "database_missing"}

    directory = backups_directory(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now()
    stamp = created_at.strftime("%Y%m%d_%H%M%S")

    with _backup_lock:
        snapshot_fd, snapshot_name = tempfile.mkstemp(
            dir=str(directory), prefix=".snapshot_", suffix=".sqlite"
        )
        os.close(snapshot_fd)
        snapshot_path = Path(snapshot_name)
        archive_path = directory / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
        if archive_path.exists():
            # 同一秒内的手动+自动备份不应互相覆盖；追加序号保证唯一。
            for sequence in range(2, 100):
                archive_path = directory / (
                    f"{BACKUP_PREFIX}{stamp}_{sequence}{BACKUP_SUFFIX}"
                )
                if not archive_path.exists():
                    break
        try:
            _create_consistent_snapshot(selected_database, snapshot_path)
            snapshot_hash = _file_sha256(snapshot_path)
            _compress_file(snapshot_path, archive_path)
        finally:
            snapshot_path.unlink(missing_ok=True)

        result: Dict[str, Any] = {
            "created": True,
            "path": str(archive_path),
            "name": archive_path.name,
            "size_bytes": archive_path.stat().st_size,
            "local_rotated": _rotate_local(
                directory,
                retention_days,
                same_day_max_count,
                now=created_at,
            ),
            "uploaded": False,
            "remote_path": None,
        }

        if webdav_sync is not None:
            try:
                result.update(
                    _upload_incremental_remote(
                        webdav_sync,
                        archive_path,
                        snapshot_hash,
                        directory,
                        logger=logger,
                    )
                )
            except Exception as exc:
                result["upload_error"] = str(exc)
                if logger:
                    logger.warning("[Backup] WebDAV 上传失败（本地备份已保留）: %s", exc)
        return result


def run_scheduled_backup(logger: Any = None) -> Optional[Dict[str, Any]]:
    """Worker hook after a daily run; honours the ``backup`` config section.

    备份始终先压缩再上传；只要 WebDAV 已启用并配置了凭据就镜像上传
    （增量：内容未变化时跳过），本地按当天数量上限和旧日期每日最新副本
    后再按配置的保留天数轮转。
    """
    from config import settings

    if not getattr(settings, "BACKUP_ENABLED", False):
        return None

    webdav_sync = None
    if getattr(settings, "WEBDAV_ENABLED", False):
        from utils.webdav_sync import create_sync_client

        webdav_sync = create_sync_client()

    retention_days = validate_local_backup_retention_days(
        getattr(
            settings,
            "BACKUP_LOCAL_RETENTION_DAYS",
            LOCAL_BACKUP_RETENTION_DAYS,
        )
    )
    try:
        configured_same_day_max_count = getattr(
            settings, "BACKUP_LOCAL_SAME_DAY_MAX_COUNT"
        )
    except (AttributeError, KeyError):
        # Third-party integrations and old test doubles may not yet expose
        # the new setting; preserve the historical unlimited-current-day
        # behaviour until their configuration is refreshed.
        configured_same_day_max_count = LOCAL_BACKUP_SAME_DAY_MAX_COUNT
    same_day_max_count = validate_local_backup_same_day_max_count(
        configured_same_day_max_count
    )
    return create_backup(
        Path(settings.DATA_DIR),
        database=Path(settings.DAILY_RESEARCH_DB_PATH),
        retention_days=retention_days,
        same_day_max_count=same_day_max_count,
        webdav_sync=webdav_sync,
        logger=logger,
    )


# ─── 手动导入 / 导出（都是压缩包，自动识别内容）─────────────────────────────

_SQLITE_HEADER = b"SQLite format 3\x00"
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


def export_backup_zip_to_file(
    data_dir: Path, *, database: Optional[Path] = None
) -> tuple[Path, str]:
    """把当前数据库的一致性快照打包为临时 zip 文件，返回 (路径, 文件名)。

    数据库可达数百 MB；内存版导出会把整个压缩包再持有一份字节拷贝。
    面板改用流式响应发送该临时文件，结束后由调用方删除。
    """
    selected_database = _backup_database_path(data_dir, database)
    if not selected_database.exists():
        raise FileNotFoundError("数据库不存在，无法导出")

    zip_fd, zip_name = tempfile.mkstemp(suffix=".zip")
    os.close(zip_fd)
    zip_path = Path(zip_name)
    snapshot_path: Path | None = None
    try:
        snapshot_fd, snapshot_name = tempfile.mkstemp(suffix=".sqlite")
        os.close(snapshot_fd)
        snapshot_path = Path(snapshot_name)
        _create_consistent_snapshot(selected_database, snapshot_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot_path, arcname="daily_research.db")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return zip_path, f"daily_research_export_{stamp}.zip"
    except BaseException:
        zip_path.unlink(missing_ok=True)
        raise
    finally:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)


def export_backup_zip(
    data_dir: Path, *, database: Optional[Path] = None
) -> tuple[bytes, str]:
    """把当前数据库的一致性快照打包为 zip，返回 (压缩包字节, 文件名)。"""
    zip_path, filename = export_backup_zip_to_file(data_dir, database=database)
    try:
        return zip_path.read_bytes(), filename
    finally:
        zip_path.unlink(missing_ok=True)


def _copy_restored_database(source, destination) -> int:
    """Copy an extracted SQLite image without allowing decompression bombs."""
    total = 0
    while chunk := source.read(_GZIP_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_RESTORED_DATABASE_BYTES:
            raise ValueError("解压后的数据库超过 4 GB 限制。")
        destination.write(chunk)
    return total


def _extract_database_to_path(data: bytes | Path, filename: str, target: Path) -> str:
    """Stream the selected zip/gzip/raw SQLite member to a staging file."""
    input_path = Path(data) if isinstance(data, Path) else None
    zip_source = input_path if input_path is not None else io.BytesIO(data)
    with target.open("wb") as destination:
        if zipfile.is_zipfile(zip_source):
            with zipfile.ZipFile(zip_source) as archive:
                members = [info for info in archive.infolist() if not info.is_dir()]
                pool = [
                    info for info in members
                    if Path(info.filename).name == "daily_research.db"
                    or Path(info.filename).suffix.lower() in _DB_SUFFIXES
                ]
                if not pool:
                    raise ValueError("压缩包里没有找到数据库文件（.db/.sqlite/.sqlite3）")
                member = sorted(pool, key=lambda info: info.filename)[0]
                if member.file_size > _MAX_RESTORED_DATABASE_BYTES:
                    raise ValueError("解压后的数据库超过 4 GB 限制。")
                with archive.open(member) as source:
                    _copy_restored_database(source, destination)
                return member.filename

        source = input_path.open("rb") if input_path is not None else io.BytesIO(data)
        with source:
            header = source.read(2)
            source.seek(0)
            if header == b"\x1f\x8b":
                with gzip.GzipFile(fileobj=source) as unpacked:
                    _copy_restored_database(unpacked, destination)
            else:
                _copy_restored_database(source, destination)
    return filename


def restore_backup_archive(
    data_dir: Path,
    data: bytes | Path,
    filename: str = "import",
    *,
    database: Optional[Path] = None,
) -> Dict[str, Any]:
    """把导入的压缩包恢复为当前数据库。

    自动识别 zip（取 daily_research.db 或首个 .db/.sqlite 成员）、gzip
    （本地轮转备份格式）与原始 SQLite 文件。导入前做完整性校验；原数据库
    先通过一致性快照存档（连同 WAL 中已提交的内容），绝不删除数据。
    """
    selected_database = _backup_database_path(data_dir, database)
    selected_database.parent.mkdir(parents=True, exist_ok=True)
    verify_fd, verify_name = tempfile.mkstemp(
        dir=selected_database.parent, prefix=".restore.", suffix=".sqlite"
    )
    os.close(verify_fd)
    verify_path = Path(verify_name)
    try:
        try:
            source = _extract_database_to_path(data, filename, verify_path)
            size_bytes = verify_path.stat().st_size
            with verify_path.open("rb") as handle:
                if handle.read(16) != _SQLITE_HEADER:
                    raise ValueError(f"导入内容不是有效的 SQLite 数据库（来源：{source}）")
            conn = sqlite3.connect(str(verify_path))
            try:
                row = conn.execute("PRAGMA quick_check").fetchone()
            finally:
                conn.close()
        except (zipfile.BadZipFile, EOFError, zlib.error, sqlite3.DatabaseError) as exc:
            raise ValueError(f"备份文件损坏或无法解压：{exc}") from exc
        if not row or str(row[0]).lower() != "ok":
            detail = row[0] if row else "unknown"
            raise ValueError(f"导入数据库完整性校验未通过：{detail}")

        archived: Optional[Path] = None
        if selected_database.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived = backups_directory(data_dir) / f"pre_import_{stamp}.db"
            archived.parent.mkdir(parents=True, exist_ok=True)
            _create_consistent_snapshot(selected_database, archived)
        # The verified file is already on the database filesystem. Replace it
        # directly instead of keeping a second full-size copy in memory/disk.
        os.replace(verify_path, selected_database)
        for suffix in ("-wal", "-shm"):
            Path(str(selected_database) + suffix).unlink(missing_ok=True)

        return {
            "restored": True,
            "source_member": source,
            "size_bytes": size_bytes,
            "archived_previous": str(archived) if archived else None,
        }
    finally:
        verify_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(verify_path) + suffix).unlink(missing_ok=True)
