import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from config import settings  # noqa: E402
from modes.daily_research import (  # noqa: E402
    DailyResearchPipeline,
    _keyword_configuration_error,
    _score_or_hydrate_paper,
)
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_errors import PaperStageError  # noqa: E402
from utils.daily_research_fingerprints import build_stage_input_fingerprints  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _paper():
    return PaperMetadata(
        paper_id="2501.12345v1",
        title="A paper",
        authors=["Alice"],
        abstract="An abstract",
        published_date=datetime.now(timezone.utc),
        url="https://arxiv.org/abs/2501.12345v1",
        source="arxiv",
    )


def _score():
    return WeightedScoreResponse(
        total_score=4,
        keyword_scores={"quantum": 4},
        author_bonus=0,
        expert_authors_found=[],
        passing_score=3,
        is_qualified=True,
        reasoning="relevant",
        tldr="A concise TLDR",
        extracted_keywords=["quantum"],
    )


class _Agent:
    deep_template = {"modules": []}

    def __init__(self, score_result=None, translation_result="中文摘要"):
        self.score_result = score_result or _score()
        self.translation_result = translation_result
        self.score_calls = 0
        self.translation_calls = 0

    def score_paper_with_keywords(self, **_kwargs):
        self.score_calls += 1
        if isinstance(self.score_result, BaseException):
            raise self.score_result
        return self.score_result

    def translate_abstract(self, _abstract):
        self.translation_calls += 1
        if isinstance(self.translation_result, BaseException):
            raise self.translation_result
        return self.translation_result


class DailyResearchStateTests(unittest.TestCase):
    def test_reference_extraction_can_be_the_only_keyword_source(self):
        self.assertIsNone(_keyword_configuration_error(None, [], True))
        self.assertIsNone(
            _keyword_configuration_error({"reference term": 0.8}, [], True)
        )
        self.assertIn(
            "未产出关键词", _keyword_configuration_error({}, [], True) or ""
        )
        self.assertIn(
            "未启用", _keyword_configuration_error(None, [], False) or ""
        )

    def _run_score_or_hydrate(self, store, run_id, paper, agent, keywords):
        return _score_or_hydrate_paper(
            run_id,
            "arxiv",
            paper,
            agent,
            keywords,
            {},
            __import__("threading").Lock(),
            store,
        )

    def test_translation_failure_is_typed_and_does_not_mark_score_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "DAILY_RESEARCH_PERSISTENCE_ENABLED", True
        ):
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            agent = _Agent(translation_result=RuntimeError("backend is down"))
            run_id = store.start_run(1)

            with self.assertRaises(PaperStageError) as raised:
                self._run_score_or_hydrate(store, run_id, paper, agent, {"quantum": 1.0})
            self.assertEqual(raised.exception.stage, "translation")
            store.update_error(run_id, "arxiv", paper.paper_id, str(raised.exception), raised.exception.stage)

            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "failed")
            self.assertIsNotNone(record["score_json"])

    def test_qualified_score_is_automatically_favorited_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "AUTO_FAVORITE_QUALIFIED_PAPERS", True
        ):
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            run_id = store.start_run(1)

            self._run_score_or_hydrate(store, run_id, paper, _Agent(), {"quantum": 1.0})

            preference = store.get_paper_preference("arxiv", paper.paper_id)
            self.assertIsNotNone(preference)
            self.assertEqual(preference["preference"], "like")
            self.assertEqual(preference["title"], paper.title)

    def test_qualified_score_is_not_automatically_favorited_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "AUTO_FAVORITE_QUALIFIED_PAPERS", False
        ):
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            run_id = store.start_run(1)

            self._run_score_or_hydrate(store, run_id, paper, _Agent(), {"quantum": 1.0})

            self.assertIsNone(store.get_paper_preference("arxiv", paper.paper_id))

    def test_score_fingerprint_change_rescores_an_incomplete_paper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            first_agent = _Agent()
            first_run = store.start_run(1)
            self._run_score_or_hydrate(store, first_run, paper, first_agent, {"quantum": 1.0})
            store.update_error(first_run, "arxiv", paper.paper_id, "analysis later", "analysis")

            changed_agent = _Agent()
            retry_run = store.start_run(1)
            self._run_score_or_hydrate(
                store, retry_run, paper, changed_agent, {"quantum": 0.5, "sensing": 1.0}
            )

            self.assertEqual(changed_agent.score_calls, 1)
            self.assertEqual(changed_agent.translation_calls, 1)
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "succeeded")

    def test_stable_inputs_reuse_persisted_score_and_translation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            first_agent = _Agent()
            first_run = store.start_run(1)
            self._run_score_or_hydrate(store, first_run, paper, first_agent, {"quantum": 1.0})
            store.update_error(first_run, "arxiv", paper.paper_id, "analysis later", "analysis")

            retry_agent = _Agent()
            retry_run = store.start_run(1)
            result = self._run_score_or_hydrate(store, retry_run, paper, retry_agent, {"quantum": 1.0})

            self.assertEqual(retry_agent.score_calls, 0)
            self.assertEqual(retry_agent.translation_calls, 0)
            self.assertEqual(result["abstract_cn"], "中文摘要")

    def test_optional_enrichment_is_hydrated_before_one_fingerprinted_upsert(self):
        """A retry must not need a preliminary SQLite write to restore a PDF URL."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            first = _paper()
            first.pdf_url = "https://arxiv.org/pdf/2501.12345v1.pdf"
            first_run = store.start_run(1)
            store.upsert_paper_seen(first_run, "arxiv", first)

            retry = _paper()
            retry_run = store.start_run(1)
            with patch.object(store, "upsert_paper_seen", wraps=store.upsert_paper_seen) as upsert:
                self._run_score_or_hydrate(
                    store, retry_run, retry, _Agent(), {"quantum": 1.0}
                )

            self.assertEqual(upsert.call_count, 1)
            self.assertEqual(retry.pdf_url, first.pdf_url)
            record = store.get_paper_record("arxiv", retry.paper_id)
            expected = build_stage_input_fingerprints(
                retry, {"quantum": 1.0}, _Agent.deep_template
            )
            self.assertEqual(record["analysis_input_fingerprint"], expected["analysis"])

    def test_new_scores_persist_non_secret_audit_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            run_id = store.start_run(1)

            self._run_score_or_hydrate(store, run_id, paper, _Agent(), {"quantum": 1.0})
            record = store.get_paper_record("arxiv", paper.paper_id)
            audit = json.loads(record["score_audit_json"])

        self.assertEqual(audit["strategy_id"], settings.normalized_score_strategy())
        self.assertIn("policy_fingerprint", audit)
        serialized = json.dumps(audit).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("base_url", serialized)
        self.assertNotIn("research_context\"", serialized)

    def test_primary_keyword_membership_invalidates_unfinished_score_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            settings, "SCORE_STRATEGY", "core_relevance_v2"
        ), patch.object(settings, "PRIMARY_KEYWORDS", ["quantum"]):
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper()
            first_agent = _Agent()
            first_run = store.start_run(1)
            self._run_score_or_hydrate(store, first_run, paper, first_agent, {"quantum": 1.0})
            store.update_error(first_run, "arxiv", paper.paper_id, "analysis later", "analysis")

            with patch.object(settings, "PRIMARY_KEYWORDS", ["sensing"]):
                retry_agent = _Agent()
                retry_run = store.start_run(1)
                self._run_score_or_hydrate(
                    store, retry_run, paper, retry_agent, {"quantum": 1.0}
                )

        self.assertEqual(retry_agent.score_calls, 1)

    def test_v2_fingerprint_changes_when_primary_membership_changes(self):
        paper = _paper()
        with patch.object(settings, "SCORE_STRATEGY", "core_relevance_v2"), patch.object(
            settings, "PRIMARY_KEYWORDS", ["quantum"]
        ):
            first = build_stage_input_fingerprints(paper, {"quantum": 1.0}, {})["score"]
        with patch.object(settings, "SCORE_STRATEGY", "core_relevance_v2"), patch.object(
            settings, "PRIMARY_KEYWORDS", ["other"]
        ):
            second = build_stage_input_fingerprints(paper, {"quantum": 1.0}, {})["score"]
        self.assertNotEqual(first, second)

    def test_pipeline_limit_queues_complete_scan_before_processing_one_paper(self):
        class _KeywordAgent:
            def get_all_keywords(self):
                return {"quantum": 1.0}

        class _SearchAgent:
            def __init__(self, **_kwargs):
                pass

            def get_enabled_sources(self):
                return ["arxiv"]

            def fetch_all_papers(self, **kwargs):
                callback = kwargs["scan_receipt_callbacks"]["arxiv"]
                callback(
                    {
                        "source": "arxiv",
                        "status": "succeeded",
                        "scanned_at": "2026-08-21T08:00:00+00:00",
                        "domain_receipts": [],
                        "total_new_candidates": 3,
                    }
                )
                return {
                    "arxiv": [
                        PaperMetadata(
                            paper_id=f"2501.1234{index}v1",
                            title=f"Paper {index}",
                            authors=["Alice"],
                            abstract=f"Abstract {index}",
                            published_date=datetime.now(timezone.utc),
                            url=f"https://arxiv.org/abs/2501.1234{index}v1",
                            source="arxiv",
                        )
                        for index in range(3)
                    ]
                }

            def can_download_pdf(self, _source):
                return False

        class _AnalysisAgent(_Agent):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            report_path = root / "ARXIV_Report.html"

            class _Reporter:
                processed_counts = []

                def generate_reports_by_source(self, **kwargs):
                    count = len(kwargs["scored_papers_by_source"]["arxiv"])
                    self.__class__.processed_counts.append(count)
                    report_path.write_text("<html>one queued batch</html>", encoding="utf-8")
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
                stack.enter_context(patch("modes.daily_research.SearchAgent", _SearchAgent))
                stack.enter_context(patch("modes.daily_research.AnalysisAgent", _AnalysisAgent))
                stack.enter_context(patch("modes.daily_research.Reporter", _Reporter))
                stack.enter_context(
                    patch(
                        "modes.daily_research.deliver_pending_after_report_syncs",
                        return_value={"claimed": 0},
                    )
                )
                stack.enter_context(
                    patch("modes.daily_research.after_report_sync_maintenance_entry", return_value=None)
                )
                result = DailyResearchPipeline().run()

            store = DailyResearchStore(db_path)
            pending, pending_count = store.select_pending_papers(["arxiv"], limit=0)

            self.assertTrue(result.success)
            self.assertEqual(result.total_papers_fetched, 1)
            self.assertEqual(_Reporter.processed_counts, [1])
            self.assertEqual(pending_count, 2)
            self.assertEqual(sum(len(items) for items in pending.values()), 2)
            self.assertEqual(
                len(store.get_version_records("arxiv", "2501.12340"))
                + len(store.get_version_records("arxiv", "2501.12341"))
                + len(store.get_version_records("arxiv", "2501.12342")),
                3,
            )

    def test_partial_stage_failure_delivers_complete_papers_and_keeps_failed_one_retryable(self):
        """A transient LLM failure must not discard an otherwise valid daily report."""

        class _KeywordAgent:
            def get_all_keywords(self):
                return {"quantum": 1.0}

        class _SearchAgent:
            def __init__(self, **_kwargs):
                pass

            def get_enabled_sources(self):
                return ["arxiv"]

            def fetch_all_papers(self, **kwargs):
                kwargs["scan_receipt_callbacks"]["arxiv"](
                    {
                        "source": "arxiv",
                        "status": "succeeded",
                        "scanned_at": "2026-08-24T08:00:00+00:00",
                        "domain_receipts": [],
                        "total_new_candidates": 2,
                    }
                )
                return {
                    "arxiv": [
                        PaperMetadata(
                            paper_id="2501.20001v1",
                            title="Fails once",
                            authors=["Alice"],
                            abstract="Temporary provider failure",
                            published_date=datetime.now(timezone.utc),
                            url="https://arxiv.org/abs/2501.20001v1",
                            source="arxiv",
                        ),
                        PaperMetadata(
                            paper_id="2501.20002v1",
                            title="Completes normally",
                            authors=["Bob"],
                            abstract="A complete paper",
                            published_date=datetime.now(timezone.utc),
                            url="https://arxiv.org/abs/2501.20002v1",
                            source="arxiv",
                        ),
                    ]
                }

            def can_download_pdf(self, _source):
                return False

        class _PartialAnalysisAgent(_Agent):
            def score_paper_with_keywords(self, **kwargs):
                if kwargs["title"] == "Fails once":
                    raise RuntimeError("temporary provider outage")
                return super().score_paper_with_keywords(**kwargs)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "daily.db"
            report_path = root / "ARXIV_Report.html"

            class _Reporter:
                processed_titles = []

                def generate_reports_by_source(self, **kwargs):
                    papers = kwargs["scored_papers_by_source"]["arxiv"]
                    self.__class__.processed_titles.append(
                        [paper["title"] for paper in papers]
                    )
                    report_path.write_text("<html>partial report</html>", encoding="utf-8")
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
                "DAILY_MAX_PAPERS_PER_RUN": 2,
                "ENABLE_CONCURRENCY": False,
                "ENABLE_MARKDOWN_REPORT": False,
                "ENABLE_HTML_REPORT": True,
                "REPORTS_DIR": root,
            }
            with ExitStack() as stack:
                for name, value in overrides.items():
                    stack.enter_context(patch.object(settings, name, value))
                stack.enter_context(patch("modes.daily_research.KeywordAgent", _KeywordAgent))
                stack.enter_context(patch("modes.daily_research.SearchAgent", _SearchAgent))
                stack.enter_context(
                    patch("modes.daily_research.AnalysisAgent", _PartialAnalysisAgent)
                )
                stack.enter_context(patch("modes.daily_research.Reporter", _Reporter))
                stack.enter_context(
                    patch(
                        "modes.daily_research.deliver_pending_after_report_syncs",
                        return_value={"claimed": 0},
                    )
                )
                stack.enter_context(
                    patch("modes.daily_research.after_report_sync_maintenance_entry", return_value=None)
                )
                result = DailyResearchPipeline().run()

            store = DailyResearchStore(db_path)
            failed = store.get_paper_record("arxiv", "2501.20001v1")
            completed = store.get_paper_record("arxiv", "2501.20002v1")
            _pending, pending_count = store.select_pending_papers(["arxiv"], limit=0)

            self.assertTrue(result.success)
            self.assertEqual(result.total_papers_fetched, 2)
            self.assertEqual(_Reporter.processed_titles, [["Completes normally"]])
            self.assertEqual(failed["score_status"], "failed")
            self.assertIsNone(failed["completed_at"])
            self.assertEqual(failed["retry_count"], 1)
            self.assertIsNotNone(completed["completed_at"])
            self.assertEqual(pending_count, 1)


if __name__ == "__main__":
    unittest.main()
