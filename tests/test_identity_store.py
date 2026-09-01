import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.arxiv_source import ArxivSource  # noqa: E402
from sources.base_source import HistoryLoadError  # noqa: E402
from sources.base_source import PaperMetadata, split_arxiv_version  # noqa: E402
from sources.search_agent import SearchAgent  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402
from report.daily.reporter import Reporter  # noqa: E402
from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from modes.daily_research import (  # noqa: E402
    _exclude_cross_source_arxiv_mirrors,
    _exclude_sqlite_delivered_papers,
)


def _paper(paper_id: str) -> PaperMetadata:
    return PaperMetadata(
        paper_id=paper_id,
        title=paper_id,
        authors=["Author"],
        abstract="abstract",
        published_date=datetime.now(timezone.utc),
        url=f"https://arxiv.org/abs/{paper_id}",
        source="arxiv",
    )


def _hf_paper(arxiv_id: str) -> PaperMetadata:
    return PaperMetadata(
        paper_id=f"hf:{arxiv_id}",
        title=f"HF mirror {arxiv_id}",
        authors=["Author"],
        abstract="abstract",
        published_date=datetime.now(timezone.utc),
        url=f"https://arxiv.org/abs/{arxiv_id}",
        source="huggingface_papers",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        arxiv_id=arxiv_id,
        arxiv_url=f"https://arxiv.org/abs/{arxiv_id}",
    )


def _source_receipt(source: str, status: str = "succeeded") -> dict:
    return {
        "source": source,
        "status": status,
        "scanned_at": "2026-08-13T08:00:00+00:00",
        "domain_receipts": [],
    }


class IdentityStoreTests(unittest.TestCase):
    def test_pending_queue_limit_preserves_exact_arxiv_versions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(0)
            v1 = _paper("2501.12345v1")
            v2 = _paper("2501.12345v2")
            v3 = _paper("2501.12345v3")

            registered = store.register_paper_candidates(
                run_id, {"arxiv": [v3, v1, v2]}
            )
            selected, total = store.select_pending_papers(["arxiv"], limit=2)

            self.assertEqual(registered, 3)
            self.assertEqual(total, 3)
            self.assertEqual(
                [paper.paper_id for paper in selected["arxiv"]],
                ["2501.12345v3", "2501.12345v1"],
            )
            self.assertEqual(
                [row["version"] for row in store.get_version_records("arxiv", "2501.12345")],
                [1, 2, 3],
            )

    def test_candidate_batch_registration_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(0)
            valid = _paper("2501.12345v1")
            invalid = _paper("2501.12346v1")
            invalid.source = "prl"

            with self.assertRaisesRegex(ValueError, "source mismatch"):
                store.register_paper_candidates(run_id, {"arxiv": [valid, invalid]})

            self.assertIsNone(store.get_paper_record("arxiv", valid.paper_id))

    def test_sqlite_daily_agent_does_not_read_legacy_json_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_dir = Path(temp_dir)
            (history_dir / "arxiv_history.json").write_text("{broken", encoding="utf-8")

            agent = SearchAgent(
                history_dir,
                enabled_sources=["arxiv"],
                arxiv_domains=["quant-ph"],
                enable_semantic_scholar=False,
                use_legacy_history_filter=False,
            )

            source = agent.sources["arxiv"]
            self.assertEqual(source.history, {})
            self.assertIsNone(source._history_load_error)
            self.assertFalse(source.history_filtering_enabled)

    def test_pending_queue_survives_completed_scan_and_prioritizes_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(0)
            papers = [_paper(f"2501.1234{index}v1") for index in range(3)]
            store.prepare_scan(run_id, 1, ["arxiv"])
            store.record_scan_receipt(run_id, "arxiv", _source_receipt("arxiv"))
            store.register_paper_candidates(run_id, {"arxiv": papers})
            store.update_error(run_id, "arxiv", papers[2].paper_id, "temporary", stage="score")
            store.complete_run(run_id)

            selected, total = store.select_pending_papers(["arxiv"], limit=1)

            self.assertEqual(total, 3)
            self.assertEqual(selected["arxiv"][0].paper_id, papers[2].paper_id)

    def test_scan_receipts_are_run_scoped_durable_and_visible_in_recent_runs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(0)
            store.prepare_scan(run_id, 3, ["arxiv"])
            receipt = {
                "source": "arxiv",
                "status": "succeeded",
                "scanned_at": "2026-08-13T08:00:00+00:00",
                "domain_receipts": [
                    {
                        "domain": "cs.AI",
                        "status": "succeeded",
                        "queries": {
                            "submitted": {"api_entries_checked": 3},
                            "updated": {"api_entries_checked": 2},
                        },
                        "new_candidates": 4,
                    }
                ],
            }
            store.record_scan_receipt(run_id, "arxiv", receipt)

            # A replacement represents a final retry result for the same
            # source/run rather than creating ambiguous multiple receipts.
            retry_receipt = {**receipt, "status": "failed"}
            store.record_scan_receipt(run_id, "arxiv", retry_receipt)
            store.fail_run(run_id, "arxiv transient failure")

            receipts = store.get_scan_receipts(run_id)
            self.assertEqual(1, len(receipts))
            self.assertEqual("failed", receipts[0]["status"])
            self.assertEqual("cs.AI", receipts[0]["domain_receipts"][0]["domain"])

            recent = store.get_recent_runs()
            self.assertEqual(run_id, recent[0]["run_id"])
            self.assertEqual("failed", recent[0]["status"])
            self.assertEqual("failed", recent[0]["receipts"][0]["status"])

    def test_scan_receipt_rejects_wrong_source_or_missing_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            receipt = {
                "source": "arxiv",
                "status": "succeeded",
                "scanned_at": "2026-08-13T08:00:00+00:00",
                "domain_receipts": [],
            }
            with self.assertRaisesRegex(KeyError, "daily run does not exist"):
                store.record_scan_receipt("missing", "arxiv", receipt)

            run_id = store.start_run(0)
            with self.assertRaisesRegex(ValueError, "来源不匹配"):
                store.record_scan_receipt(run_id, "openalex", receipt)

    def test_scan_receipt_table_is_added_to_pre_receipt_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE daily_runs (
                        run_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        total_papers INTEGER DEFAULT 0
                    )
                    """
                )
            store = DailyResearchStore(db_path)
            run_id = store.start_run(0)
            store.record_scan_receipt(
                run_id,
                "arxiv",
                {
                    "source": "arxiv",
                    "status": "succeeded",
                    "scanned_at": "2026-08-13T08:00:00+00:00",
                    "domain_receipts": [],
                },
            )
            self.assertEqual("succeeded", store.get_scan_receipts(run_id)[0]["status"])

    def test_successful_scan_advances_watermarks_and_failed_gap_expands_recovery_window(self):
        """A failed daily run must not let undelivered papers age out of the scan window."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            checkpoint_time = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)

            successful_run = store.start_run(0)
            self.assertEqual(
                store.prepare_scan(
                    successful_run,
                    configured_days=2,
                    sources=["arxiv", "prl"],
                    now=checkpoint_time,
                ),
                2,
            )
            # A no-new-paper run is still a complete scan and must establish
            # the checkpoint for every enabled source, but only after each
            # source has a durable terminal success receipt.
            for source in ("arxiv", "prl"):
                store.record_scan_receipt(successful_run, source, _source_receipt(source))
            store.complete_run(successful_run, {})

            for source in ("arxiv", "prl"):
                watermark = store.get_scan_watermark(source)
                self.assertEqual(watermark["run_id"], successful_run)
                self.assertEqual(watermark["successful_scan_started_at"], checkpoint_time.isoformat())

            failed_run = store.start_run(0)
            failed_at = checkpoint_time + timedelta(days=5, hours=4)
            self.assertEqual(
                store.prepare_scan(
                    failed_run,
                    configured_days=2,
                    sources=["arxiv", "prl"],
                    now=failed_at,
                ),
                6,
            )
            store.fail_run(failed_run, "translation unavailable")

            recovery_run = store.start_run(0)
            recovery_at = failed_at + timedelta(days=2)
            self.assertEqual(
                store.prepare_scan(
                    recovery_run,
                    configured_days=2,
                    sources=["arxiv", "prl"],
                    now=recovery_at,
                ),
                8,
            )
            # A failure must not advance the durable checkpoint.
            self.assertEqual(
                store.get_scan_watermark("arxiv")["successful_scan_started_at"],
                checkpoint_time.isoformat(),
            )

    def test_checkpoint_requires_successful_receipt_for_every_planned_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(0)
            store.prepare_scan(run_id, 2, ["arxiv", "prl"])
            store.record_scan_receipt(run_id, "arxiv", _source_receipt("arxiv"))

            with self.assertRaisesRegex(RuntimeError, "扫描收据不完整"):
                store.complete_run(run_id, {})

            store.record_scan_receipt(run_id, "prl", _source_receipt("prl", "failed"))
            with self.assertRaisesRegex(RuntimeError, "未成功: prl"):
                store.complete_run(run_id, {})

            with store._connect() as conn:
                state = conn.execute(
                    "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
                ).fetchone()["status"]
            self.assertEqual("running", state)
            self.assertIsNone(store.get_scan_watermark("arxiv"))
            self.assertIsNone(store.get_scan_watermark("prl"))

    def test_new_source_uses_normal_window_while_existing_source_recovers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            checkpoint_time = datetime(2026, 8, 1, tzinfo=timezone.utc)
            first_run = store.start_run(0)
            store.prepare_scan(first_run, 3, ["arxiv"], now=checkpoint_time)
            store.record_scan_receipt(first_run, "arxiv", _source_receipt("arxiv"))
            store.complete_run(first_run, {})

            next_run = store.start_run(0)
            # Adding a source must not trigger an accidental unbounded import;
            # the existing source still determines a safe recovery lookback.
            self.assertEqual(
                store.prepare_scan(
                    next_run,
                    3,
                    ["arxiv", "prl"],
                    now=checkpoint_time + timedelta(days=9),
                ),
                10,
            )

    def test_run_state_transitions_are_one_way(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")

            failed_run = store.start_run(0)
            store.fail_run(failed_run, "first failure")
            with self.assertRaisesRegex(RuntimeError, "只能完成 running"):
                store.complete_run(failed_run, {})
            store.fail_run(failed_run, "late replacement")
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT status, error FROM daily_runs WHERE run_id = ?",
                    (failed_run,),
                ).fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"], "first failure")

            completed_run = store.start_run(0)
            store.complete_run(completed_run, {})
            store.complete_run(completed_run, {})  # idempotent recovery call
            store.fail_run(completed_run, "late provider failure")
            with store._connect() as conn:
                row = conn.execute(
                    "SELECT status, error FROM daily_runs WHERE run_id = ?",
                    (completed_run,),
                ).fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertIsNone(row["error"])

    def test_corrupt_watermark_or_scan_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            checkpoint_run = store.start_run(0)
            store.prepare_scan(checkpoint_run, 2, ["arxiv"])
            store.record_scan_receipt(checkpoint_run, "arxiv", _source_receipt("arxiv"))
            store.complete_run(checkpoint_run, {})
            with store._connect() as conn:
                conn.execute(
                    "UPDATE daily_scan_watermarks SET successful_scan_started_at = ? "
                    "WHERE source = 'arxiv'",
                    ("not-a-timestamp",),
                )

            recovery_run = store.start_run(0)
            with self.assertRaisesRegex(RuntimeError, "水位线损坏"):
                store.prepare_scan(recovery_run, 2, ["arxiv"])

            plan_store = DailyResearchStore(Path(temp_dir) / "plan.db")
            plan_run = plan_store.start_run(0)
            plan_store.prepare_scan(plan_run, 2, ["arxiv"])
            with plan_store._connect() as conn:
                conn.execute(
                    "UPDATE daily_runs SET scanned_sources_json = ? WHERE run_id = ?",
                    ("{broken", plan_run),
                )
            with self.assertRaisesRegex(RuntimeError, "扫描计划损坏"):
                plan_store.complete_run(plan_run, {})
            with plan_store._connect() as conn:
                status = conn.execute(
                    "SELECT status FROM daily_runs WHERE run_id = ?", (plan_run,)
                ).fetchone()["status"]
            self.assertEqual(status, "running")

    def test_arxiv_identity_and_legacy_history_are_version_aware(self):
        self.assertEqual(split_arxiv_version("2501.12345v2"), ("2501.12345", 2))
        self.assertEqual(split_arxiv_version("hep-th/9901001"), ("hep-th/9901001", None))

        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            source.mark_as_processed("2501.12345v1")
            self.assertTrue(source.is_processed("2501.12345v1"))
            self.assertFalse(source.is_processed("2501.12345v2"))
            previous = source.get_previous_processed_version("2501.12345v2")
            self.assertEqual(previous["version"], 1)

            with open(Path(temp_dir) / "arxiv_history.json", "r", encoding="utf-8") as handle:
                history = json.load(handle)
            self.assertIn("2501.12345@v1", history)

    def test_store_migrates_old_schema_and_finds_previous_completed_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE daily_papers (
                        source TEXT NOT NULL,
                        paper_id TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        run_id TEXT,
                        paper_json TEXT NOT NULL,
                        score_json TEXT,
                        abstract_cn TEXT,
                        analysis_json TEXT,
                        scored_at TEXT,
                        translated_at TEXT,
                        analyzed_at TEXT,
                        completed_at TEXT,
                        last_error TEXT,
                        PRIMARY KEY (source, paper_id)
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO daily_papers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "arxiv",
                        "2501.12345v1",
                        "2026-01-01",
                        "2026-01-01",
                        "run-1",
                        "{}",
                        None,
                        "",
                        None,
                        None,
                        None,
                        None,
                        "2026-01-01T08:00:00",
                        None,
                    ),
                )

            store = DailyResearchStore(db_path)
            old = store.get_paper_record("arxiv", "2501.12345v1")
            self.assertEqual(old["canonical_id"], "2501.12345")
            self.assertEqual(old["version"], 1)

            run_id = store.start_run(1)
            v2 = _paper("2501.12345v2")
            store.upsert_paper_seen(run_id, "arxiv", v2)
            previous = store.get_previous_version_record("arxiv", v2)
            self.assertEqual(previous["paper_id"], "2501.12345v1")
            self.assertEqual(previous["version"], 1)

    def test_delivery_identity_migration_deduplicates_legacy_doi_aliases(self):
        """A DOI URL and bare DOI may merge after a legacy-history import."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "daily.db"
            doi_url = "https://doi.org/10.1103/example.123"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE paper_deliveries (
                        delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        paper_id TEXT NOT NULL,
                        canonical_id TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 0,
                        report_path TEXT,
                        delivered_at TEXT NOT NULL,
                        UNIQUE(run_id, source, paper_id)
                    )
                    """
                )
                conn.execute(
                    "CREATE UNIQUE INDEX idx_paper_deliveries_exact_version "
                    "ON paper_deliveries(source, canonical_id, version)"
                )
                conn.executemany(
                    """
                    INSERT INTO paper_deliveries(
                        run_id, source, paper_id, canonical_id, version,
                        report_path, delivered_at
                    ) VALUES (?, 'prl', ?, ?, 0, ?, ?)
                    """,
                    [
                        (
                            "first-run",
                            doi_url,
                            doi_url,
                            "first.html",
                            "2026-01-01T00:00:00",
                        ),
                        (
                            "second-run",
                            doi_url,
                            "10.1103/example.123",
                            "second.html",
                            "2026-01-02T00:00:00",
                        ),
                    ],
                )

            store = DailyResearchStore(db_path)
            with store._connect() as conn:
                deliveries = conn.execute(
                    "SELECT run_id, canonical_id, report_path FROM paper_deliveries"
                ).fetchall()
                indexes = conn.execute("PRAGMA index_list(paper_deliveries)").fetchall()

            self.assertEqual(len(deliveries), 1)
            self.assertEqual(
                dict(deliveries[0]),
                {
                    "run_id": "first-run",
                    "canonical_id": "10.1103/example.123",
                    "report_path": "first.html",
                },
            )
            self.assertTrue(
                any(
                    index[1] == "idx_paper_deliveries_exact_version" and index[2]
                    for index in indexes
                )
            )

    def test_report_status_label_identifies_revision_and_retry(self):
        paper = _paper("2501.12345v2")
        self.assertIn("修订版 v2", Reporter._paper_status_label({
            "paper_metadata": paper,
            "revision": {
                "version": 2,
                "previous_version": 1,
                "previous_pushed_at": "2026-01-01T08:00:00",
            },
        }))
        # 重试属于运维信息，不再在标题旁标注
        self.assertEqual(
            Reporter._paper_status_label({"paper_metadata": paper, "is_retry": True}),
            "",
        )
        # 普通版本不在标题旁标注 vN
        self.assertEqual(Reporter._paper_status_label({"paper_metadata": paper}), "")

    def test_stage_state_keeps_score_when_translation_or_analysis_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            store.upsert_paper_seen(run_id, "arxiv", paper)
            score = WeightedScoreResponse(
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
            scored = {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score}
            store.update_score(run_id, "arxiv", scored)
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "pending")
            self.assertIsNone(store.hydrate_scored_paper(paper, record))
            self.assertEqual(store.hydrate_scored_paper(paper, record, False)["score_response"].tldr,
                             "A concise TLDR")

            store.update_error(run_id, "arxiv", paper.paper_id, "translation down", stage="translation")
            failed = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(failed["translation_status"], "failed")
            self.assertEqual(failed["retry_count"], 1)

            store.update_translation(run_id, "arxiv", paper.paper_id, "中文摘要")
            store.update_analysis(run_id, "arxiv", paper.paper_id, {"summary": "analysis"})
            hydrated = store.hydrate_analysis(store.get_paper_record("arxiv", paper.paper_id))
            self.assertEqual(hydrated, {"summary": "analysis"})

    def test_corrupt_analysis_cache_is_cleared_and_marked_retryable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper("2501.12345v1")
            run_id = store.start_run(1)
            store.upsert_paper_seen(run_id, "arxiv", paper)
            store.update_analysis(run_id, "arxiv", paper.paper_id, {"summary": "analysis"})
            with store._connect() as conn:
                conn.execute(
                    "UPDATE daily_papers SET analysis_json = ? WHERE source = ? AND paper_id = ?",
                    ("{broken", "arxiv", paper.paper_id),
                )

            self.assertIsNone(store.hydrate_analysis(store.get_paper_record("arxiv", paper.paper_id)))
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["analysis_status"], "failed")
            self.assertIsNone(record["analysis_json"])
            self.assertEqual(record["retry_count"], 1)
            self.assertIn("缓存无效", record["last_error"])

    def test_nonrenderable_analysis_cache_is_cleared_and_marked_retryable(self):
        """A provider metadata/error object is not a completed analysis."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper("2501.12345v1")
            run_id = store.start_run(1)
            store.upsert_paper_seen(run_id, "arxiv", paper)
            store.update_analysis(run_id, "arxiv", paper.paper_id, {"summary": "analysis"})
            with store._connect() as conn:
                conn.execute(
                    "UPDATE daily_papers SET analysis_json = ? WHERE source = ? AND paper_id = ?",
                    ('{"provider_error": "empty output"}', "arxiv", paper.paper_id),
                )

            self.assertIsNone(store.hydrate_analysis(store.get_paper_record("arxiv", paper.paper_id)))
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["analysis_status"], "failed")
            self.assertIsNone(record["analysis_json"])
            self.assertEqual(record["retry_count"], 1)
            self.assertIn("可渲染内容", record["last_error"])

    def test_changed_score_input_invalidates_all_incomplete_downstream_stages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper("2501.12345v1")
            first_run = store.start_run(1)
            old_keys = {"score": "score-old", "translation": "translation-old", "analysis": "analysis-old"}
            store.upsert_paper_seen(first_run, "arxiv", paper, old_keys)
            score = WeightedScoreResponse(
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
            store.update_score(first_run, "arxiv", {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score}, "score-old")
            store.update_translation(first_run, "arxiv", paper.paper_id, "中文摘要", "translation-old")
            store.update_analysis(first_run, "arxiv", paper.paper_id, {"summary": "analysis"}, "analysis-old")

            retry_run = store.start_run(1)
            new_keys = {"score": "score-new", "translation": "translation-new", "analysis": "analysis-new"}
            store.upsert_paper_seen(retry_run, "arxiv", paper, new_keys)
            record = store.get_paper_record("arxiv", paper.paper_id)

            self.assertEqual(record["score_status"], "pending")
            self.assertEqual(record["translation_status"], "pending")
            self.assertEqual(record["analysis_status"], "pending")
            self.assertIsNone(record["score_json"])
            self.assertIsNone(record["abstract_cn"])
            self.assertIsNone(record["analysis_json"])
            self.assertEqual(record["score_input_fingerprint"], "score-new")

    def test_changed_translation_or_analysis_input_invalidates_only_that_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper("2501.12345v1")
            first_run = store.start_run(1)
            old_keys = {"score": "score-stable", "translation": "translation-old", "analysis": "analysis-old"}
            store.upsert_paper_seen(first_run, "arxiv", paper, old_keys)
            score = WeightedScoreResponse(
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
            store.update_score(first_run, "arxiv", {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score}, "score-stable")
            store.update_translation(first_run, "arxiv", paper.paper_id, "中文摘要", "translation-old")
            store.update_analysis(first_run, "arxiv", paper.paper_id, {"summary": "analysis"}, "analysis-old")

            retry_run = store.start_run(1)
            changed_translation = {"score": "score-stable", "translation": "translation-new", "analysis": "analysis-old"}
            store.upsert_paper_seen(retry_run, "arxiv", paper, changed_translation)
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "pending")
            self.assertEqual(record["analysis_status"], "pending")
            self.assertIsNotNone(record["score_json"])
            self.assertIsNone(record["abstract_cn"])
            self.assertIsNone(record["analysis_json"])

            changed_analysis = {"score": "score-stable", "translation": "translation-new", "analysis": "analysis-new"}
            store.upsert_paper_seen(retry_run, "arxiv", paper, changed_analysis)
            record = store.get_paper_record("arxiv", paper.paper_id)
            self.assertEqual(record["score_status"], "succeeded")
            self.assertEqual(record["translation_status"], "pending")
            self.assertEqual(record["analysis_status"], "pending")
            self.assertIsNone(record["analysis_json"])

    def test_exact_delivery_ledger_constraint_blocks_a_second_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            paper = _paper("2501.12345v1")
            run_one = store.start_run(1)
            store.upsert_paper_seen(run_one, "arxiv", paper)
            score = WeightedScoreResponse(
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
            store.update_score(run_one, "arxiv", {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score})
            store.update_translation(run_one, "arxiv", paper.paper_id, "中文摘要")
            delivered = {"arxiv": [{"paper_metadata": paper, "paper_id": paper.paper_id, "requires_analysis": False}]}
            store.finalize_report_delivery(run_one, {"arxiv": Path(temp_dir) / "one.md"}, delivered)

            run_two = store.start_run(1)
            with self.assertRaisesRegex(RuntimeError, "拒绝重复提交"):
                store.finalize_report_delivery(
                    run_two, {"arxiv": Path(temp_dir) / "two.md"}, delivered
                )
            with store._connect() as conn:
                rows = conn.execute(
                    "SELECT run_id, report_path FROM paper_deliveries "
                    "WHERE source = ? AND canonical_id = ? AND version = ?",
                    ("arxiv", paper.canonical_id, paper.version),
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], run_one)
            self.assertEqual(rows[0]["report_path"], str(Path(temp_dir) / "one.md"))

    def test_normal_delivery_records_report_time_without_expanding_legacy_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            score = WeightedScoreResponse(
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
            store.upsert_paper_seen(run_id, "arxiv", paper)
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
            )
            store.update_translation(run_id, "arxiv", paper.paper_id, "中文摘要")
            report_at = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
            store.finalize_report_delivery(
                run_id,
                {"arxiv": Path(temp_dir) / "report.md"},
                {"arxiv": [{"paper_metadata": paper, "paper_id": paper.paper_id, "requires_analysis": False}]},
                report_at=report_at,
            )

            with store._connect() as conn:
                delivery = conn.execute(
                    "SELECT report_at FROM paper_deliveries WHERE source = 'arxiv'"
                ).fetchone()
            self.assertEqual(delivery["report_at"], report_at.isoformat())
            self.assertIsNone(store.historical_delivery_date_range("arxiv"))

    def test_retry_preserves_optional_semantic_scholar_enrichment(self):
        """A transient S2 failure must not erase a TLDR from a retried paper."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            first_run = store.start_run(1)
            first = PaperMetadata(
                paper_id="10.9999/test.enriched",
                title="Journal paper",
                authors=["Author"],
                abstract="abstract",
                published_date=datetime.now(timezone.utc),
                url="https://doi.org/10.9999/test.enriched",
                source="prl",
                doi="10.9999/test.enriched",
                semantic_scholar_tldr="Persisted Semantic Scholar TLDR",
                arxiv_id="2501.12345v1",
                arxiv_url="https://arxiv.org/abs/2501.12345v1",
                pdf_url="https://arxiv.org/pdf/2501.12345v1.pdf",
            )
            store.upsert_paper_seen(first_run, "prl", first)

            # This models a restart where OpenAlex succeeds but Semantic
            # Scholar is temporarily unavailable and returns no enrichment.
            retry = PaperMetadata(
                paper_id=first.paper_id,
                title=first.title,
                authors=first.authors,
                abstract=first.abstract,
                published_date=first.published_date,
                url=first.url,
                source="prl",
                doi=first.doi,
            )
            retry_run = store.start_run(1)
            store.upsert_paper_seen(retry_run, "prl", retry)

            self.assertEqual(retry.semantic_scholar_tldr, first.semantic_scholar_tldr)
            self.assertEqual(retry.arxiv_id, first.arxiv_id)
            self.assertEqual(retry.pdf_url, first.pdf_url)
            record = store.get_paper_record("prl", retry.paper_id)
            persisted = json.loads(record["paper_json"])
            self.assertEqual(persisted["semantic_scholar_tldr"], first.semantic_scholar_tldr)

    def test_finalization_atomically_records_delivery_outbox_and_revision_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_v1 = store.start_run(1)
            v1 = _paper("2501.12345v1")
            store.upsert_paper_seen(run_v1, "arxiv", v1)
            score = WeightedScoreResponse(
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
            store.update_score(
                run_v1,
                "arxiv",
                {"paper_metadata": v1, "paper_id": v1.paper_id, "score_response": score},
            )
            store.update_translation(run_v1, "arxiv", v1.paper_id, "中文摘要")
            store.finalize_report_delivery(
                run_v1,
                {"arxiv": Path(temp_dir) / "v1.md"},
                {"arxiv": [{"paper_metadata": v1, "paper_id": v1.paper_id, "requires_analysis": False}]},
                [
                    {
                        "event_type": "daily_run_result",
                        "channel": "wechat_work",
                        "payload": {"result": {"run_timestamp": "2026-08-12"}},
                    }
                ],
            )

            self.assertTrue(store.is_paper_delivered("arxiv", v1.paper_id))
            self.assertEqual(store.get_pending_notification_count(), 1)

            run_v2 = store.start_run(1)
            v2 = _paper("2501.12345v2")
            store.upsert_paper_seen(run_v2, "arxiv", v2)
            previous = store.get_previous_version_record("arxiv", v2)
            self.assertEqual(previous["paper_id"], v1.paper_id)
            self.assertIsNotNone(previous["delivered_at"])

    def test_sqlite_delivery_ledger_prevents_duplicate_when_json_history_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            store.upsert_paper_seen(run_id, "arxiv", paper)
            score = WeightedScoreResponse(
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
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
            )
            store.update_translation(run_id, "arxiv", paper.paper_id, "中文摘要")
            store.finalize_report_delivery(
                run_id,
                {"arxiv": Path(temp_dir) / "report.md"},
                {"arxiv": [{"paper_metadata": paper, "paper_id": paper.paper_id, "requires_analysis": False}]},
            )

            filtered = _exclude_sqlite_delivered_papers(store, {"arxiv": [paper]})
            self.assertEqual(filtered, {"arxiv": []})

    def test_cross_source_arxiv_mirror_keeps_each_source_record(self):
        arxiv = _paper("2501.12345v2")
        mirror = _hf_paper("2501.12345")

        filtered = _exclude_cross_source_arxiv_mirrors(
            None,
            {"arxiv": [arxiv], "huggingface_papers": [mirror]},
        )

        self.assertEqual(filtered["arxiv"], [arxiv])
        self.assertEqual(filtered["huggingface_papers"], [mirror])

    def test_delivered_arxiv_keeps_late_hf_record_and_arxiv_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            v1 = _paper("2501.12345v1")
            score = WeightedScoreResponse(
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
            store.upsert_paper_seen(run_id, "arxiv", v1)
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": v1, "paper_id": v1.paper_id, "score_response": score},
            )
            store.update_translation(run_id, "arxiv", v1.paper_id, "中文摘要")
            store.finalize_report_delivery(
                run_id,
                {"arxiv": Path(temp_dir) / "report.md"},
                {"arxiv": [{"paper_metadata": v1, "paper_id": v1.paper_id, "requires_analysis": False}]},
            )

            mirror = _hf_paper("2501.12345")
            mirror_only = _exclude_cross_source_arxiv_mirrors(
                store,
                {"huggingface_papers": [mirror]},
            )
            v2 = _paper("2501.12345v2")
            revision_batch = _exclude_cross_source_arxiv_mirrors(
                store,
                {"arxiv": [v2]},
            )

        self.assertEqual(mirror_only["huggingface_papers"], [mirror])
        self.assertEqual(revision_batch["arxiv"], [v2])

    def test_hf_only_item_remains_when_no_arxiv_mirror_was_delivered(self):
        mirror = _hf_paper("2501.12345")
        unrelated = _paper("2501.99999v1")

        filtered = _exclude_cross_source_arxiv_mirrors(
            None,
            {"arxiv": [unrelated], "huggingface_papers": [mirror]},
        )

        self.assertEqual(filtered["huggingface_papers"], [mirror])

    def test_finalization_rejects_missing_translation_without_partial_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "daily.db")
            run_id = store.start_run(1)
            paper = _paper("2501.12345v1")
            store.upsert_paper_seen(run_id, "arxiv", paper)
            score = WeightedScoreResponse(
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
            store.update_score(
                run_id,
                "arxiv",
                {"paper_metadata": paper, "paper_id": paper.paper_id, "score_response": score},
            )

            with self.assertRaisesRegex(RuntimeError, "摘要翻译尚未完成"):
                store.finalize_report_delivery(
                    run_id,
                    {"arxiv": Path(temp_dir) / "report.md"},
                    {"arxiv": [{"paper_metadata": paper, "paper_id": paper.paper_id, "requires_analysis": False}]},
                )
            self.assertFalse(store.is_paper_delivered("arxiv", paper.paper_id))
            self.assertEqual(store.get_pending_notification_count(), 0)

    def test_history_batch_write_is_atomic_and_failure_does_not_mutate_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ArxivSource(Path(temp_dir))
            source.mark_many_as_processed(["2501.12345v1", "2501.12346v1"])
            history_path = Path(temp_dir) / "arxiv_history.json"
            before = history_path.read_text(encoding="utf-8")

            with patch("sources.base_source.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    source.mark_many_as_processed(["2501.12347v1"])

            self.assertEqual(history_path.read_text(encoding="utf-8"), before)
            self.assertFalse(source.is_processed("2501.12347v1"))
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_corrupt_legacy_history_fails_closed_unless_sqlite_mode_bypasses_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "arxiv_history.json"
            history_path.write_text("{not valid json", encoding="utf-8")
            source = ArxivSource(Path(temp_dir))

            with self.assertRaisesRegex(HistoryLoadError, "兼容历史不可用"):
                source.is_processed("2501.12345v1")
            with self.assertRaisesRegex(HistoryLoadError, "无法安全更新"):
                source.mark_as_processed("2501.12345v1")
            self.assertEqual(history_path.read_text(encoding="utf-8"), "{not valid json")

            source.set_history_filtering_enabled(False)
            self.assertFalse(source.is_processed("2501.12345v1"))


if __name__ == "__main__":
    unittest.main()
