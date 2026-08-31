"""SQLite-driven historical field repair and report artifact patching."""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes.history_data_repair import run_history_data_repair  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.history_report_patch import patch_historical_reports  # noqa: E402


def _paper() -> PaperMetadata:
    return PaperMetadata(
        paper_id="2603.12345v1",
        title="Repairable Quantum Paper",
        authors=["Alice"],
        abstract="An abstract that needs a Chinese translation.",
        published_date=datetime(2026, 3, 5),
        url="https://arxiv.org/abs/2603.12345v1",
        source="arxiv",
        pdf_url="https://arxiv.org/pdf/2603.12345v1.pdf",
    )


def _missing_card() -> str:
    return """<html><body>
<div class="card fail"><div class="card-title"><a href="https://arxiv.org/abs/2603.12345v1">1. Repairable Quantum Paper</a></div>
<details><summary>Abstract</summary><div class="analysis-content"><p>Original abstract.</p></div></details>
</div>
<div class="card fail"><div class="card-title"><a href="https://arxiv.org/abs/2603.99999v1">2. Untouched Paper</a></div></div>
</body></html>"""


class HistoryReportPatchTests(unittest.TestCase):
    def test_patches_only_the_sqlite_selected_nested_html_card(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "report.html"
            report.write_text(_missing_card(), encoding="utf-8")
            result = patch_historical_reports(
                [report],
                source="arxiv",
                paper_id="2603.12345v1",
                canonical_id="2603.12345",
                paper=_paper().to_dict(),
                tldr="补全后的 TLDR。",
                abstract_cn="补全后的中文摘要。",
                analysis={"summary": "补全后的分析。"},
            )
            self.assertTrue(result["success"])
            self.assertEqual(result["patched"], 1)
            content = report.read_text(encoding="utf-8")
            self.assertIn("补全后的 TLDR。", content)
            self.assertIn("补全后的中文摘要。", content)
            self.assertIn("补全后的分析。", content)
            untouched = content.split("Untouched Paper", 1)[1]
            self.assertNotIn("补全后的 TLDR。", untouched)


class HistoryDataRepairWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = DailyResearchStore(self.root / "history.db")
        self.report = self.root / "legacy.html"
        self.report.write_text(_missing_card(), encoding="utf-8")
        paper = _paper()
        self.delivery_run = self.store.start_run(0, run_kind="legacy_import")
        self.store.import_legacy_paper(
            {
                "source": "arxiv",
                "paper_id": paper.paper_id,
                "canonical_id": paper.canonical_id,
                "version": paper.version,
                "paper_json": paper.to_dict(),
                "score_json": json.dumps(
                    {
                        "total_score": 4.0,
                        "keyword_scores": {"quantum": 4.0},
                        "author_bonus": 0.0,
                        "expert_authors_found": [],
                        "passing_score": 10.0,
                        "is_qualified": False,
                        "reasoning": "old score",
                        "tldr": "",
                        "extracted_keywords": [],
                    }
                ),
                "abstract_cn": None,
                "analysis_json": None,
                "score_status": "succeeded",
                "tldr_status": "pending",
                "translation_status": "pending",
                "analysis_status": "not_required",
                "completed_at": "2026-03-05T08:00:00",
                "delivered_at": "2026-03-05T08:00:00",
                "delivery_run_id": self.delivery_run,
                "report_path": str(self.report),
            },
            delivered=True,
        )

    def _add_missing_tldr_candidate(self, paper_id: str) -> None:
        paper = PaperMetadata(
            paper_id=paper_id,
            title=f"Repairable {paper_id}",
            authors=["Bob"],
            abstract="Another abstract.",
            published_date=datetime(2026, 3, 6),
            url=f"https://arxiv.org/abs/{paper_id}",
            source="arxiv",
        )
        self.store.import_legacy_paper(
            {
                "source": "arxiv",
                "paper_id": paper.paper_id,
                "canonical_id": paper.canonical_id,
                "version": paper.version,
                "paper_json": paper.to_dict(),
                "score_json": json.dumps(
                    {
                        "total_score": 4.0,
                        "keyword_scores": {"quantum": 4.0},
                        "author_bonus": 0.0,
                        "expert_authors_found": [],
                        "passing_score": 10.0,
                        "is_qualified": False,
                        "reasoning": "old score",
                        "tldr": "",
                        "extracted_keywords": [],
                    }
                ),
                "abstract_cn": "已有中文翻译。",
                "analysis_json": None,
                "score_status": "succeeded",
                "tldr_status": "pending",
                "translation_status": "succeeded",
                "analysis_status": "not_required",
                "completed_at": "2026-03-06T08:00:00",
                "delivered_at": "2026-03-06T08:00:00",
                "delivery_run_id": self.delivery_run,
                "report_path": str(self.report),
            },
            delivered=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_repairs_tldr_and_translation_without_re_scoring_or_new_report(self):
        calls = []

        class _Agent:
            def __init__(self, *_args, **_kwargs):
                pass

            def generate_tldr(self, title, abstract):
                calls.append(("tldr", title, abstract))
                return "一条补全 TL;DR。"

            def translate_abstract(self, abstract):
                calls.append(("translation", abstract))
                return "一段补全中文翻译。"

            def score_paper_with_keywords(self, *_args, **_kwargs):
                raise AssertionError("only a missing TL;DR must not trigger re-scoring")

        with (
            patch("modes.history_data_repair.AnalysisAgent", _Agent),
            patch("modes.history_data_repair.settings.DAILY_ENABLE_DEEP_ANALYSIS", False),
        ):
            exit_code, _run_id, summary = run_history_data_repair(
                store=self.store, notify=False
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["repaired"]["score"], 0)
        self.assertEqual(summary["repaired"]["tldr"], 1)
        self.assertEqual(summary["repaired"]["translation"], 1)
        self.assertEqual([call[0] for call in calls], ["tldr", "translation"])
        record = self.store.get_paper_record("arxiv", "2603.12345v1")
        self.assertEqual(record["tldr_status"], "succeeded")
        self.assertEqual(record["translation_status"], "succeeded")
        self.assertEqual(record["report_repair_status"], "succeeded")
        self.assertEqual(self.store.history_repair_summary()["pending"], 0)
        content = self.report.read_text(encoding="utf-8")
        self.assertIn("一条补全 TL;DR。", content)
        self.assertIn("一段补全中文翻译。", content)

    def test_failed_repair_marks_maintenance_run_failed_and_keeps_candidate_retryable(self):
        """A per-paper API failure must remain visible in the status panel."""

        class _Agent:
            def __init__(self, *_args, **_kwargs):
                pass

            def generate_tldr(self, *_args, **_kwargs):
                raise RuntimeError("TL;DR provider temporarily unavailable")

        with (
            patch("modes.history_data_repair.AnalysisAgent", _Agent),
            patch("modes.history_data_repair.settings.DAILY_ENABLE_DEEP_ANALYSIS", False),
        ):
            exit_code, run_id, summary = run_history_data_repair(
                store=self.store, notify=False
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(summary["stage_failures"], 1)
        self.assertGreater(summary["pending_after"], 0)
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT status, error FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("有步骤未完成", row["error"])
        repaired = self.store.get_paper_record("arxiv", "2603.12345v1")
        self.assertEqual(repaired["tldr_status"], "failed")

    def test_history_maintenance_limit_defers_extra_repair_candidates(self):
        self._add_missing_tldr_candidate("2603.54321v1")

        class _Agent:
            def __init__(self, *_args, **_kwargs):
                pass

            def generate_tldr(self, *_args, **_kwargs):
                return "补全 TL;DR。"

            def translate_abstract(self, *_args, **_kwargs):
                return "补全中文翻译。"

        with (
            patch("modes.history_data_repair.AnalysisAgent", _Agent),
            patch("modes.history_data_repair.settings.DAILY_ENABLE_DEEP_ANALYSIS", False),
        ):
            exit_code, _run_id, summary = run_history_data_repair(
                store=self.store, notify=False, paper_limit=1
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["paper_limit"], 1)
        self.assertEqual(summary["candidates"], 1)
        self.assertTrue(summary["deferred_by_limit"])
        self.assertEqual(summary["pending_after"], 1)


if __name__ == "__main__":
    unittest.main()
