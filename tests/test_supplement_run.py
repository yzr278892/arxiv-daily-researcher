"""补充报告运行：积压装载、上限控制、交付销账与补充报告标识。"""

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes.daily_research import DailyResearchPipeline  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from config import settings  # noqa: E402


def _paper(pid: str) -> PaperMetadata:
    return PaperMetadata(
        paper_id=pid,
        title=f"Paper {pid}",
        authors=["Alice"],
        abstract=f"Abstract {pid}",
        published_date=datetime.now(timezone.utc),
        url=f"https://arxiv.org/abs/{pid}",
        source="arxiv",
    )


class _Agent:
    deep_template = {}

    def score_paper_with_keywords(self, title, authors, abstract, keywords_dict, learned_terms=None):
        from agents.analysis_agent import WeightedScoreResponse

        return WeightedScoreResponse(
            total_score=10.0,
            keyword_scores={"quantum": 5.0},
            author_bonus=0.0,
            expert_authors_found=[],
            passing_score=4.0,
            is_qualified=True,
            reasoning="r",
            tldr="t",
            extracted_keywords=["quantum"],
        )

    def translate_abstract(self, abstract):
        return "中文摘要"

    def get_all_keywords(self):
        return {"quantum": 1.0}


@contextmanager
def _pipeline_overrides(root: Path, db_path: Path, report_path: Path, reporter_log: list):
    class _KeywordAgent:
        def get_all_keywords(self):
            return {"quantum": 1.0}

    class _AnalysisAgent(_Agent):
        pass

    class _Reporter:
        def generate_reports_by_source(self, **kwargs):
            reporter_log.append(kwargs)
            report_path.write_text("<html>supplement</html>", encoding="utf-8")
            return {"arxiv_html": report_path}

    overrides = {
        "TOKEN_TRACKING_ENABLED": False,
        "DAILY_RESEARCH_DB_PATH": db_path,
        "ENABLE_NOTIFICATIONS": False,
        "ENABLED_SOURCES": ["arxiv"],
        "TARGET_DOMAINS": ["quant-ph"],
        "TARGET_JOURNALS": [],
        "ENABLE_REFERENCE_EXTRACTION": False,
        "PRIMARY_KEYWORDS": ["quantum"],
        "PRIMARY_KEYWORD_WEIGHT": 1.0,
        "SCORE_STRATEGY": "legacy_weighted_keyword_v1",
        "HISTORY_DIR": root / "history",
        "OPENALEX_API_KEY": "",
        "ENABLE_SEMANTIC_SCHOLAR_TLDR": False,
        "SEMANTIC_SCHOLAR_API_KEY": "",
        "KEYWORD_TRACKER_ENABLED": False,
        "DAILY_ENABLE_DEEP_ANALYSIS": False,
        "DAILY_MAX_PAPERS_PER_RUN": 1,
        "ENABLE_CONCURRENCY": False,
        "ENABLE_MARKDOWN_REPORT": False,
        "ENABLE_HTML_REPORT": True,
        "REPORTS_DIR": root,
    }
    with ExitStack() as stack:
        for name, value in overrides.items():
            stack.enter_context(patch.object(settings, name, value))
        stack.enter_context(patch("modes.daily_research.KeywordAgent", _KeywordAgent))
        stack.enter_context(patch("modes.daily_research.AnalysisAgent", _AnalysisAgent))
        stack.enter_context(patch("modes.daily_research.Reporter", _Reporter))
        stack.enter_context(
            patch(
                "modes.daily_research.deliver_pending_after_report_syncs",
                return_value={"claimed": 0},
            )
        )
        stack.enter_context(
            patch(
                "modes.daily_research.after_report_sync_maintenance_entry",
                return_value=None,
            )
        )
        yield


class SupplementRunTests(unittest.TestCase):
    def test_supplement_run_processes_backlog_and_resolves_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            report_path = root / "ARXIV_Report.html"
            reporter_log = []

            store = DailyResearchStore(db_path)
            paper = _paper("2602.1v1")
            store.record_supplement_backlog([
                {
                    "source": "arxiv",
                    "canonical_id": "2602.1",
                    "version": 1,
                    "paper_id": "2602.1v1",
                    "reason": "missing_analysis",
                    "paper_json": paper.to_dict(),
                },
                {
                    "source": "arxiv",
                    "canonical_id": "2602.2",
                    "version": 1,
                    "paper_id": "2602.2v1",
                    "reason": "missed_scan",
                    "paper_json": _paper("2602.2v1").to_dict(),
                },
            ])

            with _pipeline_overrides(root, db_path, report_path, reporter_log):
                result = DailyResearchPipeline().run(run_kind="supplement")

            self.assertTrue(result.success)
            # 上限 1：本次只处理 1 篇（缺数据优先）。
            self.assertEqual(result.total_papers_fetched, 1)
            self.assertEqual(len(reporter_log), 1)
            self.assertEqual(reporter_log[0]["report_kind"], "supplement")

            store = DailyResearchStore(db_path)
            summary = store.supplement_backlog_summary()
            self.assertEqual(summary["pending"], 1)
            # 交付销账：一篇 delivered，另一篇仍待处理。
            with store._connect() as conn:
                statuses = dict(
                    conn.execute(
                        "SELECT canonical_id, status FROM supplement_backlog"
                    ).fetchall()
                )
                ledger = conn.execute(
                    "SELECT COUNT(*) FROM paper_deliveries WHERE canonical_id = '2602.1'"
                ).fetchone()[0]
                run_kind = conn.execute(
                    "SELECT run_kind FROM daily_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()[0]
            self.assertEqual(statuses["2602.1"], "delivered")
            self.assertEqual(statuses["2602.2"], "pending")
            self.assertEqual(ledger, 1)
            self.assertEqual(run_kind, "supplement")

    def test_supplement_run_without_backlog_completes_quietly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            report_path = root / "ARXIV_Report.html"
            reporter_log = []
            DailyResearchStore(db_path)

            with _pipeline_overrides(root, db_path, report_path, reporter_log):
                result = DailyResearchPipeline().run(run_kind="supplement")

            self.assertTrue(result.success)
            self.assertEqual(reporter_log, [])

    def test_supplement_paper_limit_can_override_the_daily_cap_for_history_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            report_path = root / "ARXIV_Report.html"
            reporter_log = []
            store = DailyResearchStore(db_path)
            store.record_supplement_backlog([
                {
                    "source": "arxiv",
                    "canonical_id": "2602.60",
                    "version": 1,
                    "paper_id": "2602.60v1",
                    "reason": "missing_analysis",
                    "paper_json": _paper("2602.60v1").to_dict(),
                },
                {
                    "source": "arxiv",
                    "canonical_id": "2602.61",
                    "version": 1,
                    "paper_id": "2602.61v1",
                    "reason": "missing_analysis",
                    "paper_json": _paper("2602.61v1").to_dict(),
                },
            ])

            with _pipeline_overrides(root, db_path, report_path, reporter_log):
                result = DailyResearchPipeline().run(
                    run_kind="supplement", paper_limit=2
                )

            self.assertTrue(result.success)
            self.assertEqual(result.total_papers_fetched, 2)
            self.assertEqual(store.supplement_backlog_summary()["pending"], 0)

    def test_supplement_loader_fetches_missing_metadata_by_id(self):
        from modes.daily_research import _load_supplement_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DailyResearchStore(root / "db.sqlite")
            run_id = store.start_run(0)
            store.record_supplement_backlog([
                {
                    "source": "arxiv",
                    "canonical_id": "2602.3",
                    "version": 1,
                    "paper_id": "2602.3v1",
                    "reason": "missing_data",
                },
                {
                    "source": "openalex",
                    "canonical_id": "10.1103/nope",
                    "version": 0,
                    "paper_id": "https://doi.org/10.1103/nope",
                    "reason": "missing_data",
                },
            ])
            with patch(
                "modes.daily_research.settings"
            ) as fake_settings, patch(
                "sources.arxiv_source.ArxivSource.fetch_papers_by_ids",
                return_value={"2602.3": _paper("2602.3v1")},
            ):
                fake_settings.DAILY_MAX_PAPERS_PER_RUN = 10
                fake_settings.HISTORY_DIR = root
                fake_settings.get_proxy_dict.return_value = None
                papers_by_source, selected, failures = _load_supplement_candidates(
                    store, run_id
                )

            self.assertEqual(len(papers_by_source["arxiv"]), 1)
            self.assertEqual(selected, [("arxiv", "2602.3", 1)])
            self.assertEqual(failures, 1)
            with store._connect() as conn:
                statuses = dict(
                    conn.execute(
                        "SELECT canonical_id, status FROM supplement_backlog"
                    ).fetchall()
                )
            self.assertEqual(statuses["10.1103/nope"], "failed")
            self.assertEqual(statuses["2602.3"], "pending")

    def test_failed_missing_data_is_selected_again_on_a_later_retry(self):
        from modes.daily_research import _load_supplement_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DailyResearchStore(root / "db.sqlite")
            store.record_supplement_backlog([
                {
                    "source": "arxiv",
                    "canonical_id": "2602.4",
                    "version": 1,
                    "paper_id": "2602.4v1",
                    "reason": "missing_data",
                }
            ])

            with patch("modes.daily_research.settings") as fake_settings, patch(
                "sources.arxiv_source.ArxivSource.fetch_papers_by_ids",
                return_value={},
            ):
                fake_settings.DAILY_MAX_PAPERS_PER_RUN = 10
                fake_settings.HISTORY_DIR = root
                fake_settings.get_proxy_dict.return_value = None
                _, _, failures = _load_supplement_candidates(
                    store, store.start_run(0)
                )
            self.assertEqual(failures, 1)
            self.assertEqual(store.supplement_backlog_summary()["pending"], 1)

            with patch("modes.daily_research.settings") as fake_settings, patch(
                "sources.arxiv_source.ArxivSource.fetch_papers_by_ids",
                return_value={"2602.4": _paper("2602.4v1")},
            ):
                fake_settings.DAILY_MAX_PAPERS_PER_RUN = 10
                fake_settings.HISTORY_DIR = root
                fake_settings.get_proxy_dict.return_value = None
                papers_by_source, selected, failures = _load_supplement_candidates(
                    store, store.start_run(0)
                )

            self.assertEqual(failures, 0)
            self.assertEqual(selected, [("arxiv", "2602.4", 1)])
            self.assertEqual(
                [paper.paper_id for paper in papers_by_source["arxiv"]], ["2602.4v1"]
            )

    def test_pending_scan_discovery_precedes_a_previously_failed_repair(self):
        """One unfetchable retry cannot block fresh scan discoveries forever."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "db.sqlite")
            store.record_supplement_backlog([
                {
                    "source": "arxiv", "canonical_id": "2602.40", "version": 1,
                    "paper_id": "2602.40v1", "reason": "missing_translation",
                },
                {
                    "source": "arxiv", "canonical_id": "2602.41", "version": 1,
                    "paper_id": "2602.41v1", "reason": "missed_scan",
                },
            ])
            store.resolve_supplement_backlog(
                "first-attempt", [("arxiv", "2602.40", 1)], status="failed"
            )

            rows = store.claim_supplement_backlog(limit=1)

            self.assertEqual(
                [(row["canonical_id"], row["reason"]) for row in rows],
                [("2602.41", "missed_scan")],
            )

    def test_unfetchable_repair_does_not_consume_the_supplement_report_cap(self):
        """A retry failure stays visible but a later missed scan can still run."""
        from modes.daily_research import _load_supplement_candidates

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DailyResearchStore(root / "db.sqlite")
            store.record_supplement_backlog([
                {
                    "source": "arxiv", "canonical_id": "2602.50", "version": 1,
                    "paper_id": "2602.50v1", "reason": "missing_data",
                },
                {
                    "source": "arxiv", "canonical_id": "2602.51", "version": 1,
                    "paper_id": "2602.51v1", "reason": "missed_scan",
                    "paper_json": _paper("2602.51v1").to_dict(),
                },
            ])
            with patch("modes.daily_research.settings") as fake_settings, patch(
                "sources.arxiv_source.ArxivSource.fetch_papers_by_ids",
                return_value={},
            ):
                fake_settings.DAILY_MAX_PAPERS_PER_RUN = 1
                fake_settings.HISTORY_DIR = root
                fake_settings.get_proxy_dict.return_value = None
                papers_by_source, selected, failures = _load_supplement_candidates(
                    store, store.start_run(0)
                )

            self.assertEqual(failures, 1)
            self.assertEqual(selected, [("arxiv", "2602.51", 1)])
            self.assertEqual(
                [paper.paper_id for paper in papers_by_source["arxiv"]], ["2602.51v1"]
            )
            with store._connect() as conn:
                statuses = dict(
                    conn.execute(
                        "SELECT canonical_id, status FROM supplement_backlog"
                    ).fetchall()
                )
            self.assertEqual(statuses["2602.50"], "failed")
            self.assertEqual(statuses["2602.51"], "pending")

    def test_unlimited_supplement_claim_uses_zero_like_daily_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "db.sqlite")
            store.record_supplement_backlog([
                {
                    "source": "arxiv", "canonical_id": "2602.10", "version": 1,
                    "paper_id": "2602.10v1", "reason": "missing_data",
                },
                {
                    "source": "arxiv", "canonical_id": "2602.11", "version": 1,
                    "paper_id": "2602.11v1", "reason": "missed_scan",
                },
            ])

            rows = store.claim_supplement_backlog(0)

            self.assertEqual(
                [row["canonical_id"] for row in rows], ["2602.10", "2602.11"]
            )


class ReporterSupplementKindTests(unittest.TestCase):
    def test_supplement_title_and_timestamp(self):
        from report.daily.reporter import Reporter

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = root / "ARXIV_Report_test.html"
            report_path.write_text("old", encoding="utf-8")
            agent_score = _Agent().score_paper_with_keywords(
                "T", [], "A", {"quantum": 1.0}
            )
            paper = _paper("2602.9v1")
            paper_info = {
                "paper_id": paper.paper_id,
                "title": paper.title,
                "authors": "Alice",
                "abstract": paper.abstract,
                "abstract_cn": "中文",
                "url": paper.url,
                "pdf_url": None,
                "published": "2026-02-01",
                "paper_metadata": paper,
                "score_response": agent_score,
            }
            overrides = {
                "ENABLE_MARKDOWN_REPORT": True,
                "ENABLE_HTML_REPORT": True,
                "INCLUDE_ALL_IN_REPORT": True,
                "REPORTS_DIR": root,
            }
            with ExitStack() as stack:
                for name, value in overrides.items():
                    stack.enter_context(patch.object(settings, name, value))
                paths = Reporter().generate_reports_by_source(
                    {"arxiv": [paper_info]},
                    {"quantum": 1.0},
                    analyses_by_source={},
                    token_usage=None,
                    report_kind="supplement",
                    report_timestamp=datetime(2026, 3, 15, 14, 23, 5),
                )
            html_path = paths["arxiv_html"]
            markdown_path = paths["arxiv"]
            expected_relative_base = Path("other_reports") / "supplement"
            self.assertEqual(
                html_path.relative_to(root),
                expected_relative_base
                / "html"
                / "arxiv"
                / "Supplement_Report_2026-03-15_14-23-05_000000.html",
            )
            self.assertEqual(
                markdown_path.relative_to(root),
                expected_relative_base
                / "markdown"
                / "arxiv"
                / "Supplement_Report_2026-03-15_14-23-05_000000.md",
            )
            content = html_path.read_text(encoding="utf-8")
            self.assertIn("补充报告 (Supplement Report)", content)
            self.assertIn("Generated: 2026-03-15 14:23:05", content)


if __name__ == "__main__":
    unittest.main()
