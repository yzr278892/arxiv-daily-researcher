"""gzip 数据库备份：一致性快照、本地按日收敛、WebDAV 增量镜像。"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.backup import (  # noqa: E402
    BACKUP_SUFFIX,
    LOCAL_BACKUP_RETENTION_DAYS,
    LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
    _rotate_local,
    create_backup,
    list_local_backups,
    restore_backup_archive,
    run_scheduled_backup,
    validate_local_backup_retention_days,
    validate_local_backup_same_day_max_count,
)
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _seed_database(data_dir: Path) -> None:
    store = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
    store.set_app_state("seed", "v1")


def _rename_backup(directory: Path, name: str, days_ago: int) -> str:
    """Rename a local backup so its filename looks ``days_ago`` days old."""
    stamp = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d_%H%M%S")
    aged = f"daily_research_{stamp}{BACKUP_SUFFIX}"
    (directory / name).rename(directory / aged)
    return aged


def _write_dated_backup(
    directory: Path,
    stamp: datetime,
    *,
    sequence: int | None = None,
) -> str:
    """Create a lightweight archive-shaped file for local-rotation tests."""
    suffix = f"_{sequence}" if sequence is not None else ""
    name = f"daily_research_{stamp.strftime('%Y%m%d_%H%M%S')}{suffix}{BACKUP_SUFFIX}"
    (directory / name).write_bytes(b"fixture")
    return name


class _FakeWebDAVClient:
    def __init__(self, existing=()):
        self.remote_files = set(existing)
        self.uploads = []
        self.deletes = []

    def upload_file(self, remote_path, local_path):
        self.remote_files.add(remote_path.rsplit("/", 1)[-1])
        self.uploads.append((remote_path, local_path))

    def list(self, remote_dir):
        return sorted(self.remote_files)

    def delete(self, remote_path):
        self.remote_files.discard(remote_path.rsplit("/", 1)[-1])
        self.deletes.append(remote_path)


class _FakeWebDAVSync:
    def __init__(self, existing=()):
        self.client = _FakeWebDAVClient(existing)
        self.ensured = []

    def _remote(self, rel_path):
        return f"/remote/{rel_path}"

    def _ensure_remote_dir(self, remote_dir):
        self.ensured.append(remote_dir)
        return True


class BackupCreationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        _seed_database(self.data_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_backup_is_valid_gzip_sqlite(self):
        result = create_backup(self.data_dir)
        self.assertTrue(result["created"])
        backup_path = Path(result["path"])
        self.assertTrue(backup_path.name.endswith(BACKUP_SUFFIX))

        with gzip.open(backup_path, "rb") as packed:
            restored = backup_path.with_suffix(".restored")
            restored.write_bytes(packed.read())
        conn = sqlite3.connect(str(restored))
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            value = conn.execute(
                "SELECT value FROM app_state WHERE key = 'seed'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIn("daily_papers", tables)
        self.assertEqual(value, ("v1",))

    def test_local_rotation_drops_backups_older_than_one_week(self):
        fresh = create_backup(self.data_dir)
        aged = _rename_backup(
            self.data_dir / "backups", fresh["name"], LOCAL_BACKUP_RETENTION_DAYS + 1
        )
        create_backup(self.data_dir)

        survivors = {entry["name"] for entry in list_local_backups(self.data_dir)}
        self.assertNotIn(aged, survivors)
        self.assertEqual(len(survivors), 1)

    def test_local_rotation_keeps_backups_within_one_week(self):
        fresh = create_backup(self.data_dir)
        aged = _rename_backup(
            self.data_dir / "backups", fresh["name"], LOCAL_BACKUP_RETENTION_DAYS - 1
        )
        result = create_backup(self.data_dir)

        survivors = {entry["name"] for entry in list_local_backups(self.data_dir)}
        self.assertIn(aged, survivors)
        self.assertEqual(result["local_rotated"], [])

    def test_custom_retention_window_deletes_older_local_backups(self):
        fresh = create_backup(self.data_dir)
        aged = _rename_backup(self.data_dir / "backups", fresh["name"], 3)

        result = create_backup(self.data_dir, retention_days=2)

        survivors = {entry["name"] for entry in list_local_backups(self.data_dir)}
        self.assertNotIn(aged, survivors)
        self.assertIn(aged, result["local_rotated"])

    def test_zero_retention_keeps_each_prior_day_forever_after_compaction(self):
        fresh = create_backup(self.data_dir)
        aged = _rename_backup(self.data_dir / "backups", fresh["name"], 30)

        result = create_backup(self.data_dir, retention_days=0)

        self.assertIn(
            aged,
            {entry["name"] for entry in list_local_backups(self.data_dir)},
        )
        self.assertEqual(result["local_rotated"], [])

    def test_rotation_keeps_all_today_and_only_latest_archive_per_earlier_day(self):
        directory = self.data_dir / "backups"
        directory.mkdir()
        now = datetime.now().replace(microsecond=0)
        yesterday = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        today_first = _write_dated_backup(
            directory, now.replace(hour=0, minute=1, second=0)
        )
        today_second = _write_dated_backup(
            directory, now.replace(hour=1, minute=1, second=0)
        )
        yesterday_old = _write_dated_backup(
            directory, yesterday.replace(hour=8, minute=0, second=0)
        )
        yesterday_new = _write_dated_backup(
            directory, yesterday.replace(hour=20, minute=0, second=0)
        )
        yesterday_same_second_old = _write_dated_backup(
            directory, yesterday.replace(hour=21, minute=0, second=0)
        )
        yesterday_same_second_new = _write_dated_backup(
            directory,
            yesterday.replace(hour=21, minute=0, second=0),
            sequence=2,
        )
        two_days_old = _write_dated_backup(
            directory, two_days_ago.replace(hour=9, minute=0, second=0)
        )
        two_days_new = _write_dated_backup(
            directory, two_days_ago.replace(hour=10, minute=0, second=0)
        )

        result = create_backup(self.data_dir, retention_days=0)
        survivors = {entry["name"] for entry in list_local_backups(self.data_dir)}

        self.assertIn(today_first, survivors)
        self.assertIn(today_second, survivors)
        self.assertIn(yesterday_same_second_new, survivors)
        self.assertIn(two_days_new, survivors)
        self.assertNotIn(yesterday_old, survivors)
        self.assertNotIn(yesterday_new, survivors)
        self.assertNotIn(yesterday_same_second_old, survivors)
        self.assertNotIn(two_days_old, survivors)
        self.assertEqual(
            set(result["local_rotated"]),
            {
                yesterday_old,
                yesterday_new,
                yesterday_same_second_old,
                two_days_old,
            },
        )

    def test_rotation_limits_current_day_to_newest_requested_count(self):
        directory = self.data_dir / "backups"
        directory.mkdir()
        now = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
        first = _write_dated_backup(
            directory, now.replace(hour=8, minute=0, second=0)
        )
        second = _write_dated_backup(
            directory, now.replace(hour=9, minute=0, second=0)
        )
        third = _write_dated_backup(
            directory, now.replace(hour=10, minute=0, second=0)
        )

        removed = _rotate_local(
            directory,
            retention_days=0,
            same_day_max_count=2,
            now=now,
        )

        survivors = {entry["name"] for entry in list_local_backups(self.data_dir)}
        self.assertEqual(survivors, {second, third})
        self.assertEqual(removed, [first])

        # Zero remains the backward-compatible unlimited default.
        _write_dated_backup(directory, now.replace(hour=11, minute=0, second=0))
        self.assertEqual(
            _rotate_local(
                directory,
                retention_days=0,
                same_day_max_count=LOCAL_BACKUP_SAME_DAY_MAX_COUNT,
                now=now,
            ),
            [],
        )
        self.assertEqual(len(list_local_backups(self.data_dir)), 3)

    def test_retention_window_rejects_negative_and_non_integers(self):
        for invalid in (-1, True, "7"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "local_retention_days"):
                    validate_local_backup_retention_days(invalid)

    def test_same_day_max_count_rejects_negative_and_non_integers(self):
        for invalid in (-1, True, "3"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "same_day_max_count"):
                    validate_local_backup_same_day_max_count(invalid)

    def test_missing_database_reports_reason(self):
        empty = self.data_dir / "empty"
        empty.mkdir()
        result = create_backup(empty)
        self.assertFalse(result["created"])
        self.assertEqual(result["reason"], "database_missing")

    def test_webdav_upload_is_incremental_and_never_deletes_remote(self):
        fake = _FakeWebDAVSync(
            existing={
                "daily_research_20200101_000000.db.gz",
            }
        )
        first = create_backup(self.data_dir, webdav_sync=fake)
        self.assertTrue(first["created"])
        self.assertTrue(first["uploaded"])
        self.assertEqual(len(fake.client.uploads), 1)
        state = json.loads(
            (self.data_dir / "backups" / "webdav_upload_state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(state["remote_name"], first["name"])

        # 数据库未变化：跳过上传，远端一个文件都不删
        second = create_backup(self.data_dir, webdav_sync=fake)
        self.assertTrue(second["created"])
        self.assertFalse(second["uploaded"])
        self.assertEqual(second["skipped_reason"], "content_unchanged")
        self.assertEqual(len(fake.client.uploads), 1)

        # 数据库变化：重新上传，远端历史副本保留
        DailyResearchStore(
            self.data_dir / "daily_research" / "daily_research.db"
        ).set_app_state("seed", "v2")
        third = create_backup(self.data_dir, webdav_sync=fake)
        self.assertTrue(third["uploaded"])
        self.assertEqual(len(fake.client.uploads), 2)
        self.assertIn("daily_research_20200101_000000.db.gz", fake.client.remote_files)
        self.assertEqual(fake.client.deletes, [])

    def test_unchanged_snapshot_is_reuploaded_if_recorded_remote_copy_is_missing(self):
        fake = _FakeWebDAVSync()
        first = create_backup(self.data_dir, webdav_sync=fake)
        self.assertTrue(first["uploaded"])
        self.assertEqual(len(fake.client.uploads), 1)

        # The local state file still says this content was mirrored, but an
        # operator/loss removed the remote object.  Incremental mode must
        # repair the durable copy instead of claiming it is unchanged.
        fake.client.remote_files.clear()
        repaired = create_backup(self.data_dir, webdav_sync=fake)

        self.assertTrue(repaired["uploaded"])
        self.assertEqual(len(fake.client.uploads), 2)
        self.assertFalse(fake.client.deletes)

    def test_upload_failure_keeps_local_backup_and_state(self):
        fake = _FakeWebDAVSync()
        fake.client.upload_file = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("network down")
        )
        result = create_backup(self.data_dir, webdav_sync=fake)

        self.assertTrue(result["created"])
        self.assertFalse(result["uploaded"])
        self.assertIn("upload_error", result)
        self.assertTrue(Path(result["path"]).exists())
        self.assertFalse(
            (self.data_dir / "backups" / "webdav_upload_state.json").exists()
        )

    def test_no_temp_snapshot_left_behind(self):
        create_backup(self.data_dir)
        leftovers = [
            item.name
            for item in (self.data_dir / "backups").iterdir()
            if item.name.startswith(".snapshot_")
        ]
        self.assertEqual(leftovers, [])


class ScheduledBackupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        _seed_database(self.data_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def _install_settings(self, attrs):
        import config as config_module

        self._original_settings = config_module.settings
        holder = type("Holder", (), {"__getattr__": lambda self, name: attrs[name]})
        config_module.settings = holder()

    def tearDownSettings(self):
        import config as config_module

        if hasattr(self, "_original_settings"):
            config_module.settings = self._original_settings

    def test_disabled_returns_none(self):
        self._install_settings(
            {
                "BACKUP_ENABLED": False,
                "DATA_DIR": self.data_dir,
                "WEBDAV_ENABLED": False,
            }
        )
        try:
            self.assertIsNone(run_scheduled_backup())
        finally:
            self.tearDownSettings()

    def test_enabled_creates_local_backup(self):
        self._install_settings(
            {
                "BACKUP_ENABLED": True,
                "BACKUP_LOCAL_RETENTION_DAYS": LOCAL_BACKUP_RETENTION_DAYS,
                "DATA_DIR": self.data_dir,
                "DAILY_RESEARCH_DB_PATH": (
                    self.data_dir / "daily_research" / "daily_research.db"
                ),
                "WEBDAV_ENABLED": False,
            }
        )
        try:
            result = run_scheduled_backup()
        finally:
            self.tearDownSettings()
        self.assertIsNotNone(result)
        self.assertTrue(result["created"])
        self.assertFalse(result["uploaded"])

    def test_scheduled_backup_uses_configured_local_retention(self):
        fresh = create_backup(self.data_dir)
        aged = _rename_backup(self.data_dir / "backups", fresh["name"], 3)
        self._install_settings(
            {
                "BACKUP_ENABLED": True,
                "BACKUP_LOCAL_RETENTION_DAYS": 2,
                "DATA_DIR": self.data_dir,
                "DAILY_RESEARCH_DB_PATH": (
                    self.data_dir / "daily_research" / "daily_research.db"
                ),
                "WEBDAV_ENABLED": False,
            }
        )
        try:
            result = run_scheduled_backup()
        finally:
            self.tearDownSettings()

        self.assertTrue(result["created"])
        self.assertNotIn(
            aged,
            {entry["name"] for entry in list_local_backups(self.data_dir)},
        )


if __name__ == "__main__":
    unittest.main()


class BackupImportExportTests(unittest.TestCase):
    def test_explicit_database_path_is_used_for_snapshot_and_round_trip(self):
        import gzip as gzip_mod

        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "archives"
            database = Path(temp_dir) / "custom" / "papers.sqlite"
            store = DailyResearchStore(database)
            store.set_app_state("seed", "custom-path")

            created = create_backup(data_dir, database=database)
            self.assertTrue(created["created"])
            raw_database = gzip_mod.decompress(Path(created["path"]).read_bytes())
            restored_path = Path(temp_dir) / "restored.sqlite"
            restored_path.write_bytes(raw_database)
            restored = DailyResearchStore(restored_path)
            self.assertEqual(restored.get_app_state("seed"), "custom-path")

            store.set_app_state("seed", "changed")
            result = restore_backup_archive(
                data_dir,
                Path(created["path"]).read_bytes(),
                created["name"],
                database=database,
            )
            self.assertTrue(result["restored"])
            self.assertEqual(
                DailyResearchStore(database).get_app_state("seed"), "custom-path"
            )

    def test_export_zip_round_trip_restores_and_archives_previous(self):
        import io
        import zipfile

        from utils.backup import export_backup_zip, restore_backup_archive

        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            _seed_database(data_dir)

            bundle, filename = export_backup_zip(data_dir)
            self.assertTrue(filename.endswith(".zip"))
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                self.assertIn("daily_research.db", archive.namelist())

            # 在导出后写入新数据，导入应回滚到导出时的快照并存档当前库
            store = DailyResearchStore(
                data_dir / "daily_research" / "daily_research.db"
            )
            store.set_app_state("seed", "v2")

            result = restore_backup_archive(data_dir, bundle, filename)
            self.assertTrue(result["restored"])
            self.assertEqual(result["source_member"], "daily_research.db")
            self.assertIsNotNone(result["archived_previous"])

            restored = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
            self.assertEqual(restored.get_app_state("seed"), "v1")
            archived = sqlite3.connect(result["archived_previous"])
            archived_state = archived.execute(
                "SELECT value FROM app_state WHERE key='seed'"
            ).fetchone()[0]
            archived.close()
            self.assertEqual(archived_state, "v2")

    def test_restore_accepts_a_staged_upload_path(self):
        from utils.backup import export_backup_zip, restore_backup_archive

        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _seed_database(data_dir)
            bundle, filename = export_backup_zip(data_dir)
            upload = data_dir / "upload.zip"
            upload.write_bytes(bundle)
            store = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
            store.set_app_state("seed", "changed")

            result = restore_backup_archive(data_dir, upload, filename)

            self.assertEqual(result["source_member"], "daily_research.db")
            self.assertEqual(
                DailyResearchStore(data_dir / "daily_research" / "daily_research.db").get_app_state("seed"),
                "v1",
            )

    def test_restore_rejects_oversized_decompressed_database(self):
        from unittest.mock import patch
        from utils import backup

        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _seed_database(data_dir)
            archive = data_dir / "oversized.db.gz"
            archive.write_bytes(gzip.compress(b"SQLite format 3\x00" + b"x" * 200))
            with patch.object(backup, "_MAX_RESTORED_DATABASE_BYTES", 100):
                with self.assertRaisesRegex(ValueError, "超过 4 GB"):
                    backup.restore_backup_archive(data_dir, archive, archive.name)

    def test_restore_auto_detects_gzip_and_raw_sqlite(self):
        import gzip as gzip_mod

        from utils.backup import create_backup, restore_backup_archive

        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            _seed_database(data_dir)
            created = create_backup(data_dir)
            self.assertTrue(created["created"])

            with TemporaryDirectory() as other_dir:
                other = Path(other_dir)
                _seed_database(other)
                store = DailyResearchStore(other / "daily_research" / "daily_research.db")
                store.set_app_state("seed", "changed")

                packed = Path(created["path"]).read_bytes()  # 已是 gzip
                result = restore_backup_archive(
                    other, packed, created["name"]
                )
                self.assertTrue(result["restored"])
                restored = DailyResearchStore(
                    other / "daily_research" / "daily_research.db"
                )
                self.assertEqual(restored.get_app_state("seed"), "v1")

                raw = Path(created["path"]).read_bytes()
                raw_db = gzip_mod.decompress(raw)
                result2 = restore_backup_archive(other, raw_db, "daily_research.db")
                self.assertTrue(result2["restored"])
                self.assertEqual(
                    DailyResearchStore(
                        other / "daily_research" / "daily_research.db"
                    ).get_app_state("seed"),
                    "v1",
                )

    def test_restore_rejects_invalid_payload(self):
        from utils.backup import restore_backup_archive

        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(ValueError):
                restore_backup_archive(
                    Path(temp_dir), b"not a database at all", "junk.zip"
                )
            import io
            import zipfile

            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("readme.txt", "no db here")
            with self.assertRaises(ValueError):
                restore_backup_archive(
                    Path(temp_dir), buffer.getvalue(), "empty.zip"
                )

    def test_corrupt_sqlite_with_a_valid_header_keeps_the_live_database(self):
        with TemporaryDirectory() as directory:
            data_dir = Path(directory)
            _seed_database(data_dir)
            database = data_dir / "daily_research" / "daily_research.db"
            with self.assertRaisesRegex(ValueError, "备份文件损坏"):
                restore_backup_archive(
                    data_dir, b"SQLite format 3\x00" + b"broken data", "corrupt.db"
                )
            self.assertEqual(DailyResearchStore(database).get_app_state("seed"), "v1")
            self.assertEqual(list(database.parent.glob(".restore.*")), [])
