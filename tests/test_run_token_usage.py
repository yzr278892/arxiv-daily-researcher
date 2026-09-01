import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


class RunTokenUsageTests(unittest.TestCase):
    def test_record_and_aggregate_by_day_and_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            run_id = store.start_run(total_papers=0)
            store.record_token_usage(
                run_id,
                {
                    "cheap-model": {"prompt": 100, "completion": 50, "total": 150},
                    "smart-model": {"prompt": 200, "completion": 80, "total": 280},
                },
            )
            store.record_token_usage(
                "trend_20260822_120000",
                {"cheap-model": {"prompt": 10, "completion": 5, "total": 15}},
                mode="trend_research",
            )

            days = store.get_daily_token_totals()
            self.assertEqual(len(days), 1)
            self.assertEqual(days[0]["prompt"], 310)
            self.assertEqual(days[0]["completion"], 135)
            self.assertEqual(days[0]["total"], 445)
            self.assertEqual(days[0]["runs"], 2)

            models = store.get_token_usage_by_model()
            self.assertEqual(models[0]["model"], "smart-model")
            self.assertEqual(models[0]["total"], 280)
            self.assertEqual(models[1]["model"], "cheap-model")
            self.assertEqual(models[1]["total"], 165)

    def test_rerecording_same_run_replaces_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            run_id = store.start_run(total_papers=0)
            store.record_token_usage(run_id, {"m": {"prompt": 100, "completion": 0}})
            store.record_token_usage(run_id, {"m": {"prompt": 300, "completion": 20}})

            days = store.get_daily_token_totals()
            self.assertEqual(days[0]["total"], 320)

    def test_historical_token_upsert_uses_report_time_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            recorded_at = datetime(2024, 1, 2, 8, 30)

            self.assertEqual(
                store.upsert_historical_token_usage(
                    "historical_report_example",
                    {"historical_report": {"prompt": 12, "completion": 3}},
                    mode="daily_research",
                    recorded_at=recorded_at,
                ),
                "imported",
            )
            self.assertEqual(
                store.upsert_historical_token_usage(
                    "historical_report_example",
                    {"historical_report": {"prompt": 12, "completion": 3}},
                    mode="daily_research",
                    recorded_at="2024-01-02T08:30:00",
                ),
                "unchanged",
            )
            self.assertEqual(
                store.upsert_historical_token_usage(
                    "historical_report_example",
                    {"historical_report": {"prompt": 15, "completion": 5}},
                    mode="daily_research",
                    recorded_at=recorded_at,
                ),
                "updated",
            )

            self.assertEqual(store.get_daily_token_totals(), [{
                "date": "2024-01-02", "prompt": 15, "completion": 5,
                "total": 20, "runs": 1,
            }])

    def test_daily_window_filters_old_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            store.record_token_usage("r1", {"m": {"prompt": 5, "completion": 5}})
            # 手动把这条记录改到一年前，模拟历史数据。
            import sqlite3

            with sqlite3.connect(store.db_path) as conn:
                conn.execute(
                    "UPDATE run_token_usage SET recorded_at = '2020-01-01T00:00:00'"
                )
            self.assertEqual(store.get_daily_token_totals(days=30), [])
            self.assertEqual(len(store.get_daily_token_totals()), 1)

    def test_precise_token_windows_support_hourly_charts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            store.record_token_usage("morning", {"cheap": {"prompt": 100, "completion": 20}})
            store.record_token_usage("midmorning", {"smart": {"prompt": 40, "completion": 10}})
            store.record_token_usage("previous_day", {"cheap": {"prompt": 8, "completion": 2}})
            import sqlite3

            with sqlite3.connect(store.db_path) as conn:
                conn.execute(
                    "UPDATE run_token_usage SET recorded_at = ? WHERE run_id = ?",
                    ("2026-08-30T08:15:00", "morning"),
                )
                conn.execute(
                    "UPDATE run_token_usage SET recorded_at = ? WHERE run_id = ?",
                    ("2026-08-30T09:40:00", "midmorning"),
                )
                conn.execute(
                    "UPDATE run_token_usage SET recorded_at = ? WHERE run_id = ?",
                    ("2026-08-29T23:15:00", "previous_day"),
                )

            start = datetime(2026, 8, 30, 8)
            end = datetime(2026, 8, 30, 10)
            series = store.get_token_usage_series(start_at=start, end_at=end, bucket="hour")
            self.assertEqual([row["bucket"] for row in series], ["2026-08-30T08", "2026-08-30T09"])
            self.assertEqual(series[0]["total"], 120)
            self.assertEqual(series[1]["total"], 50)
            self.assertEqual(store.get_token_usage_summary(start_at=start, end_at=end), {
                "prompt": 140, "completion": 30, "total": 170, "runs": 2,
            })
            self.assertEqual(
                [row["model"] for row in store.get_token_usage_by_model_range(start_at=start, end_at=end)],
                ["cheap", "smart"],
            )


if __name__ == "__main__":
    unittest.main()
