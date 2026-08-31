"""SQLite omission scan grouping and natural-calendar-week supplement runs."""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from modes.history_omission_scan import (  # noqa: E402
    _scan_sqlite_history,
    run_history_omission_scan,
)
from sources.base_source import PaperMetadata  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _entry(canonical: str, published: str, reason: str = "missed_scan") -> dict:
    return {
        "source": "arxiv",
        "canonical_id": canonical,
        "version": 1,
        "paper_id": f"{canonical}v1",
        "reason": reason,
        "paper_json": {
            "paper_id": f"{canonical}v1",
            "title": f"Paper {canonical}",
            "authors": ["Alice"],
            "abstract": "abstract",
            "published_date": f"{published}T00:00:00",
            "url": f"https://arxiv.org/abs/{canonical}v1",
            "source": "arxiv",
            "pdf_url": f"https://arxiv.org/pdf/{canonical}v1.pdf",
            "canonical_id": canonical,
            "version": 1,
            "categories": [],
        },
    }


class HistoryOmissionScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DailyResearchStore(Path(self.temp.name) / "history.db")
        self.store.record_supplement_backlog(
            [
                _entry("2603.10001", "2026-03-02"),  # Monday of first ISO week
                _entry("2603.10002", "2026-03-04"),
                _entry("2603.10003", "2026-03-09"),  # following Monday
                _entry("2603.20001", "2026-03-04", reason="missing_data"),
            ]
        )

    def tearDown(self):
        self.temp.cleanup()

    def _record_delivered(self, source: str, paper_id: str) -> None:
        paper = PaperMetadata(
            paper_id=paper_id,
            title=f"Delivered {paper_id}",
            authors=["Alice"],
            abstract="abstract",
            published_date=datetime(2026, 3, 2, tzinfo=timezone.utc),
            url=f"https://example.test/{paper_id}",
            source=source,
        )
        run_id = self.store.start_run(0, run_kind="legacy_import")
        self.store.import_legacy_paper(
            {
                "source": source,
                "paper_id": paper.paper_id,
                "canonical_id": paper.canonical_id,
                "version": paper.version,
                "paper_json": paper.to_dict(),
                "score_status": "completed",
                "tldr_status": "completed",
                "translation_status": "not_required",
                "analysis_status": "not_required",
                "completed_at": paper.published_date.isoformat(),
                "delivered_at": paper.published_date.isoformat(),
                "delivery_run_id": run_id,
                "report_path": "legacy.html",
            },
            delivered=True,
        )

    def test_sqlite_scan_covers_each_enabled_source(self):
        self._record_delivered("arxiv", "2603.30001v1")
        self._record_delivered("prl", "10.1103/known")

        class _SearchAgent:
            def __init__(self, **_kwargs):
                self.closed = False

            def get_enabled_sources(self):
                return ["arxiv", "prl"]

            def fetch_source_papers_between(self, source, _start, _end, **_kwargs):
                paper_id = "2603.39999v1" if source == "arxiv" else "10.1103/new"
                return [
                    PaperMetadata(
                        paper_id=paper_id,
                        title=f"New {paper_id}",
                        authors=["Bob"],
                        abstract="abstract",
                        published_date=datetime(2026, 3, 2, tzinfo=timezone.utc),
                        url=f"https://example.test/{paper_id}",
                        source=source,
                    )
                ]

            def close(self):
                self.closed = True

        agent = _SearchAgent()
        with patch("sources.search_agent.SearchAgent", return_value=agent):
            summary = _scan_sqlite_history(
                self.store,
                run_id="test-history-scan",
                progress_callback=lambda **_kwargs: None,
            )

        self.assertTrue(agent.closed)
        self.assertEqual(set(summary["sources"]), {"arxiv", "prl"})
        self.assertEqual(summary["missed_found"], 2)
        self.assertEqual(summary["backlog_queued"], 2)
        self.assertEqual(summary["failed_chunks"], 0)

    def test_no_enabled_source_skips_new_scan_without_failing(self):
        """Full legacy repair can run offline while live sources are paused."""
        with patch("modes.history_omission_scan.settings.ENABLED_SOURCES", []), patch(
            "sources.search_agent.SearchAgent"
        ) as search_agent:
            summary = _scan_sqlite_history(
                self.store,
                run_id="test-history-scan",
                progress_callback=lambda **_kwargs: None,
            )

        search_agent.assert_not_called()
        self.assertEqual(summary["failed_chunks"], 0)
        self.assertEqual(summary["sources"], {})
        self.assertTrue(summary["skipped_no_enabled_sources"])
        self.assertIn("未启用数据源", summary["skipped_reason"])

    def test_one_source_failure_does_not_hide_other_source_results(self):
        self._record_delivered("arxiv", "2603.30001v1")
        self._record_delivered("prl", "10.1103/known")

        class _SearchAgent:
            def __init__(self, **_kwargs):
                pass

            def get_enabled_sources(self):
                return ["arxiv", "prl"]

            def fetch_source_papers_between(self, source, _start, _end, **_kwargs):
                if source == "prl":
                    raise RuntimeError("OpenAlex unavailable")
                return [
                    PaperMetadata(
                        paper_id="2603.39999v1",
                        title="New arXiv",
                        authors=["Bob"],
                        abstract="abstract",
                        published_date=datetime(2026, 3, 2, tzinfo=timezone.utc),
                        url="https://arxiv.org/abs/2603.39999v1",
                        source="arxiv",
                    )
                ]

            def close(self):
                pass

        with patch("sources.search_agent.SearchAgent", _SearchAgent):
            summary = _scan_sqlite_history(
                self.store,
                run_id="test-history-scan",
                progress_callback=lambda **_kwargs: None,
            )

        self.assertEqual(summary["sources"]["arxiv"]["missed_found"], 1)
        self.assertEqual(summary["sources"]["prl"]["failed_chunks"], 1)
        self.assertEqual(summary["missed_found"], 1)
        self.assertEqual(summary["failed_chunks"], 1)
        self.assertTrue(any(error.startswith("prl:") for error in summary["errors"]))

    def test_groups_only_pending_missed_rows_by_iso_week(self):
        self.assertEqual(
            self.store.missed_scan_week_groups(),
            {
                datetime(2026, 3, 2).date(): 2,
                datetime(2026, 3, 9).date(): 1,
            },
        )

    def test_runs_each_week_in_capped_batches_with_sunday_report_dates(self):
        calls = []

        class _Pipeline:
            def run(self, **kwargs):
                calls.append(kwargs)
                rows = self_store.claim_supplement_backlog(
                    1,
                    reasons=kwargs["supplement_reasons"],
                    published_from=kwargs["supplement_week_start"],
                    published_to=kwargs["supplement_week_end"],
                )
                assert rows
                row = rows[0]
                self_store.resolve_supplement_backlog(
                    "fake-supplement",
                    [(row["source"], row["canonical_id"], row["version"])],
                    status="delivered",
                )
                return SimpleNamespace(
                    success=True,
                    interrupted=False,
                    total_papers_fetched=1,
                    report_paths={"arxiv_html": f"{row['canonical_id']}.html"},
                )

        # The nested fake class gets no direct store argument; bind the test
        # store as a local rather than depending on any global database path.
        self_store = self.store
        with patch(
            "modes.history_omission_scan._scan_sqlite_history",
            return_value={
                "range_start": "2026-03-02",
                "range_end": "2026-03-09",
                "papers_scanned": 3,
                "missed_found": 0,
                "failed_chunks": 0,
                "errors": [],
            },
        ):
            exit_code, _run_id, summary = run_history_omission_scan(
                store=self.store,
                notify=False,
                pipeline_factory=_Pipeline,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            [call["supplement_week_start"].isoformat() for call in calls],
            ["2026-03-02", "2026-03-02", "2026-03-09"],
        )
        self.assertEqual(
            [call["report_timestamp"].date().isoformat() for call in calls],
            ["2026-03-08", "2026-03-08", "2026-03-15"],
        )
        self.assertEqual(summary["pending_after"], 0)
        self.assertEqual([week["batches"] for week in summary["weeks"]], [2, 1])
        # A non-omission compatibility repair must stay for its separate flow.
        pending = self.store.supplement_backlog_summary(reasons={"missing_data"})
        self.assertEqual(pending["pending"], 1)

    def test_history_maintenance_limit_bounds_all_omission_batches(self):
        calls = []

        class _Pipeline:
            def run(self, **kwargs):
                calls.append(kwargs)
                rows = self_store.claim_supplement_backlog(
                    kwargs["paper_limit"],
                    reasons=kwargs["supplement_reasons"],
                    published_from=kwargs["supplement_week_start"],
                    published_to=kwargs["supplement_week_end"],
                )
                self_store.resolve_supplement_backlog(
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

        self_store = self.store
        with patch(
            "modes.history_omission_scan._scan_sqlite_history",
            return_value={
                "range_start": "2026-03-02",
                "range_end": "2026-03-09",
                "papers_scanned": 3,
                "missed_found": 0,
                "failed_chunks": 0,
                "errors": [],
            },
        ):
            exit_code, _run_id, summary = run_history_omission_scan(
                store=self.store,
                notify=False,
                pipeline_factory=_Pipeline,
                paper_limit=2,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["paper_limit"], 2)
        self.assertEqual(summary["processed"], 2)
        self.assertTrue(summary["deferred_by_limit"])
        self.assertEqual(summary["pending_after"], 1)

    def test_failed_supplement_batch_marks_scan_run_failed(self):
        class _FailingPipeline:
            def run(self, **_kwargs):
                return SimpleNamespace(
                    success=False,
                    interrupted=False,
                    total_papers_fetched=0,
                    report_paths={},
                    error_message="supplement LLM request failed",
                )

        with patch(
            "modes.history_omission_scan._scan_sqlite_history",
            return_value={
                "range_start": "2026-03-02",
                "range_end": "2026-03-09",
                "papers_scanned": 3,
                "missed_found": 0,
                "failed_chunks": 0,
                "errors": [],
            },
        ):
            exit_code, run_id, summary = run_history_omission_scan(
                store=self.store,
                notify=False,
                pipeline_factory=_FailingPipeline,
            )

        self.assertEqual(exit_code, 1)
        self.assertTrue(summary["issues"])
        self.assertGreater(summary["pending_after"], 0)
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT status, error FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("有步骤未完成", row["error"])


if __name__ == "__main__":
    unittest.main()
