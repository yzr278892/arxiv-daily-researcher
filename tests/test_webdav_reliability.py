import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.run_lock import database_restore_activity_gate  # noqa: E402
from utils.webdav_sync import (  # noqa: E402
    DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS,
    WebDAVSync,
    after_report_sync_maintenance_entry,
    cron_schedule_matches,
    deliver_pending_after_report_syncs,
    normalize_webdav_base_url,
    normalize_webdav_remote_path,
    validate_cron_schedule,
)
from utils.webdav_scheduler import run_scheduled_webdav_sync  # noqa: E402


class _FakeClient:
    def __init__(self, remote_database: Path):
        self.remote_database = remote_database

    def download_file(self, _remote_file: str, local_file: str) -> None:
        Path(local_file).write_bytes(self.remote_database.read_bytes())


class _ConfigDownloadClient:
    def __init__(self, content: str):
        self.content = content

    def download_file(self, _remote_file: str, local_file: str) -> None:
        Path(local_file).write_text(self.content, encoding="utf-8")


class _ConfigUploadClient:
    def __init__(self):
        self.calls = []

    def upload_file(self, remote_file: str, local_file: str) -> None:
        self.calls.append((remote_file, local_file))


def _sync_shell(project_root: Path) -> WebDAVSync:
    """Create a WebDAVSync object without importing the optional client library."""
    sync = WebDAVSync.__new__(WebDAVSync)
    sync._project_root = project_root
    sync.remote_root = "arxiv-researcher"
    return sync


class _PropfindResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


class _PropfindSession:
    def __init__(self, response: _PropfindResponse):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class _WebDavLibraryClient:
    def __init__(self, options):
        self.options = options
        self.session = type("Session", (), {"proxies": {}})()


class _StreamingResponse:
    def __init__(self, status_code: int, content: bytes, headers=None):
        self.status_code = status_code
        self._content = content
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=1):
        del chunk_size
        yield self._content

    def close(self):
        self.closed = True


class _RequestSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


class _FakeDownloadSession:
    def __init__(self, remote_file: Path):
        self.remote_file = remote_file
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        content = self.remote_file.read_bytes()
        return _StreamingResponse(200, content, {"Content-Length": str(len(content))})


class _NeverPullClient:
    def pull(self, *_args, **_kwargs):
        raise AssertionError("untrusted WebDAV pull() must not be used")


class WebDAVReliabilityTests(unittest.TestCase):
    @staticmethod
    def _settings(**overrides):
        defaults = {
            "WEBDAV_ENABLED": True,
            "WEBDAV_SYNC_MODE": "after_report",
            "RETRY_MAX_ATTEMPTS": 3,
            "RETRY_MIN_WAIT": 1,
            "RETRY_MAX_WAIT": 4,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _store(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return DailyResearchStore(Path(temp_dir.name) / "daily.db")

    @staticmethod
    def _dav_directory_xml(remote_path: str, entries) -> bytes:
        """Build a minimal DAV Depth-1 response for controlled-download tests."""
        response_entries = [
            f"""
            <d:response>
              <d:href>/{remote_path}/</d:href>
              <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
            </d:response>
            """
        ]
        for name, is_collection, length in entries:
            resource_type = "<d:resourcetype><d:collection/></d:resourcetype>" if is_collection else "<d:resourcetype/>"
            length_element = "" if length is None else f"<d:getcontentlength>{length}</d:getcontentlength>"
            trailing = "/" if is_collection else ""
            response_entries.append(
                f"""
                <d:response>
                  <d:href>/{remote_path}/{name}{trailing}</d:href>
                  <d:propstat><d:prop>{resource_type}{length_element}</d:prop></d:propstat>
                </d:response>
                """
            )
        return (
            '<d:multistatus xmlns:d="DAV:">' + "".join(response_entries) + "</d:multistatus>"
        ).encode("utf-8")

    @staticmethod
    def _directory_sync_shell(root: Path, responses) -> WebDAVSync:
        sync = _sync_shell(root)
        sync._base_url = "https://dav.example.test"
        sync._http = _RequestSession(responses)
        sync.client = _NeverPullClient()
        return sync

    def test_report_finalization_atomically_queues_maintenance_task(self):
        store = self._store()
        run_id = store.start_run(0)
        entry = {
            "task_key": f"webdav_after_report:{run_id}",
            "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
        }

        store.finalize_report_delivery(run_id, {}, {}, maintenance_entries=[entry])
        task = store.get_maintenance_task(entry["task_key"])
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempt_count"], 0)

    def test_failed_webdav_upload_is_deferred_without_reopening_report_delivery(self):
        store = self._store()
        run_id = store.start_run(0)
        entry = {
            "task_key": f"webdav_after_report:{run_id}",
            "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
        }
        store.finalize_report_delivery(run_id, {}, {}, maintenance_entries=[entry])

        with patch("config.settings", self._settings()), patch(
            "utils.webdav_sync.sync_after_report", side_effect=RuntimeError("remote offline")
        ):
            summary = deliver_pending_after_report_syncs(store)

        self.assertEqual(summary, {"claimed": 1, "completed": 0, "deferred": 1})
        task = store.get_maintenance_task(entry["task_key"])
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["attempt_count"], 1)
        self.assertIn("remote offline", task["last_error"])
        with store._connect() as conn:
            run = conn.execute("SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(run["status"], "completed")

    def test_successful_webdav_upload_completes_only_its_maintenance_task(self):
        store = self._store()
        run_id = store.start_run(0)
        entry = {
            "task_key": f"webdav_after_report:{run_id}",
            "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
        }
        store.finalize_report_delivery(run_id, {}, {}, maintenance_entries=[entry])

        with patch("config.settings", self._settings()), patch(
            "utils.webdav_sync.sync_after_report", return_value={"success": 1, "total": 1, "results": {}}
        ):
            summary = deliver_pending_after_report_syncs(store)

        self.assertEqual(summary, {"claimed": 1, "completed": 1, "deferred": 0})
        task = store.get_maintenance_task(entry["task_key"])
        self.assertEqual(task["status"], "completed")
        self.assertIsNotNone(task["completed_at"])

    def test_maintenance_entry_respects_webdav_configuration(self):
        with patch("config.settings", self._settings()):
            entry = after_report_sync_maintenance_entry("run-1")
        self.assertEqual(entry["task_key"], "webdav_after_report:run-1")

        with patch("config.settings", self._settings(WEBDAV_ENABLED=False)):
            self.assertIsNone(after_report_sync_maintenance_entry("run-1"))

    def test_downloaded_sqlite_snapshot_is_checked_and_preserves_previous_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            local_database = data_dir / "daily_research" / "daily_research.db"
            remote_database = root / "remote.db"
            local_database.parent.mkdir(parents=True)

            with sqlite3.connect(local_database) as conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('local')")
            with sqlite3.connect(remote_database) as conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('remote')")

            sync = _sync_shell(root)
            sync.client = _FakeClient(remote_database)
            sync._base_url = "https://dav.example.test"
            sync._http = _FakeDownloadSession(remote_database)
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            self.assertTrue(sync._download_daily_research_snapshot(data_dir))
            with sqlite3.connect(local_database) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "remote")
            backup_path = local_database.with_name("daily_research.db.before_webdav_restore")
            with sqlite3.connect(backup_path) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "local")

    def test_invalid_download_does_not_replace_local_sqlite_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            local_database = data_dir / "daily_research" / "daily_research.db"
            remote_database = root / "remote.db"
            local_database.parent.mkdir(parents=True)

            with sqlite3.connect(local_database) as conn:
                conn.execute("CREATE TABLE marker(value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('local')")
            remote_database.write_text("not a sqlite database", encoding="utf-8")

            sync = _sync_shell(root)
            sync.client = _FakeClient(remote_database)
            sync._base_url = "https://dav.example.test"
            sync._http = _FakeDownloadSession(remote_database)
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            self.assertFalse(sync._download_daily_research_snapshot(data_dir))
            with sqlite3.connect(local_database) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "local")

    def test_downloaded_sqlite_waits_for_all_worker_modes_to_be_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            sync = _sync_shell(Path(directory))
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative
            with patch.object(sync, "_download_remote_file_to_path") as download:
                with database_restore_activity_gate(data_dir=data_dir):
                    self.assertFalse(sync._download_daily_research_snapshot(data_dir))
            download.assert_not_called()

    def test_invalid_webdav_config_download_keeps_the_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "runtime" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"old": true}\n', encoding="utf-8")

            sync = _sync_shell(root)
            sync.client = _ConfigDownloadClient("<html>not config</html>")
            sync._base_url = "https://dav.example.test"
            sync._http = _FakeDownloadSession(root / "invalid-config-response")
            (root / "invalid-config-response").write_text("<html>not config</html>", encoding="utf-8")
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            result = sync.download_configs()
            self.assertFalse(result["configs/config.json"])
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"old": true}\n')
            self.assertEqual(list(config_path.parent.glob("*.download")), [])

    def test_config_upload_uses_runtime_file_and_stable_remote_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "runtime" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"target_domains": {"domains": ["quant-ph"]}}\n', encoding="utf-8")

            sync = _sync_shell(root)
            client = _ConfigUploadClient()
            sync.client = client
            sync._ensure_remote_dir = lambda _remote: True
            sync._remote = lambda relative: f"remote/{relative}"

            self.assertEqual(sync.upload_configs(), {"configs/config.json": True})
            self.assertEqual(client.calls, [("remote/configs/config.json", str(config_path))])

    def test_history_scope_syncs_sqlite_only_and_leaves_legacy_json_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            legacy_history = data_dir / "history"
            legacy_history.mkdir(parents=True)
            (legacy_history / "arxiv_history.json").write_text("{}", encoding="utf-8")

            sync = _sync_shell(root)
            sync._data_dir = lambda: data_dir
            uploads = []
            sync._upload_daily_research_snapshot = lambda actual_data_dir: (
                uploads.append(actual_data_dir) or True
            )
            sync._upload_directory = lambda *_args, **_kwargs: self.fail(
                "normal SQLite history sync must not upload legacy data/history"
            )

            result = sync.upload_data(
                include_history=True,
                include_keywords=False,
                include_reports=False,
            )

            self.assertEqual(
                result,
                {"data/daily_research/daily_research.db": True},
            )
            self.assertEqual(uploads, [data_dir])
            self.assertTrue((legacy_history / "arxiv_history.json").exists())

    def test_history_scope_download_restores_sqlite_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            sync = _sync_shell(root)
            sync._data_dir = lambda: data_dir
            sync._check_remote = lambda remote: self.fail(
                f"legacy history directory must not be requested: {remote}"
            )
            restores = []
            sync._download_daily_research_snapshot = lambda actual_data_dir: (
                restores.append(actual_data_dir) or True
            )

            result = sync.download_data(
                include_history=True,
                include_keywords=False,
                include_reports=False,
            )

            self.assertEqual(
                result,
                {"data/daily_research/daily_research.db": True},
            )
            self.assertEqual(restores, [data_dir])
            self.assertFalse((data_dir / "history").exists())

    def test_history_snapshot_uses_custom_configured_sqlite_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            custom_database = root / "state" / "research.sqlite"
            sync = _sync_shell(root)

            with patch(
                "config.settings",
                SimpleNamespace(
                    DATA_DIR=data_dir,
                    DAILY_RESEARCH_DB_PATH=custom_database,
                ),
            ):
                self.assertEqual(
                    sync._daily_research_database_path(data_dir), custom_database
                )
            self.assertEqual(
                sync._daily_research_database_path(root / "other-data"),
                root / "other-data" / "daily_research" / "daily_research.db",
            )

    def test_unsafe_webdav_config_download_keeps_the_existing_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "runtime" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text('{"old": true}\n', encoding="utf-8")
            remote_content = '{paths: {reports: "../outside"}}'
            remote_file = root / "unsafe-config-response"
            remote_file.write_text(remote_content, encoding="utf-8")

            sync = _sync_shell(root)
            sync._base_url = "https://dav.example.test"
            sync._http = _FakeDownloadSession(remote_file)
            sync._check_remote = lambda _remote: True
            sync._remote = lambda relative: relative

            result = sync.download_configs()

            self.assertFalse(result["configs/config.json"])
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"old": true}\n')

    def test_controlled_directory_download_never_uses_client_pull(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            listing = self._dav_directory_xml("data/history", [("seen.json", False, 2)])
            sync = self._directory_sync_shell(
                root,
                [
                    _StreamingResponse(207, listing),
                    _StreamingResponse(200, b"{}", {"Content-Length": "2"}),
                ],
            )

            downloaded = sync._download_directory_safely("data/history", root / "history")

            self.assertEqual(downloaded, 2)
            self.assertEqual((root / "history" / "seen.json").read_bytes(), b"{}")
            self.assertEqual(sync._http.calls[0][0][0], "PROPFIND")
            self.assertEqual(sync._http.calls[1][0][0], "GET")

    def test_controlled_directory_download_rejects_traversal_before_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            listing = self._dav_directory_xml("data/history", [("%2e%2e", False, 4)])
            sync = self._directory_sync_shell(root, [_StreamingResponse(207, listing)])
            local_dir = root / "history"

            with self.assertRaisesRegex(ValueError, "不安全|目录外"):
                sync._download_directory_safely("data/history", local_dir)

            self.assertFalse((root / "outside.txt").exists())
            self.assertEqual(list(local_dir.iterdir()), [])

    def test_controlled_directory_download_rejects_cross_platform_filename_collisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            listing = self._dav_directory_xml(
                "data/history",
                [("State.json", False, 1), ("state.json", False, 1)],
            )
            sync = self._directory_sync_shell(root, [_StreamingResponse(207, listing)])

            with self.assertRaisesRegex(ValueError, "重复目录项"):
                sync._download_directory_safely("data/history", root / "history")

    def test_controlled_directory_download_rejects_server_length_mismatch_without_replacing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_dir = root / "history"
            local_dir.mkdir()
            target = local_dir / "state.json"
            target.write_text("old", encoding="utf-8")
            listing = self._dav_directory_xml("data/history", [("state.json", False, 5)])
            sync = self._directory_sync_shell(
                root,
                [
                    _StreamingResponse(207, listing),
                    _StreamingResponse(200, b"new", {"Content-Length": "3"}),
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "长度与 PROPFIND 声明不一致"):
                sync._download_directory_safely("data/history", local_dir)

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(local_dir.glob("*.download")), [])

    def test_controlled_directory_download_rejects_symlink_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_dir = root / "history"
            local_dir.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (local_dir / "state.json").symlink_to(outside)
            listing = self._dav_directory_xml("data/history", [("state.json", False, 2)])
            sync = self._directory_sync_shell(root, [_StreamingResponse(207, listing)])

            with self.assertRaisesRegex(ValueError, "符号链接"):
                sync._download_directory_safely("data/history", local_dir)

            self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_remote_root_and_base_url_reject_traversal_credentials_and_query_syntax(self):
        self.assertEqual(
            normalize_webdav_remote_path(" /research/physics/ "), "research/physics"
        )
        self.assertEqual(
            normalize_webdav_base_url("https://dav.example.test/dav/root/"),
            "https://dav.example.test/dav/root",
        )
        for unsafe_path in ("../other", "data/%2e%2e/private", "a/%252e%252e/b", "a?x=1"):
            with self.subTest(unsafe_path=unsafe_path):
                with self.assertRaisesRegex(ValueError, "WebDAV 远程路径"):
                    normalize_webdav_remote_path(unsafe_path)
        for unsafe_url in (
            "https://user:pass@dav.example.test/root",
            "https://dav.example.test/root?token=1",
            "https://dav.example.test/root#fragment",
        ):
            with self.subTest(unsafe_url=unsafe_url):
                with self.assertRaisesRegex(ValueError, "WebDAV URL"):
                    normalize_webdav_base_url(unsafe_url)

    def test_propfind_is_bounded_non_redirecting_and_closes_response(self):
        sync = WebDAVSync.__new__(WebDAVSync)
        sync._base_url = "https://dav.example.test/root"
        response = _PropfindResponse(207)
        sync._http = _PropfindSession(response)

        self.assertTrue(sync._check_remote("workspace/report"))
        args, kwargs = sync._http.calls[0]
        self.assertEqual(args, ("PROPFIND", "https://dav.example.test/root/workspace/report"))
        self.assertEqual(kwargs["headers"], {"Depth": "0"})
        self.assertEqual(kwargs["timeout"], DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(response.closed)

    def test_ensure_remote_dir_fails_when_mkdir_cannot_be_confirmed(self):
        sync = WebDAVSync.__new__(WebDAVSync)
        sync._check_remote = lambda _remote: False
        sync.client = type("Client", (), {"mkdir": lambda *_args, **_kwargs: None})()

        self.assertFalse(sync._ensure_remote_dir("workspace/reports/"))

    def test_constructor_sets_timeout_and_proxy_on_both_webdav_sessions(self):
        with patch.dict(
            sys.modules,
            {
                "webdav3": type("Module", (), {})(),
                "webdav3.client": type("Module", (), {"Client": _WebDavLibraryClient})(),
            },
        ):
            sync = WebDAVSync(
                "https://dav.example.test/root/",
                "user",
                "password",
                remote_path="research",
                proxy_url="http://proxy.example.test:3128",
            )

        expected_proxy = {
            "http": "http://proxy.example.test:3128",
            "https": "http://proxy.example.test:3128",
        }
        self.assertEqual(sync.client.options["webdav_timeout"], DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(sync.client.session.proxies, expected_proxy)
        self.assertEqual(sync._http.proxies, expected_proxy)

    def test_scheduled_cron_matching_supports_ranges_steps_names_and_cron_day_rules(self):
        self.assertTrue(
            cron_schedule_matches("*/15 8-18 * jan,jun mon-fri", datetime(2026, 6, 1, 9, 15))
        )
        self.assertFalse(
            cron_schedule_matches("*/15 8-18 * jan,jun mon-fri", datetime(2026, 6, 1, 9, 14))
        )
        self.assertTrue(cron_schedule_matches("0 8 2 * mon", datetime(2026, 6, 1, 8, 0)))
        self.assertTrue(cron_schedule_matches("0 8 2 * mon", datetime(2026, 6, 2, 8, 0)))
        self.assertEqual(validate_cron_schedule("@hourly"), "0 * * * *")
        with self.assertRaisesRegex(ValueError, "5 个字段"):
            validate_cron_schedule("0 8 * *")
        with self.assertRaisesRegex(ValueError, "超出范围"):
            validate_cron_schedule("61 8 * * *")

    def test_scheduled_sync_is_idempotent_and_retries_without_daily_report_state(self):
        store = self._store()
        settings = self._settings(
            WEBDAV_SYNC_MODE="scheduled",
            WEBDAV_CRON_SCHEDULE="0 23 * * *",
        )
        now = datetime(2026, 8, 12, 23, 0)
        calls = []

        def offline(_log):
            calls.append("offline")
            raise RuntimeError("remote offline")

        first = run_scheduled_webdav_sync(
            now,
            store=store,
            settings_override=settings,
            sync_callable=offline,
        )
        self.assertEqual(first["matched"], True)
        self.assertEqual(first["queued"], True)
        self.assertEqual(first["claimed"], 1)
        self.assertEqual(first["deferred"], 1)
        task_key = "webdav_scheduled:202608122300"
        failed_task = store.get_maintenance_task(task_key)
        self.assertEqual(failed_task["status"], "pending")
        self.assertEqual(failed_task["attempt_count"], 1)

        # A duplicate cron invocation in the same minute cannot queue or call
        # WebDAV again before the persisted retry delay is due.
        duplicate = run_scheduled_webdav_sync(
            now,
            store=store,
            settings_override=settings,
            sync_callable=offline,
        )
        self.assertEqual(duplicate["queued"], False)
        self.assertEqual(duplicate["claimed"], 0)
        self.assertEqual(calls, ["offline"])

        with store._connect() as conn:
            conn.execute(
                "UPDATE maintenance_outbox SET next_attempt_at = ? WHERE task_key = ?",
                ("2000-01-01T00:00:00", task_key),
            )

        succeeded = run_scheduled_webdav_sync(
            datetime(2026, 8, 12, 23, 1),
            store=store,
            settings_override=settings,
            sync_callable=lambda _log: {"results": {"snapshot": True}},
        )
        self.assertEqual(succeeded["matched"], False)
        self.assertEqual(succeeded["claimed"], 1)
        self.assertEqual(succeeded["completed"], 1)
        self.assertEqual(store.get_maintenance_task(task_key)["status"], "completed")

    def test_scheduled_sync_does_not_enqueue_when_mode_is_not_scheduled(self):
        store = self._store()
        settings = self._settings(WEBDAV_SYNC_MODE="after_report")
        result = run_scheduled_webdav_sync(
            datetime(2026, 8, 12, 23, 0),
            store=store,
            settings_override=settings,
            sync_callable=lambda _log: self.fail("should not sync"),
        )
        self.assertEqual(result, {
            "enabled": False,
            "matched": False,
            "queued": False,
            "claimed": 0,
            "completed": 0,
            "deferred": 0,
        })

    def test_scheduled_sync_keeps_existing_tasks_pending_after_mode_change(self):
        store = self._store()
        store.enqueue_maintenance_task(
            "webdav_scheduled:202608122300", {"task_type": "webdav_scheduled"}
        )
        settings = self._settings(WEBDAV_SYNC_MODE="manual")
        result = run_scheduled_webdav_sync(
            datetime(2026, 8, 12, 23, 1),
            store=store,
            settings_override=settings,
            sync_callable=lambda _log: self.fail("should not sync"),
        )
        self.assertEqual(result["claimed"], 0)
        self.assertEqual(
            store.get_maintenance_task("webdav_scheduled:202608122300")["status"], "pending"
        )


if __name__ == "__main__":
    unittest.main()
