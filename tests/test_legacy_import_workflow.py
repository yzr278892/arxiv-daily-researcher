"""Legacy import mode: lightweight ledger path and opt-in full repair path."""

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes import legacy_import  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.legacy_history import LEGACY_IMPORT_STATE_KEY  # noqa: E402


@contextmanager
def _no_lock(*_args, **_kwargs):
    yield


def _base_import_summary(full_repair: bool) -> dict:
    return {
        "finished_at": "2026-08-24T12:00:00",
        "full_repair_enabled": full_repair,
        "reports_scanned": 1,
        "cards_found": 1,
        "cards_selected": 1,
        "delivered_ledger_rows": 1,
        "backlog_queued": 0,
        "errors": [],
    }


class LegacyImportWorkflowTests(unittest.TestCase):
    def _patch_environment(self, root: Path):
        return (
            patch.object(legacy_import.settings, "DAILY_RESEARCH_DB_PATH", root / "daily.db"),
            patch.object(legacy_import.settings, "HISTORY_DIR", root / "history"),
            patch.object(legacy_import.settings, "REPORTS_DIR", root / "reports"),
            # Tests must not inherit a developer's enabled webhook setting.
            # The dedicated notification case below enables it with a fake
            # notifier, while every ordinary workflow case stays hermetic.
            patch.object(legacy_import.settings, "ENABLE_NOTIFICATIONS", False),
            patch.object(legacy_import, "run_lock", side_effect=_no_lock),
            patch.object(legacy_import, "daily_workflow_gate", side_effect=_no_lock),
            patch.object(legacy_import, "legacy_import_activity_gate", side_effect=_no_lock),
        )

    @contextmanager
    def _environment(self, root: Path):
        with ExitStack() as stack:
            for context in self._patch_environment(root):
                stack.enter_context(context)
            yield

    def test_lightweight_import_only_builds_html_delivery_ledger(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            calls = []

            def fake_import(_store, **kwargs):
                calls.append(kwargs)
                return _base_import_summary(False)

            with (
                self._environment(root),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
            ):
                self.assertEqual(legacy_import.main(full_repair=False), 0)

            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0]["full_repair"])
            store = DailyResearchStore(root / "daily.db")
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertFalse(summary["full_repair_enabled"])
            self.assertEqual(summary["history_repair"]["state"], "skipped")
            self.assertEqual(summary["supplement"]["state"], "skipped")
            self.assertEqual(summary["omission_scan"]["state"], "skipped")
            with store._connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT status FROM daily_runs").fetchone()["status"],
                    "completed",
                )

    def test_full_import_runs_repair_then_cardless_and_weekly_workflows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            call_order = []

            def fake_import(_store, **kwargs):
                call_order.append(("import", kwargs["full_repair"]))
                return _base_import_summary(True)

            def fake_repair(*, store, notify):
                call_order.append(("repair", notify))
                return 0, "repair-run", {
                    "candidates": 2,
                    "pending_after": 0,
                    "repaired": {"tldr": 1, "translation": 1, "analysis": 0},
                }

            def fake_supplement(_store, _summary, **_kwargs):
                call_order.append(("cardless", None))
                return 0

            def fake_omission(*, store, notify):
                call_order.append(("omission", notify))
                return 0, "omission-run", {
                    "scan": {"missed_found": 3},
                    "weeks": [{"week_start": "2026-08-24", "batches": 1}],
                    "pending_after": 0,
                }

            with (
                self._environment(root),
                patch.object(legacy_import, "import_legacy_history", side_effect=fake_import),
                patch("modes.history_data_repair.run_history_data_repair", side_effect=fake_repair),
                patch("modes.history_omission_scan.run_history_omission_scan", side_effect=fake_omission),
                patch.object(legacy_import, "_run_automatic_supplement", side_effect=fake_supplement),
            ):
                self.assertEqual(legacy_import.main(full_repair=True), 0)

            self.assertEqual(
                call_order,
                [("import", True), ("repair", False), ("cardless", None), ("omission", False)],
            )
            store = DailyResearchStore(root / "daily.db")
            summary = json.loads(store.get_app_state(LEGACY_IMPORT_STATE_KEY))
            self.assertEqual(summary["history_repair"]["state"], "completed")
            self.assertEqual(summary["omission_scan"]["state"], "completed")
            self.assertEqual(summary["omission_scan"]["scan"]["missed_found"], 3)

    def test_full_import_sends_one_consolidated_degraded_notification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            delivered = []

            class _Notifier:
                def enqueue_workflow_result(self, _store, run_id, result):
                    delivered.append((run_id, result))
                    return 1

                def deliver_pending_workflow_results(self, _store):
                    return {"claimed": 1, "sent": 1, "deferred": 0}

            with (
                self._environment(root),
                patch.object(legacy_import.settings, "ENABLE_NOTIFICATIONS", True),
                patch.object(legacy_import, "NotifierAgent", _Notifier),
                patch.object(
                    legacy_import,
                    "import_legacy_history",
                    return_value=_base_import_summary(True),
                ),
                patch(
                    "modes.history_data_repair.run_history_data_repair",
                    return_value=(1, "repair-run", {"candidates": 1, "pending_after": 1, "issues": ["TL;DR API 暂不可用"]}),
                ),
                patch.object(legacy_import, "_run_automatic_supplement", return_value=0),
                patch(
                    "modes.history_omission_scan.run_history_omission_scan",
                    return_value=(0, "omission-run", {"scan": {}, "weeks": [], "pending_after": 0}),
                ),
            ):
                self.assertEqual(legacy_import.main(full_repair=True), 1)

            self.assertEqual(len(delivered), 1)
            store = DailyResearchStore(root / "daily.db")
            with store._connect() as conn:
                parent = conn.execute(
                    "SELECT status, error FROM daily_runs WHERE run_kind = 'legacy_import'"
                ).fetchone()
            self.assertEqual(parent["status"], "failed")
            self.assertIn("旧历史完整修复有步骤未完成", parent["error"])
            _run_id, result = delivered[0]
            self.assertEqual(result.workflow, "旧历史导入")
            self.assertFalse(result.success)
            self.assertTrue(any("TL;DR API 暂不可用" in item for item in result.issues))

    def test_cardless_supplement_uses_the_history_maintenance_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DailyResearchStore(root / "daily.db")
            store.record_supplement_backlog([
                {
                    "source": "arxiv",
                    "canonical_id": "2608.1",
                    "version": 1,
                    "paper_id": "2608.1v1",
                    "reason": "missing_data",
                },
                {
                    "source": "arxiv",
                    "canonical_id": "2608.2",
                    "version": 1,
                    "paper_id": "2608.2v1",
                    "reason": "missing_data",
                },
            ])
            calls = []

            class _Pipeline:
                def run(self, **kwargs):
                    calls.append(kwargs)
                    rows = store.claim_supplement_backlog(
                        kwargs["paper_limit"], reasons=kwargs["supplement_reasons"]
                    )
                    store.resolve_supplement_backlog(
                        "fake-supplement",
                        [(row["source"], row["canonical_id"], row["version"]) for row in rows],
                        status="delivered",
                    )
                    return SimpleNamespace(
                        success=True,
                        interrupted=False,
                        total_papers_fetched=len(rows),
                        report_paths={},
                    )

            summary: dict = {}
            with (
                self._environment(root),
                patch.object(
                    legacy_import.settings,
                    "DAILY_MAX_PAPERS_PER_RUN",
                    99,
                ),
                patch.object(
                    legacy_import.settings,
                    "HISTORY_MAINTENANCE_MAX_PAPERS_PER_RUN",
                    1,
                ),
                patch("modes.daily_research.DailyResearchPipeline", _Pipeline),
            ):
                self.assertEqual(
                    legacy_import._run_automatic_supplement(store, summary), 0
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["paper_limit"], 1)
            self.assertEqual(summary["supplement"]["processed"], 1)
            self.assertTrue(summary["supplement"]["deferred_by_limit"])
            self.assertEqual(store.supplement_backlog_summary()["pending"], 1)

    def test_interrupted_import_marks_parent_run_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                self._environment(root),
                patch.object(legacy_import, "import_legacy_history", side_effect=KeyboardInterrupt),
            ):
                self.assertEqual(legacy_import.main(full_repair=False), 130)

            store = DailyResearchStore(root / "daily.db")
            with store._connect() as conn:
                row = conn.execute("SELECT status, completed_at, error FROM daily_runs").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertIsNotNone(row["completed_at"])
            self.assertIn("用户中断旧历史导入", row["error"])


if __name__ == "__main__":
    unittest.main()
