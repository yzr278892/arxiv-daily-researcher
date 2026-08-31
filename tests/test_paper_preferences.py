import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _deliver_paper(store, source="arxiv", paper_id="2401.00001", title="A paper", authors=None, categories=None):
    store.set_paper_preference(
        source,
        paper_id,
        preference="like",
        title=title,
        authors=authors or ["Alice", "Bob"],
        categories=categories or ["quant-ph"],
    )


class PaperPreferenceTests(unittest.TestCase):
    def test_like_dislike_clear_never_deletes_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            store.set_paper_preference(
                "arxiv", "2401.00001", preference="like", title="T", authors=["A"], categories=["quant-ph"]
            )
            store.set_paper_preference(
                "arxiv", "2401.00001", preference="dislike", title="T", authors=["A"], categories=["quant-ph"]
            )
            store.set_paper_preference(
                "arxiv", "2401.00001", preference="none", title="T", authors=["A"], categories=["quant-ph"]
            )

            # 行仍在，状态是 none：清除是更新，不是删除。
            pref = store.get_paper_preference("arxiv", "2401.00001")
            self.assertEqual(pref["preference"], "none")
            self.assertEqual(store.get_preference_counts(), {"like": 0, "dislike": 0, "none": 1})
            self.assertEqual(store.list_preferences(), [])  # 默认跳过 none

    def test_invalid_preference_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            with self.assertRaises(ValueError):
                store.set_paper_preference("arxiv", "x", preference="meh", title="T")

    def test_aggregate_ranks_liked_authors_and_categories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            _deliver_paper(store, paper_id="1", authors=["Alice", "Bob"], categories=["quant-ph"])
            _deliver_paper(store, paper_id="2", authors=["Alice"], categories=["cond-mat"])
            _deliver_paper(store, paper_id="3", authors=["Alice"], categories=["quant-ph"])
            store.set_paper_preference(
                "arxiv", "4", preference="dislike", title="Disliked", authors=["Alice"], categories=["quant-ph"]
            )

            agg = store.aggregate_liked_preferences()
            self.assertEqual(agg["authors"][0], {"name": "Alice", "count": 3})
            self.assertEqual(agg["authors"][1], {"name": "Bob", "count": 1})
            self.assertEqual(agg["categories"][0], {"name": "quant-ph", "count": 2})
            # dislike 不进汇总
            self.assertEqual(sum(a["count"] for a in agg["authors"]), 4)

    def test_preference_map_skips_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            _deliver_paper(store, paper_id="1")
            store.set_paper_preference("arxiv", "2", preference="none", title="T")
            mapping = store.get_preference_map(
                [{"source": "arxiv", "paper_id": "1"}, {"source": "arxiv", "paper_id": "2"}]
            )
            self.assertEqual(mapping, {("arxiv", "1"): "like"})

    def test_auto_favorite_is_idempotent_and_preserves_reader_decisions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            added = store.add_auto_favorite_if_unmarked(
                "arxiv",
                "2401.00001v1",
                title="Automatic paper",
                canonical_id="2401.00001",
                version=1,
                authors=["Alice"],
                categories=["quant-ph"],
            )
            self.assertTrue(added)
            self.assertFalse(
                store.add_auto_favorite_if_unmarked(
                    "arxiv", "2401.00001v1", title="Changed title"
                )
            )
            preference = store.get_paper_preference("arxiv", "2401.00001v1")
            self.assertEqual(preference["preference"], "like")
            self.assertEqual(preference["title"], "Automatic paper")
            # Automatic saves are a reading-list convenience, not an explicit
            # user signal for learned-preference scoring.
            self.assertEqual(store.get_learned_preference_terms(), [])

            store.set_paper_preference(
                "arxiv", "2401.00001v1", preference="dislike", title="Manual"
            )
            self.assertFalse(
                store.add_auto_favorite_if_unmarked(
                    "arxiv", "2401.00001v1", title="Automatic retry"
                )
            )
            self.assertEqual(
                store.get_paper_preference("arxiv", "2401.00001v1")["preference"],
                "dislike",
            )

            store.set_paper_preference(
                "arxiv", "2401.00001v1", preference="none", title="Manual"
            )
            self.assertFalse(
                store.add_auto_favorite_if_unmarked(
                    "arxiv", "2401.00001v1", title="Automatic retry"
                )
            )
            self.assertEqual(
                store.get_paper_preference("arxiv", "2401.00001v1")["preference"],
                "none",
            )

    def test_collect_qualified_favorites_is_idempotent_and_keeps_reader_marks(self):
        import json

        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            papers = [
                (
                    "qualified",
                    {"title": "Qualified", "authors": ["Alice"], "categories": ["quant-ph"]},
                    {"is_qualified": True},
                ),
                (
                    "not-qualified",
                    {"title": "Not qualified", "authors": ["Bob"], "categories": ["hep-th"]},
                    {"is_qualified": False},
                ),
                (
                    "manual-mark",
                    {"title": "Manual mark", "authors": "not-a-list", "categories": "not-a-list"},
                    {"is_qualified": True},
                ),
            ]
            with store._connect() as conn:
                for paper_id, metadata, score in papers:
                    conn.execute(
                        "INSERT INTO daily_papers (source, paper_id, paper_json, score_json,"
                        " first_seen_at, last_seen_at, run_id) VALUES (?,?,?,?,?,?,?)",
                        (
                            "arxiv",
                            paper_id,
                            json.dumps(metadata),
                            json.dumps(score),
                            "2026-01-01",
                            "2026-01-01",
                            "run-1",
                        ),
                    )
            store.set_paper_preference(
                "arxiv", "manual-mark", preference="dislike", title="Manual mark"
            )

            first = store.collect_qualified_favorites()

            self.assertEqual(first, {"scanned": 3, "qualified": 2, "added": 1, "preserved": 1})
            favorite = store.get_paper_preference("arxiv", "qualified")
            self.assertEqual(favorite["preference"], "like")
            self.assertEqual(favorite["authors"], ["Alice"])
            self.assertEqual(favorite["categories"], ["quant-ph"])
            self.assertEqual(
                store.get_paper_preference("arxiv", "manual-mark")["preference"], "dislike"
            )
            self.assertIsNone(store.get_paper_preference("arxiv", "not-qualified"))

            second = store.collect_qualified_favorites()
            self.assertEqual(second, {"scanned": 3, "qualified": 2, "added": 0, "preserved": 2})

    def _seed_daily_paper(self, store, paper_id, *, url=None, keywords=None, liked=True):
        import json
        import sqlite3

        paper_json = json.dumps(
            {"title": f"Paper {paper_id}", "authors": ["A"], "url": url}
        )
        score_json = json.dumps({"extracted_keywords": keywords or []})
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO daily_papers (source, paper_id, paper_json, score_json,"
                " first_seen_at, last_seen_at, run_id) VALUES (?,?,?,?,?,?,?)",
                ("arxiv", paper_id, paper_json, score_json, "2026-01-01", "2026-01-01", "r"),
            )
        if liked:
            store.set_paper_preference(
                "arxiv", paper_id, preference="like", title=f"Paper {paper_id}"
            )

    def test_liked_keyword_aggregation_counts_extracted_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            self._seed_daily_paper(store, "1", keywords=["error correction", "topological"])
            self._seed_daily_paper(store, "2", keywords=["error correction"])
            self._seed_daily_paper(store, "3", keywords=["error correction"], liked=False)
            store.set_paper_preference("arxiv", "3", preference="dislike", title="Paper 3")

            ranked = store.aggregate_liked_keywords()
            # 只统计 like 的论文；同频按字母序
            self.assertEqual(
                ranked,
                [
                    {"keyword": "error correction", "count": 2},
                    {"keyword": "topological", "count": 1},
                ],
            )

    def test_liked_paper_urls_come_from_stored_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DailyResearchStore(Path(temp_dir) / "state.db")
            self._seed_daily_paper(store, "1", url="https://arxiv.org/abs/2401.00001v2")
            self._seed_daily_paper(store, "2", url=None)
            self._seed_daily_paper(store, "3", url="https://arxiv.org/abs/2401.00003", liked=False)
            store.set_paper_preference("arxiv", "3", preference="none", title="Paper 3")

            urls = store.liked_paper_urls()
            self.assertEqual(
                urls, {("arxiv", "1"): "https://arxiv.org/abs/2401.00001v2"}
            )


if __name__ == "__main__":
    unittest.main()


class QueueDepthTests(unittest.TestCase):
    def test_pending_counts_split_fresh_and_failed(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            store = DailyResearchStore(db_path)
            conn = sqlite3.connect(db_path)
            # 三篇：一篇已完成、一篇全新待处理、一篇评分失败待重试。
            rows = [
                ("arxiv", "done1", "succeeded", "succeeded", "not_required", "2026-01-01T00:00:00"),
                ("arxiv", "fresh1", "pending", "pending", "pending", None),
                ("arxiv", "fail1", "failed", "pending", "pending", None),
            ]
            for source, pid, sc, tr, an, completed in rows:
                conn.execute(
                    "INSERT INTO daily_papers (source, paper_id, paper_json, score_status,"
                    " translation_status, analysis_status, completed_at, first_seen_at,"
                    " last_seen_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (source, pid, "{}", sc, tr, an, completed, "2026-01-01", "2026-01-01", "r"),
                )
            conn.commit()
            conn.close()

            counts = store.count_pending_papers()
            self.assertEqual(counts["total"], 2)
            self.assertEqual(counts["failed_retry"], 1)
            self.assertEqual(counts["fresh"], 1)
