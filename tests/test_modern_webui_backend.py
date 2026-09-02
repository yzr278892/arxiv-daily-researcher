"""Focused contract tests for the modern WebUI backend helpers."""

from __future__ import annotations

import os
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from modern_webui import backend
from utils.daily_research_store import DailyResearchStore
from utils.webui_trigger import enqueue_trigger, trigger_status_directory


class ModernBackendTests(unittest.TestCase):
    def test_analytics_windows_keep_rolling_and_calendar_ranges_distinct(self) -> None:
        now = datetime(2026, 8, 30, 15, 45, 30)
        start, end, bucket, key = backend._analytics_window("24h", now=now)
        self.assertEqual(start, datetime(2026, 8, 29, 15, 45, 30))
        self.assertEqual(end, now)
        self.assertEqual(bucket, "hour")
        self.assertEqual(key, "24h")

        start, end, bucket, key = backend._analytics_window("7d", now=now)
        self.assertEqual(start, datetime(2026, 8, 24))
        self.assertEqual(end, now)
        self.assertEqual(bucket, "day")
        self.assertEqual(key, "7d")

        start, end, bucket, key = backend._analytics_window(
            "custom", "2026-08-01", "2026-08-03", now=now
        )
        self.assertEqual(start, datetime(2026, 8, 1))
        self.assertEqual(end, datetime(2026, 8, 4))
        self.assertEqual(bucket, "day")
        self.assertEqual(key, "custom")

    def test_historical_token_import_groups_reports_and_preserves_report_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            reports_dir = data_dir / "reports"
            store = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
            stamp = "2024-01-02_08-30-00"
            daily_report = (
                reports_dir
                / "daily_research"
                / "markdown"
                / "arxiv"
                / f"ARXIV_Report_{stamp}.md"
            )
            second_source = (
                reports_dir
                / "daily_research"
                / "markdown"
                / "prl"
                / f"PRL_Report_{stamp}.md"
            )
            html_only = (
                reports_dir
                / "daily_research"
                / "html"
                / "arxiv"
                / "ARXIV_Report_2024-01-03_09-45-00.html"
            )
            trend_report = (
                reports_dir
                / "trend_research"
                / "markdown"
                / "quantum"
                / "2023-01-01_2024-01-04.md"
            )
            for path in (daily_report, second_source, html_only, trend_report):
                path.parent.mkdir(parents=True, exist_ok=True)
            daily_text = """## Token 消耗统计

- **总计**: 18 tokens（输入 10 / 输出 8）

| 模型 | 输入 | 输出 | 合计 |
|------|------|------|------|
| cheap | 4 | 2 | 6 |
| smart | 6 | 6 | 12 |
"""
            daily_report.write_text(daily_text, encoding="utf-8")
            second_source.write_text(daily_text, encoding="utf-8")
            html_only.write_text(
                "<p>Token 消耗: <strong>12</strong> tokens（输入 8 / 输出 4）</p>",
                encoding="utf-8",
            )
            trend_report.write_text(
                """## Token 消耗统计

- **总计**: 9 tokens（输入 3 / 输出 6）

*本报告由 ArXiv Daily Researcher 研究趋势模式生成 | 2024-01-04 10:15:00*
""",
                encoding="utf-8",
            )

            with patch.object(backend, "flat_config", return_value={}), patch.object(
                backend, "configured_reports_dir", return_value=reports_dir
            ), patch.object(backend, "configured_data_dir", return_value=data_dir), patch.object(
                backend, "open_store", return_value=store
            ):
                first = backend.import_historical_report_token_usage()
                second = backend.import_historical_report_token_usage()

            self.assertEqual(first["reports"], 3)
            self.assertEqual(first["imported"], 3)
            self.assertEqual(first["conflicted"], 0)
            self.assertEqual(second["imported"], 0)
            self.assertEqual(second["unchanged"], 3)
            self.assertEqual(
                store.get_token_usage_summary(),
                {
                    "prompt": 21,
                    "cached_prompt": 0,
                    "completion": 18,
                    "total": 39,
                    "runs": 3,
                },
            )
            self.assertEqual(
                store.get_token_usage_summary(
                    start_at=datetime(2024, 1, 2), end_at=datetime(2024, 1, 3)
                ),
                {
                    "prompt": 10,
                    "cached_prompt": 0,
                    "completion": 8,
                    "total": 18,
                    "runs": 1,
                },
            )
            by_model = {row["model"]: row["total"] for row in store.get_token_usage_by_model()}
            self.assertEqual(by_model["cheap"], 6)
            self.assertEqual(by_model["smart"], 12)
            self.assertEqual(by_model["historical_report"], 21)

    def test_historical_token_parser_preserves_cache_or_defaults_to_ordinary_input(self) -> None:
        cache_aware = backend._parse_historical_token_usage(
            Path("cache-aware.md"),
            """## Token 消耗统计

- **总计**: 30 tokens（普通输入 8 / 缓存输入 15 / 输出 7）

| 模型 | 普通输入 | 缓存输入 | 输出 | 合计 |
|------|----------|----------|------|------|
| smart | 8 | 15 | 7 | 30 |
""",
        )
        self.assertEqual(cache_aware["prompt"], 8)
        self.assertEqual(cache_aware["cached_prompt"], 15)
        self.assertEqual(cache_aware["completion"], 7)
        self.assertEqual(cache_aware["by_model"]["smart"]["cached_prompt"], 15)

        legacy = backend._parse_historical_token_usage(
            Path("legacy.md"),
            "- **总计**: 30 tokens（输入 23 / 输出 7）",
        )
        self.assertEqual(legacy["prompt"], 23)
        self.assertEqual(legacy["cached_prompt"], 0)
        self.assertEqual(legacy["by_model"]["historical_report"]["cached_prompt"], 0)

    def test_historical_token_import_skips_a_report_with_native_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            reports_dir = data_dir / "reports"
            report = (
                reports_dir
                / "daily_research"
                / "markdown"
                / "arxiv"
                / "ARXIV_Report_2024-01-02_08-30-00.md"
            )
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                "## Token 消耗统计\n\n- **总计**: 10 tokens（输入 6 / 输出 4）\n",
                encoding="utf-8",
            )
            store = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
            run_id = store.start_run(0)
            store.complete_run(
                run_id,
                {"arxiv": "/app/data/reports/daily_research/markdown/arxiv/ARXIV_Report_2024-01-02_08-30-00.md"},
            )
            store.record_token_usage(
                run_id,
                {"native": {"prompt": 6, "completion": 4}},
                recorded_at=datetime(2024, 1, 2, 8, 30),
            )

            with patch.object(backend, "flat_config", return_value={}), patch.object(
                backend, "configured_reports_dir", return_value=reports_dir
            ), patch.object(backend, "configured_data_dir", return_value=data_dir), patch.object(
                backend, "open_store", return_value=store
            ):
                result = backend.import_historical_report_token_usage()

            self.assertEqual(result["reports"], 1)
            self.assertEqual(result["already_recorded"], 1)
            self.assertEqual(result["imported"], 0)
            self.assertEqual(store.get_token_usage_summary()["runs"], 1)

    def test_public_settings_never_returns_a_secret_value(self) -> None:
        with patch.object(backend, "flat_config", return_value={"daily_run_time": "12:00"}), patch.object(
            backend,
            "read_env",
            return_value={
                "CHEAP_LLM__API_KEY": "private-value",
                "CHEAP_LLM__MODEL_NAME": "small-model",
            },
        ):
            payload = backend.public_settings()

        self.assertNotIn("CHEAP_LLM__API_KEY", payload["env"])
        self.assertTrue(payload["secrets"]["CHEAP_LLM__API_KEY"])
        self.assertEqual(payload["env"]["CHEAP_LLM__MODEL_NAME"], "small-model")
        categories = {item["code"]: item["label"] for item in payload["arxiv_categories"]}
        self.assertGreater(len(categories), 100)
        self.assertEqual(categories["quant-ph"], "quant-ph · Quantum Physics")

    def test_flat_config_cache_reuses_parse_and_detects_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            first_document = {"daily_research": {"max_papers_per_run": 7}}
            changed_document = {"daily_research": {"max_papers_per_run": 9}}
            backend._invalidate_runtime_caches()
            try:
                with patch.object(
                    backend, "ensure_runtime_config_path", return_value=config_path
                ), patch.object(
                    backend,
                    "read_config_json",
                    side_effect=[first_document, changed_document],
                ) as read:
                    first = backend.flat_config()
                    first["daily_max_papers_per_run"] = 0
                    second = backend.flat_config()

                    self.assertEqual(read.call_count, 1)
                    self.assertEqual(second["daily_max_papers_per_run"], 7)

                    # Size changes as well as mtime, avoiding filesystem
                    # timestamp-resolution assumptions in this contract test.
                    config_path.write_text("{changed: true}", encoding="utf-8")
                    changed = backend.flat_config()

                self.assertEqual(read.call_count, 2)
                self.assertEqual(changed["daily_max_papers_per_run"], 9)
            finally:
                backend._invalidate_runtime_caches()

    def test_open_store_reuses_the_wrapper_until_cache_invalidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "daily_research.db"
            backend._invalidate_runtime_caches()
            try:
                with patch.object(backend, "configured_db_path", return_value=database):
                    first = backend.open_store(create=True)
                    second = backend.open_store()
                    self.assertIsNotNone(first)
                    self.assertIs(first, second)

                    backend._invalidate_runtime_caches(clear_config=False)
                    third = backend.open_store()

                self.assertIsNot(first, third)
            finally:
                backend._invalidate_runtime_caches()

    def test_save_settings_rejects_unknown_fields_before_writing(self) -> None:
        with self.assertRaisesRegex(backend.ModernWebUIError, "不支持"):
            backend.save_settings({"not_a_real_config_option": True}, {})

    def test_task_records_read_the_same_trigger_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            queued = enqueue_trigger(data_dir, "legacy_import", full_repair=False)
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir):
                rows = backend.task_records({"legacy_import"})

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_id"], queued.stem.rsplit("_", 1)[-1])
        self.assertEqual(rows[0]["state"], "queued")
        self.assertFalse(rows[0]["args"]["full_repair"])

    def test_running_receipt_beats_persistent_handoff_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            request = enqueue_trigger(data_dir, "legacy_import", full_repair=False)
            request_id = request.stem.rsplit("_", 1)[-1]
            request.rename(request.with_suffix(".running"))
            status_dir = trigger_status_directory(data_dir)
            status_dir.mkdir(parents=True, exist_ok=True)
            (status_dir / f"{request_id}.json").write_text(
                json.dumps(
                    {
                        "request_id": request_id,
                        "mode": "legacy_import",
                        "state": "running",
                        "created_at": "2026-08-29T00:00:00+00:00",
                        "started_at": "2026-08-29T00:00:03+00:00",
                        "updated_at": "2026-08-29T00:00:03+00:00",
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir):
                rows = backend.task_records({"legacy_import"})

        self.assertEqual(rows[0]["state"], "running")
        self.assertEqual(rows[0]["started_at"], "2026-08-29T00:00:03+00:00")

    def test_active_locks_includes_parameterized_trend_locks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            run_dir = data_dir / "run"
            run_dir.mkdir()
            trend_lock = run_dir / "trend_research_a1b2c3d4.lock"
            trend_lock.write_text("PID=42\n", encoding="utf-8")
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir), patch.object(
                backend, "configured_data_dir", return_value=data_dir
            ), patch.object(backend, "is_lock_held", return_value=True):
                locks = backend.active_locks()

        self.assertEqual(locks, [{"name": trend_lock.name, "pid": 42}])

    def test_run_status_surfaces_a_live_lock_without_a_receipt(self) -> None:
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[{"name": "trend_research_a1b2.lock", "pid": 42}]
        ), patch.object(backend, "task_records", return_value=[]), patch.object(
            backend, "open_store", return_value=None
        ), patch.object(backend, "_live_log_tail", return_value=None):
            status = backend.run_status("trend")

        self.assertTrue(status["is_active"])
        self.assertFalse(status["can_start"])
        self.assertEqual(status["task"]["label"], "趋势任务")
        self.assertEqual(status["relevant_locks"][0]["pid"], 42)
        self.assertEqual(status["stop_kind"], "trend")
        self.assertTrue(status["can_stop"])

    def test_live_status_log_keeps_only_the_latest_fifteen_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logs_root = Path(directory)
            log_path = logs_root / "daily_20260829.log"
            log_path.write_text(
                "\n".join(f"line {index}" for index in range(20)), encoding="utf-8"
            )
            with patch.object(backend, "LOGS_DIR", logs_root):
                tail = backend._live_log_tail([{"name": "daily_research.lock"}])

        self.assertIsNotNone(tail)
        self.assertTrue(tail["truncated"])
        lines = tail["content"].splitlines()
        self.assertEqual(len(lines), 16)
        self.assertIn("5 行", lines[0])
        self.assertEqual(lines[1], "line 5")
        self.assertEqual(lines[-1], "line 19")

    def test_log_tail_reader_bounds_large_files_without_losing_latest_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "long-running.log"
            path.write_text(
                "\n".join(f"line {index}" for index in range(50_000)),
                encoding="utf-8",
            )
            lines, truncated, skipped = backend._read_log_tail_lines(
                path,
                max_lines=4,
                chunk_size=64,
                max_bytes=128,
            )

        self.assertTrue(truncated)
        self.assertIsNone(skipped)
        self.assertEqual(lines, ["line 49996", "line 49997", "line 49998", "line 49999"])

    def test_scoped_stop_targets_only_matching_lock(self) -> None:
        locks = [
            {"name": "daily_research.lock", "pid": 101},
            {"name": "trend_research_abc123.lock", "pid": 202},
        ]
        with patch.object(backend, "active_locks", return_value=locks), patch.object(
            backend, "request_stop"
        ) as request_stop:
            stopped = backend.stop_active_tasks("trend")

        self.assertEqual(stopped, [202])
        request_stop.assert_called_once_with(backend.DEFAULT_DATA_DIR, 202)

    def test_scoped_stop_rejects_unknown_task_kind(self) -> None:
        with self.assertRaisesRegex(backend.ModernWebUIError, "不支持"):
            backend.stop_active_tasks("unknown")

    def test_run_status_marks_an_unclaimed_trigger_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            request = enqueue_trigger(data_dir, "daily_research")
            old = time.time() - backend._TRIGGER_STALE_AFTER_SECONDS - 5
            os.utime(request, (old, old))
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir), patch.object(
                backend, "flat_config", return_value={}
            ), patch.object(backend, "active_locks", return_value=[]), patch.object(
                backend, "open_store", return_value=None
            ), patch.object(backend, "_is_container_webui", return_value=False):
                status = backend.run_status("daily")

        self.assertTrue(status["trigger"]["stale"])
        self.assertTrue(status["trigger"]["can_clear"])
        self.assertFalse(status["is_active"])
        self.assertFalse(status["can_start"])
        self.assertEqual(status["task"]["state"], "stale")

    def test_clear_stale_triggers_keeps_a_fresh_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            stale = enqueue_trigger(data_dir, "daily_research")
            fresh = enqueue_trigger(data_dir, "daily_research")
            old = time.time() - backend._TRIGGER_STALE_AFTER_SECONDS - 5
            os.utime(stale, (old, old))
            with patch.object(backend, "DEFAULT_DATA_DIR", data_dir), patch.object(
                backend, "active_locks", return_value=[]
            ), patch.object(backend, "_is_container_webui", return_value=False):
                result = backend.clear_stale_triggers()
                self.assertEqual(result, {"removed": 1})
                self.assertFalse(stale.exists())
                self.assertTrue(fresh.exists())

    def test_clear_stale_triggers_is_disabled_in_a_container(self) -> None:
        with patch.object(backend, "_is_container_webui", return_value=True):
            with self.assertRaisesRegex(backend.ModernWebUIError, "Docker"):
                backend.clear_stale_triggers()

    def test_log_categories_match_the_streamlit_three_picker_layout(self) -> None:
        self.assertEqual(backend._log_category("system.log"), "system")
        self.assertEqual(backend._log_category("daily_20260829.log"), "run")
        self.assertEqual(backend._log_category("history_data_repair_20260829.log"), "run")
        self.assertEqual(backend._log_category("trend_20260829.log"), "other")

    def test_report_tokens_are_bound_to_the_report_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "daily.html"
            report.write_text("<html></html>", encoding="utf-8")
            token = backend._report_token(report, root)
            self.assertEqual(backend._report_path(token, root), report)
            with self.assertRaises(backend.ModernWebUIError):
                backend._report_path("Li4vZXRjL3Bhc3N3ZA", root)

    def test_daily_report_source_preserves_legacy_and_nested_source_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_root = root / "daily_research" / "html"
            legacy = daily_root / "arxiv_Report_2026-08-01_12-00-00.html"
            nested = daily_root / "openalex" / "OpenAlex_Report_2026-08-02_12-00-00.html"
            supplement = (
                root
                / "other_reports"
                / "supplement"
                / "html"
                / "prl"
                / "Supplement_Report_2026-08-03_12-00-00.html"
            )
            nested.parent.mkdir(parents=True)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            supplement.parent.mkdir(parents=True)
            legacy.touch()
            nested.touch()
            supplement.touch()

            self.assertEqual(backend._daily_report_source(legacy, root), "arxiv")
            self.assertEqual(backend._daily_report_source(nested, root), "openalex")
            self.assertEqual(backend._daily_report_source(supplement, root), "prl")

    def test_report_list_separates_legacy_and_new_supplements_from_daily_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reports"
            daily = root / "daily_research" / "html" / "arxiv" / "ARXIV_Report_2026-09-01_12-00-00.html"
            legacy_supplement = root / "daily_research" / "html" / "arxiv" / "ARXIV_Report_2026-09-01_13-00-00.html"
            keyword = root / "keyword_trend" / "html" / "Keyword_Trend_2026-09-01.html"
            supplement = root / "other_reports" / "supplement" / "html" / "arxiv" / "Supplement_Report_2026-09-01_14-00-00.html"
            for path in (daily, legacy_supplement, keyword, supplement):
                path.parent.mkdir(parents=True, exist_ok=True)
            daily.write_text("<html><title>Daily report</title></html>", encoding="utf-8")
            legacy_supplement.write_text(
                "<html><title>arXiv Report Supplement Report</title><h1>arXiv 补充报告 (Supplement Report)</h1></html>",
                encoding="utf-8",
            )
            keyword.write_text("<html><title>Keyword trend</title></html>", encoding="utf-8")
            supplement.write_text(
                "<html><title>arXiv Report Supplement Report</title><h1>arXiv 补充报告 (Supplement Report)</h1></html>",
                encoding="utf-8",
            )

            with patch.object(backend, "configured_reports_dir", return_value=root), patch.object(
                backend, "open_store", return_value=None
            ), patch.object(backend, "_report_source_labels", return_value={"arxiv": "arXiv"}):
                groups = backend.list_reports(show_non_arxiv=True)

        self.assertEqual(set(groups), {"daily", "trend", "other"})
        self.assertEqual([row["name"] for row in groups["daily"]], [daily.name])
        self.assertEqual(
            {row["type"] for row in groups["other"]}, {"keyword_trend", "supplement"}
        )
        self.assertEqual(
            {row["name"] for row in groups["other"] if row["type"] == "supplement"},
            {legacy_supplement.name, supplement.name},
        )

    def test_migrate_legacy_supplements_moves_artifacts_and_rewrites_sqlite_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            root = data_dir / "reports"
            old_html = (
                root
                / "daily_research"
                / "html"
                / "arxiv"
                / "ARXIV_Report_2026-09-01_13-14-15_123456.html"
            )
            old_markdown = (
                root
                / "daily_research"
                / "markdown"
                / "arxiv"
                / "ARXIV_Report_2026-09-01_13-14-15_123456.md"
            )
            old_html.parent.mkdir(parents=True)
            old_markdown.parent.mkdir(parents=True)
            old_html.write_text(
                "<html><title>arXiv Report Supplement Report</title><h1>arXiv 补充报告 (Supplement Report)</h1></html>",
                encoding="utf-8",
            )
            old_markdown.write_text("# arXiv · 补充报告\n", encoding="utf-8")

            store = DailyResearchStore(data_dir / "daily_research" / "daily_research.db")
            run_id = store.start_run(1, run_kind="supplement")
            old_html_reference = "/app/data/reports/daily_research/html/arxiv/ARXIV_Report_2026-09-01_13-14-15_123456.html"
            old_markdown_reference = "/app/data/reports/daily_research/markdown/arxiv/ARXIV_Report_2026-09-01_13-14-15_123456.md"
            store.complete_run(
                run_id,
                {"arxiv_html": old_html_reference, "arxiv": old_markdown_reference},
            )
            with store._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO paper_deliveries(
                        run_id, source, paper_id, canonical_id, version,
                        report_path, delivered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        "arxiv",
                        "2609.00001v1",
                        "2609.00001",
                        1,
                        old_html_reference,
                        "2026-09-01T13:14:15",
                    ),
                )

            with patch.object(backend, "flat_config", return_value={}), patch.object(
                backend, "configured_reports_dir", return_value=root
            ), patch.object(backend, "configured_data_dir", return_value=data_dir), patch.object(
                backend, "open_store", return_value=store
            ):
                result = backend.migrate_supplement_reports()

                repeated = backend.migrate_supplement_reports()

            new_html = (
                root
                / "other_reports"
                / "supplement"
                / "html"
                / "arxiv"
                / "Supplement_Report_2026-09-01_13-14-15_123456.html"
            )
            new_markdown = (
                root
                / "other_reports"
                / "supplement"
                / "markdown"
                / "arxiv"
                / "Supplement_Report_2026-09-01_13-14-15_123456.md"
            )
            expected_html_reference = "/app/data/reports/other_reports/supplement/html/arxiv/Supplement_Report_2026-09-01_13-14-15_123456.html"
            expected_markdown_reference = "/app/data/reports/other_reports/supplement/markdown/arxiv/Supplement_Report_2026-09-01_13-14-15_123456.md"

            self.assertEqual(
                result,
                {
                    "ok": True,
                    "html_moved": 1,
                    "markdown_moved": 1,
                    "database_runs": 1,
                    "database_deliveries": 1,
                },
            )
            self.assertEqual(
                repeated,
                {
                    "ok": True,
                    "html_moved": 0,
                    "markdown_moved": 0,
                    "database_runs": 0,
                    "database_deliveries": 0,
                },
            )
            self.assertFalse(old_html.exists())
            self.assertFalse(old_markdown.exists())
            self.assertTrue(new_html.is_file())
            self.assertTrue(new_markdown.is_file())
            self.assertEqual(
                store.report_paths_for_paper("arxiv", "2609.00001v1"),
                [expected_html_reference, expected_markdown_reference],
            )
            with store._connect() as conn:
                persisted = json.loads(
                    conn.execute(
                        "SELECT report_paths_json FROM daily_runs WHERE run_id = ?", (run_id,)
                    ).fetchone()["report_paths_json"]
                )
            self.assertEqual(persisted["arxiv_html"], expected_html_reference)
            self.assertEqual(persisted["arxiv"], expected_markdown_reference)

    def test_migrate_legacy_supplements_rejects_an_active_worker_before_moving_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "data"
            root = data_dir / "reports"
            old_html = root / "daily_research" / "html" / "arxiv" / "ARXIV_Report_2026-09-01_13-14-15.html"
            old_html.parent.mkdir(parents=True)
            old_html.write_text(
                "<html><title>arXiv Report Supplement Report</title></html>",
                encoding="utf-8",
            )

            with patch.object(backend, "flat_config", return_value={}), patch.object(
                backend, "configured_reports_dir", return_value=root
            ), patch.object(backend, "configured_data_dir", return_value=data_dir), patch.object(
                backend,
                "database_restore_activity_gate",
                side_effect=backend.DatabaseRestoreBusyError("busy"),
            ):
                with self.assertRaisesRegex(backend.ModernWebUIError, "运行中的任务"):
                    backend.migrate_supplement_reports()

            self.assertTrue(old_html.is_file())

    def test_report_rows_use_streamlit_friendly_labels_and_disambiguate_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "daily_research" / "html" / "arxiv" / "arxiv_Report_2026-08-02_10-11-12_123.html"
            second = root / "daily_research" / "html" / "arxiv" / "arxiv_Report_2026-08-02_10-11-12_456.html"
            first.parent.mkdir(parents=True)
            first.write_text("<html></html>", encoding="utf-8")
            second.write_text("<html></html>", encoding="utf-8")
            with patch.object(backend, "_report_source_labels", return_value={"arxiv": "arXiv"}):
                rows = [
                    backend._report_row(first, root, "daily", "arxiv"),
                    backend._report_row(second, root, "daily", "arxiv"),
                ]
            backend._disambiguate_report_labels(rows)

        self.assertEqual(rows[0]["source_label"], "arXiv")
        self.assertEqual(rows[0]["label"], "2026-08-02  10:11:12.123")
        self.assertEqual(rows[1]["label"], "2026-08-02  10:11:12.456")

    def test_report_preference_can_initialise_the_shared_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "daily_research.db"
            with patch.object(backend, "configured_db_path", return_value=database):
                result = backend.set_preference(
                    {
                        "source": "arxiv",
                        "paper_id": "2608.00001",
                        "title": "A saved report paper",
                        "preference": "like",
                    }
                )
                store = backend.open_store()

            self.assertTrue(database.is_file())
            self.assertEqual(result, {"ok": True, "preference": "like"})
            self.assertEqual(
                store.get_paper_preference("arxiv", "2608.00001")["preference"],
                "like",
            )

    def test_trend_prompt_templates_round_trip_with_bounded_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trend_prompt_templates.json"
            with patch.object(backend, "TREND_PROMPT_TEMPLATES_PATH", path):
                rows = backend.save_trend_prompt_template("量子计算", "关注实验进展")
                custom = next(item for item in rows if item["name"] == "量子计算")
                self.assertEqual(custom, {
                    "name": "量子计算",
                    "text": "关注实验进展",
                    "builtin": False,
                    "overridden": False,
                    "default": False,
                })
                default_template = next(item for item in rows if item["default"])
                self.assertEqual(default_template["name"], backend.DEFAULT_TREND_PROMPT_TEMPLATE_NAME)
                self.assertTrue(default_template["builtin"])
                self.assertEqual(len([item for item in rows if item["builtin"]]), 4)
                after_delete = backend.delete_trend_prompt_template("量子计算")
                self.assertFalse(any(item["name"] == "量子计算" for item in after_delete))

                builtin_name = next(iter(backend.BUILTIN_TREND_PROMPT_TEMPLATES))
                edited = backend.save_trend_prompt_template(builtin_name, "本机自定义内容")
                overridden = next(item for item in edited if item["name"] == builtin_name)
                self.assertTrue(overridden["builtin"])
                self.assertTrue(overridden["overridden"])
                self.assertEqual(overridden["text"], "本机自定义内容")
                restored = backend.delete_trend_prompt_template(builtin_name)
                default = next(item for item in restored if item["name"] == builtin_name)
                self.assertFalse(default["overridden"])
                self.assertEqual(default["text"], backend.BUILTIN_TREND_PROMPT_TEMPLATES[builtin_name])
                with self.assertRaisesRegex(backend.ModernWebUIError, "名称不能为空"):
                    backend.save_trend_prompt_template("", "内容")

    def test_webdav_client_uses_current_saved_panel_values(self) -> None:
        settings = {
            "webdav_enabled": True,
            "webdav_remote_path": "/research/",
            "proxy_enabled": True,
            "proxy_webdav": True,
            "proxy_url": "http://proxy.example:7890",
        }
        env = {
            "WEBDAV_URL": "https://dav.example.test/root/",
            "WEBDAV_USERNAME": "operator",
            "WEBDAV_PASSWORD": "saved-password",
        }
        client = MagicMock()
        with patch.object(backend, "WebDAVSync", return_value=client) as construct:
            result = backend._configured_webdav_client(settings, env)

        self.assertIs(result, client)
        construct.assert_called_once_with(
            url=env["WEBDAV_URL"],
            username=env["WEBDAV_USERNAME"],
            password=env["WEBDAV_PASSWORD"],
            remote_path="/research/",
            proxy_url="http://proxy.example:7890",
        )

    def test_manual_webdav_sync_uses_saved_scope(self) -> None:
        client = MagicMock()
        client.sync_all.return_value = {"success": 2, "total": 2}
        settings = {
            "webdav_enabled": True,
            "webdav_sync_configs": False,
            "webdav_sync_history": True,
            "webdav_sync_keywords": False,
            "webdav_sync_reports": True,
        }
        with patch.object(backend, "flat_config", return_value=settings), patch.object(
            backend, "_configured_webdav_client", return_value=client
        ):
            result = backend.webdav_operation("upload")

        self.assertTrue(result["ok"])
        client.sync_all.assert_called_once_with(
            direction="upload",
            include_reports=True,
            include_configs=False,
            include_history=True,
            include_keywords=False,
        )

    def test_manual_webdav_sync_uses_unsaved_form_values_without_persisting(self) -> None:
        client = MagicMock()
        client.sync_all.return_value = {"success": 1, "total": 1}
        saved_settings = {
            "webdav_enabled": True,
            "webdav_remote_path": "/saved/",
            "webdav_sync_configs": True,
            "webdav_sync_history": True,
            "webdav_sync_keywords": True,
            "webdav_sync_reports": False,
        }
        saved_env = {
            "WEBDAV_URL": "https://saved.example.test/dav/",
            "WEBDAV_USERNAME": "saved-user",
            "WEBDAV_PASSWORD": "saved-password",
        }
        overrides = {
            "webdav_enabled": True,
            "webdav_remote_path": "/candidate/",
            "webdav_sync_configs": False,
            "webdav_sync_history": False,
            "webdav_sync_keywords": True,
            "webdav_sync_reports": True,
        }
        env_overrides = {
            "WEBDAV_URL": "https://candidate.example.test/dav/",
            "WEBDAV_USERNAME": "candidate-user",
            "WEBDAV_PASSWORD": "candidate-password",
        }
        with patch.object(backend, "flat_config", return_value=saved_settings), patch.object(
            backend, "read_env", return_value=saved_env
        ), patch.object(backend, "_configured_webdav_client", return_value=client) as build_client:
            result = backend.webdav_operation("upload", overrides, env_overrides)

        self.assertTrue(result["ok"])
        settings, env = build_client.call_args.args[:2]
        self.assertEqual(settings["webdav_remote_path"], "/candidate/")
        self.assertEqual(env["WEBDAV_URL"], "https://candidate.example.test/dav/")
        self.assertEqual(saved_settings["webdav_remote_path"], "/saved/")
        self.assertEqual(saved_env["WEBDAV_URL"], "https://saved.example.test/dav/")
        client.sync_all.assert_called_once_with(
            direction="upload",
            include_reports=True,
            include_configs=False,
            include_history=False,
            include_keywords=True,
        )

    def test_manual_backup_keeps_local_snapshot_and_mirrors_when_configured(self) -> None:
        settings = {
            "webdav_enabled": True,
            "backup_local_retention_days": 31,
            "backup_local_same_day_max_count": 4,
        }
        webdav_client = MagicMock()
        with patch.object(backend, "flat_config", return_value=settings), patch.object(
            backend, "configured_data_dir", return_value=Path("/data")
        ), patch.object(backend, "configured_db_path", return_value=Path("/data/db.sqlite")), patch.object(
            backend, "_configured_webdav_client", return_value=webdav_client
        ), patch.object(backend, "create_backup", return_value={"created": True, "name": "snapshot.db.gz"}) as create:
            result = backend.create_local_backup()

        self.assertTrue(result["created"])
        create.assert_called_once_with(
            Path("/data"),
            database=Path("/data/db.sqlite"),
            retention_days=31,
            same_day_max_count=4,
            webdav_sync=webdav_client,
        )

    def test_manual_backup_uses_unsaved_retention_and_webdav_values(self) -> None:
        settings = {
            "webdav_enabled": True,
            "backup_local_retention_days": 7,
            "backup_local_same_day_max_count": 0,
        }
        client = MagicMock()
        with patch.object(backend, "flat_config", return_value=settings), patch.object(
            backend, "read_env", return_value={"WEBDAV_URL": "https://saved.example/", "WEBDAV_USERNAME": "saved"}
        ), patch.object(backend, "configured_data_dir", return_value=Path("/data")), patch.object(
            backend, "configured_db_path", return_value=Path("/data/db.sqlite")
        ), patch.object(backend, "_configured_webdav_client", return_value=client) as build_client, patch.object(
            backend, "create_backup", return_value={"created": True, "name": "candidate.db.gz"}
        ) as create:
            result = backend.create_local_backup(
                {
                    "webdav_enabled": True,
                    "backup_local_retention_days": 31,
                    "backup_local_same_day_max_count": 4,
                },
                {
                    "WEBDAV_URL": "https://candidate.example/",
                    "WEBDAV_USERNAME": "candidate",
                    "WEBDAV_PASSWORD": "candidate-password",
                },
            )

        self.assertTrue(result["created"])
        settings_arg, env_arg = build_client.call_args.args[:2]
        self.assertEqual(settings_arg["backup_local_retention_days"], 31)
        self.assertEqual(env_arg["WEBDAV_USERNAME"], "candidate")
        create.assert_called_once_with(
            Path("/data"),
            database=Path("/data/db.sqlite"),
            retention_days=31,
            same_day_max_count=4,
            webdav_sync=client,
        )

    def test_restore_backup_refuses_an_active_worker_database_gate(self) -> None:
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "configured_data_dir", return_value=Path("/data")
        ), patch.object(
            backend, "configured_db_path", return_value=Path("/data/daily_research.db")
        ), patch.object(
            backend,
            "database_restore_activity_gate",
            side_effect=backend.DatabaseRestoreBusyError("busy"),
        ), patch.object(backend, "restore_backup_archive") as restore:
            with self.assertRaisesRegex(backend.ModernWebUIError, "运行中的任务"):
                backend.restore_database_backup(b"SQLite format 3\\x00fixture", "backup.db")

        restore.assert_not_called()

    def test_smtp_connection_test_keeps_tls_enabled_for_a_blank_optional_value(self) -> None:
        with patch.object(backend, "read_env", return_value={}), patch.object(
            backend, "validate_smtp_connection", return_value=(True, "连接正常")
        ) as validate:
            result = backend.connection_test(
                "smtp",
                {
                    "host": "smtp.example.test",
                    "port": "587",
                    "user": "operator",
                    "password": "secret",
                    "use_tls": "",
                },
            )

        self.assertTrue(result["ok"])
        self.assertTrue(validate.call_args.args[-1])

    def test_diagnostics_preserve_source_receipt_fields_for_the_ui(self) -> None:
        store = MagicMock()
        store.get_source_health_for_days.return_value = {
            "arxiv": {
                "last_status": "failed",
                "last_scan_at": "2026-08-29T10:00:00",
                "last_task_kind": "history_omission_scan",
                "scans_in_window": 4,
                "succeeded_in_window": 3,
                "success_rate": 0.75,
                "last_new_candidates": 8,
                "last_error": "temporary request failure",
                "last_error_at": "2026-08-29T09:59:00",
            }
        }
        store.get_llm_health_by_model.return_value = []
        store.get_recent_operational_runs.return_value = []
        with patch.object(backend, "open_store", return_value=store), patch.object(
            backend, "flat_config", return_value={"extra_source_definitions": []}
        ):
            payload = backend.diagnostics(7)

        row = payload["sources"][0]
        self.assertEqual(row["last_event_at"], "2026-08-29T10:00:00")
        self.assertEqual(row["last_task_kind"], "history_omission_scan")
        self.assertEqual(row["events"], 4)
        self.assertEqual(row["succeeded"], 3)
        self.assertEqual(row["last_new_candidates"], 8)

    def test_history_status_exposes_progress_timing_and_retry_metadata(self) -> None:
        store = MagicMock()
        store.get_app_state.return_value = ""
        store.active_run_progress.return_value = {
            "run_kind": "history_data_repair",
            "phase": "history_repair",
            "detail": "正在补全 TL;DR",
            "current": 2,
            "total": 5,
        }
        records = [
            {
                "request_id": "running",
                "mode": "history_data_repair",
                "state": "running",
                "created_at": "2026-08-29T00:00:00+00:00",
                "started_at": "2026-08-29T00:01:00+00:00",
                "updated_at": "2026-08-29T00:02:00+00:00",
                "issue": "",
                "args": {},
            },
            {
                "request_id": "failed",
                "mode": "legacy_import",
                "state": "failed",
                "created_at": "2026-08-28T00:00:00+00:00",
                "started_at": "2026-08-28T00:01:00+00:00",
                "updated_at": "2026-08-28T00:02:00+00:00",
                "issue": "读取报告失败",
                "args": {"full_repair": True},
            },
        ]
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "open_store", return_value=store
        ), patch.object(backend, "task_records", return_value=records), patch.object(
            backend, "run_status", return_value={"is_active": True}
        ):
            payload = backend.history_status()

        running = next(item for item in payload["tasks"] if item["request_id"] == "running")
        failed = next(item for item in payload["tasks"] if item["request_id"] == "failed")
        self.assertEqual(running["label"], "历史数据补全")
        self.assertIn("补全历史数据", running["progress"])
        self.assertIn("正在补全 TL;DR", running["progress"])
        self.assertIn("2/5", running["progress"])
        self.assertEqual(running["started_at"], "2026-08-29T00:01:00+00:00")
        self.assertTrue(failed["retryable"])
        self.assertEqual(failed["completed_at"], "2026-08-28T00:02:00+00:00")

    def test_history_status_hides_a_failure_replaced_by_its_retry(self) -> None:
        records = [
            {
                "request_id": "failed",
                "mode": "legacy_import",
                "state": "failed",
                "created_at": "2026-08-28T00:00:00+00:00",
                "updated_at": "2026-08-28T00:02:00+00:00",
                "issue": "读取报告失败",
                "args": {"full_repair": True},
                "retry_of": "",
            },
            {
                "request_id": "retry",
                "mode": "legacy_import",
                "state": "succeeded",
                "created_at": "2026-08-29T00:00:00+00:00",
                "updated_at": "2026-08-29T00:02:00+00:00",
                "issue": "",
                "args": {"full_repair": True},
                "retry_of": "failed",
            },
        ]
        store = MagicMock()
        store.get_app_state.return_value = ""
        store.active_run_progress.return_value = None
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "open_store", return_value=store
        ), patch.object(backend, "task_records", return_value=records), patch.object(
            backend, "run_status", return_value={"is_active": False}
        ):
            payload = backend.history_status()

        self.assertEqual(payload["tasks"], [])

    def test_history_status_explains_a_time_window_queue(self) -> None:
        records = [
            {
                "request_id": "queued",
                "mode": "history_omission_scan",
                "state": "queued",
                "created_at": "2026-08-31T00:00:00+00:00",
                "updated_at": "",
                "issue": "",
                "args": {},
            }
        ]
        store = MagicMock()
        store.get_app_state.return_value = ""
        store.active_run_progress.return_value = None
        flat = {
            "history_maintenance_run_mode": "time_window",
            "history_maintenance_time_window_start": "00:00",
            "history_maintenance_time_window_end": "06:00",
        }
        with patch.object(backend, "flat_config", return_value=flat), patch.object(
            backend, "open_store", return_value=store
        ), patch.object(backend, "task_records", return_value=records), patch.object(
            backend, "run_status", return_value={"is_active": False}
        ):
            payload = backend.history_status()

        self.assertEqual(payload["schedule"]["run_mode"], "time_window")
        self.assertEqual(
            payload["tasks"][0]["progress"], "等待 00:00–06:00 时段及后端空闲"
        )

    def test_daily_status_hides_history_locks_but_keeps_launch_guard(self) -> None:
        history_lock = {"name": "legacy_import.lock", "pid": 42}
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[history_lock]
        ), patch.object(backend, "task_records", return_value=[]), patch.object(
            backend, "open_store", return_value=None
        ):
            status = backend.run_status("daily")

        self.assertEqual(status["active_locks"], [])
        self.assertFalse(status["can_start"])

    def test_daily_status_shows_other_operational_work_that_blocks_launch(self) -> None:
        backfill_lock = {"name": "backfill_run.lock", "pid": 42}
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[backfill_lock]
        ), patch.object(backend, "task_records", return_value=[]), patch.object(
            backend, "open_store", return_value=None
        ), patch.object(backend, "_live_log_tail", return_value=None):
            status = backend.run_status("daily")

        self.assertTrue(status["is_active"])
        self.assertFalse(status["can_start"])
        self.assertEqual(status["task"]["label"], "过去日报")
        self.assertEqual(status["active_locks"], [backfill_lock])
        self.assertEqual(status["relevant_locks"], [])

    def test_past_daily_can_queue_behind_an_active_worker(self) -> None:
        daily_lock = {"name": "daily_research.lock", "pid": 42}
        running_daily = {
            "request_id": "daily-running",
            "mode": "daily_research",
            "state": "running",
            "created_at": "2026-08-29T00:00:00+00:00",
            "started_at": "2026-08-29T00:00:01+00:00",
            "updated_at": "2026-08-29T00:00:02+00:00",
            "issue": "",
            "args": {},
        }
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[daily_lock]
        ), patch.object(backend, "task_records", return_value=[running_daily]), patch.object(
            backend, "open_store", return_value=None
        ):
            status = backend.run_status("past")

        self.assertTrue(status["can_start"])

    def test_trend_can_queue_behind_an_active_daily_worker(self) -> None:
        """Trend has no daily-workflow gate, matching the Streamlit launcher."""
        daily_lock = {"name": "daily_research.lock", "pid": 42}
        running_daily = {
            "request_id": "daily-running",
            "mode": "daily_research",
            "state": "running",
            "created_at": "2026-08-29T00:00:00+00:00",
            "started_at": "2026-08-29T00:00:01+00:00",
            "updated_at": "2026-08-29T00:00:02+00:00",
            "issue": "",
            "args": {},
        }
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[daily_lock]
        ), patch.object(
            backend,
            "task_records",
            side_effect=lambda modes, **_kwargs: [running_daily]
            if "daily_research" in modes
            else [],
        ), patch.object(
            backend, "open_store", return_value=None
        ), patch.object(backend, "_live_log_tail", return_value=None):
            status = backend.run_status("trend")

        self.assertTrue(status["can_start"])
        self.assertFalse(status["is_active"])

    def test_daily_status_uses_the_active_backfill_progress(self) -> None:
        store = MagicMock()
        store.active_run_progress.return_value = {
            "run_kind": "backfill",
            "phase": "analyze",
            "current": 2,
            "total": 5,
        }
        store.count_pending_papers.return_value = {"total": 0, "failed_retry": 0}
        store.backfill_queue_summary.return_value = {}
        with patch.object(backend, "flat_config", return_value={}), patch.object(
            backend, "active_locks", return_value=[{"name": "backfill_run.lock", "pid": 42}]
        ), patch.object(backend, "task_records", return_value=[]), patch.object(
            backend, "open_store", return_value=store
        ), patch.object(backend, "_live_log_tail", return_value=None):
            status = backend.run_status("daily")

        self.assertTrue(status["is_active"])
        self.assertEqual(status["task"]["label"], "过去日报")
        self.assertEqual(status["task"]["current"], 2)

    def test_collect_qualified_favorites_uses_the_shared_store(self) -> None:
        store = MagicMock()
        store.collect_qualified_favorites.return_value = {
            "scanned": 8,
            "qualified": 3,
            "added": 2,
            "preserved": 1,
        }
        with patch.object(backend, "open_store", return_value=store):
            result = backend.collect_qualified_favorites()

        self.assertEqual(result["added"], 2)
        self.assertTrue(result["ok"])
        store.collect_qualified_favorites.assert_called_once_with()

    def test_notification_test_uses_draft_values_and_saved_secret_fallback(self) -> None:
        with patch.object(
            backend,
            "read_env",
            return_value={
                "TELEGRAM_BOT_TOKEN": "saved-token",
                "TELEGRAM_CHAT_ID": "saved-chat",
            },
        ), patch.object(
            backend,
            "flat_config",
            return_value={
                "proxy_enabled": True,
                "proxy_notifications": True,
                "proxy_url": "http://127.0.0.1:7890",
            },
        ), patch.object(backend, "send_test_notification") as send:
            result = backend.test_notification("telegram", {"TELEGRAM_CHAT_ID": "draft-chat"})

        self.assertEqual(result, {"ok": True, "message": "测试通知已发送。"})
        send.assert_called_once_with(
            "telegram",
            {
                "TELEGRAM_BOT_TOKEN": "saved-token",
                "TELEGRAM_CHAT_ID": "draft-chat",
            },
            proxies={"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
