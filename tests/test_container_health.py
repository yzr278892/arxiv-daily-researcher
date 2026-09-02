"""Container health checks stay local, permission-aware and task-safe."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.container_health import (  # noqa: E402
    ContainerHealthError,
    _check_worker_processes,
    _check_sqlite,
    _check_trigger_consumer,
    _check_writable_directory,
    _lightweight_worker_checks,
    _parse_args,
    _worker_checks,
)


class ContainerHealthTests(unittest.TestCase):
    def test_manual_worker_mode_does_not_require_a_cron_daemon(self):
        with patch.dict(os.environ, {"MODE": "manual"}, clear=False), patch(
            "utils.container_health._process_named",
            side_effect=AssertionError("manual mode must not probe cron"),
        ):
            _check_worker_processes("adr")

    def test_default_worker_health_check_uses_lightweight_liveness_checks(self):
        with patch("utils.container_health._check_worker_processes"), patch(
            "utils.container_health._drop_to_runtime_user"
        ), patch("utils.container_health._lightweight_worker_checks") as lightweight:
            _worker_checks("adr")

        lightweight.assert_called_once_with()

    def test_full_worker_diagnostic_remains_an_explicit_cli_option(self):
        self.assertFalse(_parse_args(["worker"]).full)
        self.assertTrue(_parse_args(["worker", "--full"]).full)

    def test_lightweight_worker_check_keeps_runtime_paths_and_queue_healthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            project_root = Path(temporary)
            data_dir = project_root / "data"
            with patch("utils.container_health.PROJECT_ROOT", project_root), patch(
                "utils.container_health._check_writable_directory"
            ) as writable, patch(
                "utils.container_health._check_trigger_consumer"
            ) as trigger_consumer:
                _lightweight_worker_checks()

        self.assertEqual(
            {call.args[0] for call in writable.call_args_list},
            {
                data_dir,
                project_root / "logs",
                project_root / "configs",
                project_root / "runtime",
            },
        )
        trigger_consumer.assert_called_once_with(data_dir)

    def test_writable_directory_uses_and_removes_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _check_writable_directory(directory)
            self.assertEqual(list(directory.iterdir()), [])

    def test_sqlite_quick_check_accepts_valid_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE state (value TEXT)")
            _check_sqlite(database)

    def test_sqlite_quick_check_rejects_corrupt_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.db"
            database.write_bytes(b"not a sqlite database")
            with self.assertRaises(ContainerHealthError):
                _check_sqlite(database)

    def test_fresh_watcher_heartbeat_is_healthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            queue = data / "run" / "webui_triggers"
            (queue / "status").mkdir(parents=True)
            (queue / ".watcher-heartbeat").touch()
            _check_trigger_consumer(data, max_age_seconds=5)

    def test_stale_heartbeat_allows_live_claimed_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            queue = data / "run" / "webui_triggers"
            (queue / "status").mkdir(parents=True)
            heartbeat = queue / ".watcher-heartbeat"
            heartbeat.touch()
            old = time.time() - 120
            os.utime(heartbeat, (old, old))
            (queue / "request.running").write_text("{}", encoding="utf-8")
            (data / "run" / "webui_triggered.pid").write_text(
                "PID=123, started=2026-08-27T00:00:00Z\n", encoding="utf-8"
            )
            with patch("utils.container_health._pid_alive", return_value=True):
                _check_trigger_consumer(data, max_age_seconds=5)

    def test_stale_idle_watcher_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            queue = data / "run" / "webui_triggers"
            (queue / "status").mkdir(parents=True)
            heartbeat = queue / ".watcher-heartbeat"
            heartbeat.touch()
            old = time.time() - 120
            os.utime(heartbeat, (old, old))
            with self.assertRaises(ContainerHealthError):
                _check_trigger_consumer(data, max_age_seconds=5)


class WorkerEntrypointLayoutTests(unittest.TestCase):
    """Fresh bind mounts must include parents checked by worker health."""

    def test_entrypoint_creates_default_sqlite_parent_directories(self):
        project_root = Path(__file__).resolve().parents[1]
        entrypoint = (project_root / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        dockerfile = (project_root / "docker" / "Dockerfile").read_text(encoding="utf-8")

        for directory in (
            "/app/data/daily_research",
            "/app/data/keywords",
            "/app/data/reports/other_reports/supplement/markdown",
            "/app/data/reports/other_reports/supplement/html",
        ):
            self.assertIn(directory, entrypoint)
            self.assertIn(directory, dockerfile)

    def test_webui_trigger_uses_importable_module_path(self):
        project_root = Path(__file__).resolve().parents[1]
        entrypoint = (project_root / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn(
            "adr_run_as_user env PYTHONPATH=/app/src /usr/local/bin/python -m utils.webui_trigger",
            entrypoint,
        )
        self.assertNotIn("python /app/src/utils/webui_trigger.py", entrypoint)
        self.assertEqual(entrypoint.count("python -m utils.webui_trigger"), 1)
        self.assertIn('webui_trigger "$CLAIMED_FILE" --pid-file "$PID_FILE"', entrypoint)

    def test_trigger_selector_is_guarded_by_a_queued_json_check(self):
        project_root = Path(__file__).resolve().parents[1]
        entrypoint = (project_root / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("has_queued_trigger() {", entrypoint)
        self.assertIn('for request_path in "$TRIGGER_DIR"/*.json; do', entrypoint)
        watcher = entrypoint[entrypoint.index("trigger_watcher() {") :]
        guard_index = watcher.index("if has_queued_trigger; then")
        selector_index = watcher.index("REQUEST_FILE=$(webui_trigger")
        self.assertLess(guard_index, selector_index)
        self.assertIn(
            "--next-eligible-request --data-dir /app/data",
            watcher[guard_index : selector_index + 160],
        )


if __name__ == "__main__":
    unittest.main()
