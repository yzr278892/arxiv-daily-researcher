import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.webui_trigger import (  # noqa: E402
    TriggerValidationError,
    build_main_command,
    build_trigger_payload,
    enqueue_trigger,
    execute_trigger_request,
    next_eligible_trigger_request,
    read_trigger_payload,
    rotate_restart_request_markers,
    rotate_trigger_statuses,
    sanitize_task_error_summary,
    trigger_status_directory,
    validate_trigger_payload,
)


class _CompletedProcess:
    def __init__(self, return_code: int):
        self.return_code = return_code
        self.pid = 4321

    def wait(self, timeout=None):
        return self.return_code

    def poll(self):
        return self.return_code


class _OutputProcess(_CompletedProcess):
    def __init__(self, return_code: int, output: str):
        super().__init__(return_code)
        self.stdout = io.StringIO(output)


class WebUITriggerTests(unittest.TestCase):
    def test_retry_reference_survives_trigger_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = enqueue_trigger(
                Path(directory),
                "legacy_import",
                retry_of="a" * 32,
                full_repair=True,
            )
            payload = read_trigger_payload(path)

        self.assertEqual(payload["retry_of"], "a" * 32)
        self.assertEqual(payload["args"], {"full_repair": True})

    def test_daily_request_is_atomic_and_has_no_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            self.assertTrue(request_path.is_file())
            self.assertEqual(list(request_path.parent.glob("*.tmp")), [])
            payload = read_trigger_payload(request_path)
            self.assertEqual(payload["mode"], "daily_research")
            self.assertEqual(payload["args"], {})
            self.assertEqual(oct(request_path.stat().st_mode & 0o777), "0o600")


    def test_legacy_import_accepts_optional_full_repair_switch(self):
        payload = build_trigger_payload("legacy_import")
        self.assertEqual(payload["mode"], "legacy_import")
        self.assertEqual(payload["args"], {})
        command = build_main_command(payload, Path("/worker"))
        self.assertEqual(command[:4], [sys.executable, "/worker/main.py", "--mode", "legacy_import"])
        full = build_trigger_payload("legacy_import", full_repair=True)
        self.assertEqual(full["args"], {"full_repair": True})
        self.assertIn("--legacy-full-repair", build_main_command(full, Path("/worker")))
        light = build_trigger_payload("legacy_import", full_repair=False)
        self.assertEqual(light["args"], {"full_repair": False})
        self.assertIn("--no-legacy-full-repair", build_main_command(light, Path("/worker")))
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("legacy_import", full_repair="yes")
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("legacy_import", anything=1)

    def test_sqlite_history_maintenance_modes_have_no_arguments(self):
        for mode in ("history_data_repair", "history_omission_scan"):
            payload = build_trigger_payload(mode)
            self.assertEqual(payload["args"], {})
            command = build_main_command(payload, Path("/worker"))
            self.assertEqual(command[-1], mode)
            with self.assertRaises(TriggerValidationError):
                build_trigger_payload(mode, unsafe=True)

    def test_trend_request_uses_argument_list_and_preserves_quoted_phrase(self):
        payload = build_trigger_payload(
            "trend_research",
            keywords=["quantum error correction", "surface code"],
            date_from="2025-01-01",
            date_to="2025-12-31",
            categories=["quant-ph", "cs.AI"],
            sort_order="descending",
            max_results=123,
        )
        command = build_main_command(payload, Path("/worker"))
        self.assertEqual(command[:4], [sys.executable, "/worker/main.py", "--mode", "trend_research"])
        keywords_index = command.index("--keywords")
        self.assertEqual(command[keywords_index + 1 : keywords_index + 3], [
            "quantum error correction",
            "surface code",
        ])
        self.assertIn("--categories", command)
        self.assertIn("quant-ph", command)
        self.assertIn("123", command)

    def test_trend_analysis_prompt_is_optional_bounded_and_forwarded(self):
        base = dict(
            keywords=["quantum"],
            sort_order="ascending",
            max_results=10,
        )
        # 缺省：不带 --analysis-prompt
        command = build_main_command(
            build_trigger_payload("trend_research", **base), Path("/worker")
        )
        self.assertNotIn("--analysis-prompt", command)

        prompt = "请重点分析纠错码实验进展，按主题分节输出。" * 20
        payload = build_trigger_payload("trend_research", analysis_prompt=prompt, **base)
        self.assertEqual(payload["args"]["analysis_prompt"], prompt.strip())
        command = build_main_command(payload, Path("/worker"))
        prompt_index = command.index("--analysis-prompt")
        self.assertEqual(command[prompt_index + 1], prompt)

        with self.assertRaises(TriggerValidationError):
            build_trigger_payload(
                "trend_research", analysis_prompt="x" * 8001, **base
            )
        with self.assertRaises(TriggerValidationError):
            build_trigger_payload("trend_research", analysis_prompt=42, **base)

    def test_invalid_request_is_rejected_before_a_command_can_be_built(self):
        payload = {
            "schema_version": 1,
            "request_id": "00000000-0000-0000-0000-000000000001",
            "created_at": "now",
            "mode": "trend_research",
            "args": {
                "keywords": ["valid"],
                "categories": ["quant-ph; rm -rf /"],
                "sort_order": "ascending",
                "max_results": 10,
            },
        }
        with self.assertRaises(TriggerValidationError):
            validate_trigger_payload(payload)

    def test_failed_or_malformed_request_is_consumed_and_records_durable_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            claimed_path = request_path.with_suffix(".running")
            os.replace(request_path, claimed_path)
            (root / "main.py").write_text("# worker placeholder\n", encoding="utf-8")

            with patch("utils.webui_trigger.subprocess.Popen", return_value=_CompletedProcess(1)):
                self.assertEqual(execute_trigger_request(claimed_path, project_root=root), 1)

            self.assertFalse(claimed_path.exists())
            statuses = list(trigger_status_directory(data_dir).glob("*.json"))
            self.assertEqual(len(statuses), 1)
            status = json.loads(statuses[0].read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "failed")
            self.assertEqual(status["return_code"], 1)
            self.assertTrue(status.get("started_at"))
            self.assertTrue(status.get("updated_at"))

            malformed = claimed_path.with_name("malformed.running")
            malformed.write_text("not json", encoding="utf-8")
            self.assertEqual(execute_trigger_request(malformed, project_root=root), 1)
            self.assertFalse(malformed.exists())
            statuses = list(trigger_status_directory(data_dir).glob("*.json"))
            self.assertEqual(len(statuses), 2)
            self.assertIn("rejected", {json.loads(path.read_text())["state"] for path in statuses})

    def test_failed_request_records_sanitized_stage_summary_from_worker_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            claimed_path = request_path.with_suffix(".running")
            os.replace(request_path, claimed_path)
            (root / "main.py").write_text("# worker placeholder\n", encoding="utf-8")
            output = (
                "2026-08-27 10:00:00 | INFO | Main | >>> 阶段3: 从数据源抓取论文\n"
                "2026-08-27 10:01:00 | ERROR | Main | 任务失败: "
                "api_key=supersecret https://hooks.example.test/path?token=leak\n"
            )

            with patch(
                "utils.webui_trigger.subprocess.Popen",
                return_value=_OutputProcess(1, output),
            ):
                self.assertEqual(execute_trigger_request(claimed_path, project_root=root), 1)

            status = json.loads(
                next(trigger_status_directory(data_dir).glob("*.json")).read_text(
                    encoding="utf-8"
                )
            )
            summary = status["error_summary"]
            self.assertIn("阶段", summary)
            self.assertIn("<凭据已隐藏>", summary)
            self.assertIn("<链接已隐藏>", summary)
            self.assertNotIn("supersecret", summary)
            self.assertNotIn("https://", summary)

    def test_error_summary_sanitizer_bounds_request_like_text(self):
        summary = sanitize_task_error_summary(
            "password: hidden-value; Bearer abcdefghijklmnopqrstuvwxyz "
            "https://example.test/webhook?token=leak "
            + "x" * 2000
        )
        self.assertLessEqual(len(summary), 420)
        self.assertNotIn("hidden-value", summary)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", summary)
        self.assertNotIn("https://", summary)

    def test_trigger_status_and_restart_audits_are_rotated_by_count_and_age(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            status_dir = trigger_status_directory(data_dir)
            status_dir.mkdir(parents=True)
            status_names = [f"{index:032x}.json" for index in range(1, 4)]
            for index, name in enumerate(status_names, start=1):
                path = status_dir / name
                path.write_text('{"state":"succeeded"}\n', encoding="utf-8")
                os.utime(path, (index, index))

            removed = rotate_trigger_statuses(
                data_dir, max_records=2, retention_days=0
            )
            self.assertEqual(removed, [status_names[0]])
            self.assertEqual(
                {path.name for path in status_dir.glob("*.json")},
                set(status_names[1:]),
            )

            restart_names = [
                f"restart_worker.request.done-20260101T00000{index}"
                for index in range(1, 4)
            ]
            queue_dir = data_dir / "run" / "webui_triggers"
            for index, name in enumerate(restart_names, start=1):
                path = queue_dir / name
                path.write_text("requested\n", encoding="utf-8")
                os.utime(path, (index, index))

            removed = rotate_restart_request_markers(
                data_dir, max_records=2, retention_days=0
            )
            self.assertEqual(removed, [restart_names[0]])
            self.assertEqual(
                {
                    path.name
                    for path in queue_dir.glob("restart_worker.request.done-*")
                },
                set(restart_names[1:]),
            )

            # Filename timestamps are audit labels; expiry intentionally uses
            # filesystem age so manually restored records remain meaningful.
            for name in restart_names[1:]:
                os.utime(queue_dir / name, None)

            expired = queue_dir / "restart_worker.request.done-20200101T000000"
            expired.write_text("old\n", encoding="utf-8")
            old_timestamp = 1
            os.utime(expired, (old_timestamp, old_timestamp))
            self.assertEqual(
                rotate_restart_request_markers(
                    data_dir, max_records=0, retention_days=1
                ),
                [expired.name],
            )

    def test_pid_file_is_removed_after_worker_exits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "data"
            request_path = enqueue_trigger(data_dir, "daily_research")
            claimed_path = request_path.with_suffix(".running")
            os.replace(request_path, claimed_path)
            (root / "main.py").write_text("# worker placeholder\n", encoding="utf-8")
            pid_file = data_dir / "run" / "webui_triggered.pid"

            with patch("utils.webui_trigger.subprocess.Popen", return_value=_CompletedProcess(0)):
                self.assertEqual(
                    execute_trigger_request(claimed_path, project_root=root, pid_file=pid_file), 0
                )
            self.assertFalse(pid_file.exists())

    def test_normal_request_passes_waiting_history_maintenance(self):
        """A history request outside its window cannot block normal research."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            history = enqueue_trigger(data_dir, "legacy_import", full_repair=False)
            normal = enqueue_trigger(data_dir, "daily_research")

            selected = next_eligible_trigger_request(
                data_dir,
                now=datetime(2026, 8, 31, 12, 0),
                history_schedule=("time_window", "00:00", "06:00"),
            )

        self.assertEqual(selected, normal)
        self.assertNotEqual(selected, history)

    def test_history_maintenance_waits_for_idle_and_respects_time_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            request = enqueue_trigger(data_dir, "history_data_repair")

            with patch(
                "utils.webui_trigger._worker_has_active_lock", return_value=True
            ):
                self.assertIsNone(
                    next_eligible_trigger_request(
                        data_dir,
                        now=datetime(2026, 8, 31, 1, 0),
                        history_schedule=("idle", "00:00", "06:00"),
                    )
                )

            self.assertIsNone(
                next_eligible_trigger_request(
                    data_dir,
                    now=datetime(2026, 8, 31, 12, 0),
                    history_schedule=("time_window", "00:00", "06:00"),
                )
            )
            self.assertEqual(
                next_eligible_trigger_request(
                    data_dir,
                    now=datetime(2026, 8, 31, 1, 0),
                    history_schedule=("time_window", "00:00", "06:00"),
                ),
                request,
            )

    def test_invalid_queued_request_is_still_selected_for_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            queue_dir = data_dir / "run" / "webui_triggers"
            queue_dir.mkdir(parents=True)
            malformed = queue_dir / "00000000_invalid.json"
            malformed.write_text("not json", encoding="utf-8")
            enqueue_trigger(data_dir, "daily_research")

            selected = next_eligible_trigger_request(
                data_dir,
                history_schedule=("idle", "00:00", "06:00"),
            )

        self.assertEqual(selected, malformed)


if __name__ == "__main__":
    unittest.main()


class StopRequestTests(unittest.TestCase):
    def test_monitor_stop_requests_signals_matching_child(self):
        import subprocess
        import threading
        import time

        from utils import webui_trigger

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            # 子进程模拟 main.py：把 SIGTERM 当中断处理，以 130 退出。
            child_code = (
                "import signal, sys, time\n"
                "signal.signal(signal.SIGTERM, lambda *a: sys.exit(130))\n"
                "time.sleep(60)\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            monitor = threading.Thread(
                target=webui_trigger._monitor_stop_requests,
                args=(child, data_dir),
                daemon=True,
            )
            monitor.start()
            time.sleep(0.5)

            webui_trigger.request_stop(data_dir, child.pid)

            child.wait(timeout=15)
            monitor.join(timeout=5)
            self.assertEqual(child.returncode, 130)
            # 停止请求被消费，不残留。
            self.assertEqual(
                list(webui_trigger.stop_request_directory(data_dir).glob("stop_*.json")),
                [],
            )

    def test_request_stop_writes_atomic_json_with_pid(self):
        from utils import webui_trigger

        with tempfile.TemporaryDirectory() as temp_dir:
            target = webui_trigger.request_stop(Path(temp_dir), 4242)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["pid"], 4242)
            self.assertEqual(target.name, "stop_4242.json")


class SkippedBusyMappingTests(unittest.TestCase):
    def test_exit_75_maps_to_skipped_busy_status(self):
        """被锁跳过的触发不得伪装成 succeeded。"""
        from utils import webui_trigger
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            from utils.webui_trigger import trigger_directory

            requests = trigger_directory(data_dir)
            requests.mkdir(parents=True, exist_ok=True)
            request_path = requests / "20260101T000000000000Z_c0ffee0000000000000000000000eeee.json"
            webui_trigger._atomic_write_json(
                request_path,
                {
                    "schema_version": 1,
                    "request_id": "c0ffee0000000000000000000000eeee",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "mode": "daily_research",
                    "args": {},
                },
            )
            claimed = request_path.with_suffix(".running")
            request_path.rename(claimed)

            # 伪造一个总是以 75 退出的 main.py。
            with patch.object(
                webui_trigger.subprocess, "Popen"
            ) as popen:
                popen.return_value.returncode = 75
                popen.return_value.poll.return_value = 75
                popen.return_value.pid = 4242
                popen.return_value.wait.return_value = 75
                with patch.object(webui_trigger.threading, "Thread"):
                    rc = webui_trigger.execute_trigger_request(
                        claimed, project_root=Path.cwd()
                    )
            self.assertEqual(rc, 75)
            status_dir = trigger_status_directory(data_dir)
            status_files = list(status_dir.glob("c0ffee0000000000000000000000eeee.json"))
            self.assertEqual(len(status_files), 1)
            state = json.loads(status_files[0].read_text(encoding="utf-8"))["state"]
            self.assertEqual(state, "skipped_busy")
