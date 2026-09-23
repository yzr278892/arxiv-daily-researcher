"""关键词趋势追踪融合 SQLite 的回归测试。

原始关键词不再单独收集：KeywordDatabase 直接读每日研究库里
已完成论文的 extracted_keywords，标准化/别名/每日统计表寄宿同一个 db 文件。
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from keyword_tracker.database import KeywordDatabase  # noqa: E402
from utils.daily_research_store import DailyResearchStore  # noqa: E402


def _insert_completed_paper(
    db_path: Path,
    paper_id: str,
    keywords: list[str],
    completed_day: str,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO daily_papers (source, paper_id, first_seen_at, last_seen_at,"
        " paper_json, score_json, completed_at)"
        " VALUES ('arxiv', ?, ?, ?, ?, ?, ?)",
        (
            paper_id,
            f"{completed_day}T08:00:00",
            f"{completed_day}T08:05:00",
            json.dumps({"title": f"paper {paper_id}", "authors": []}),
            json.dumps({"total_score": 5.0, "extracted_keywords": keywords}),
            f"{completed_day}T09:00:00",
        ),
    )
    conn.commit()
    conn.close()


def _create_v32_keyword_database(path: Path) -> None:
    """Create the relevant v3.2 keyword-store schema and representative rows."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            source TEXT NOT NULL,
            extracted_date DATE NOT NULL,
            normalized_keyword_id INTEGER
        );
        CREATE TABLE normalized_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_keyword TEXT NOT NULL UNIQUE,
            category TEXT
        );
        CREATE TABLE keyword_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_keyword TEXT NOT NULL UNIQUE,
            normalized_keyword_id INTEGER NOT NULL,
            confidence REAL DEFAULT 1.0
        );
        CREATE TABLE keyword_daily_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_keyword_id INTEGER NOT NULL,
            count_date DATE NOT NULL,
            paper_count INTEGER DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO normalized_keywords (id, canonical_keyword, category) VALUES (1, ?, ?)",
        ("quantum error correction", "quantum"),
    )
    conn.executemany(
        "INSERT INTO keyword_aliases (raw_keyword, normalized_keyword_id, confidence) VALUES (?, 1, ?)",
        [("qec", 0.9), ("entanglement", 0.8)],
    )
    conn.executemany(
        "INSERT INTO keywords (keyword, paper_id, source, extracted_date) VALUES (?, ?, ?, ?)",
        [
            ("QEC", "2401.00001", "arxiv", "2026-08-20"),
            ("Entanglement", "2401.00002", "arxiv", "2026-08-21"),
            ("", "broken", "arxiv", "2026-08-21"),
        ],
    )
    conn.commit()
    conn.close()


class KeywordFusionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "daily_research.db"
        # 用真实 store 初始化 schema（keyword 表与论文表共存）
        DailyResearchStore(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_keywords_are_read_from_paper_store(self):
        _insert_completed_paper(
            self.db_path, "2401.00001", ["Quantum Error Correction", "qubit"], "2026-08-20"
        )
        db = KeywordDatabase(self.db_path)

        unnormalized = db.get_unique_unnormalized_keywords()
        self.assertEqual(
            sorted(unnormalized), ["quantum error correction", "qubit"]
        )
        # 论文库里的关键词不计入未完成记录
        stats = db.get_stats()
        self.assertEqual(stats["total_keywords"], 2)
        self.assertEqual(stats["normalized_keywords"], 0)

    def test_alias_normalization_and_daily_counts(self):
        first_day = date.today() - timedelta(days=3)
        second_day = first_day + timedelta(days=1)
        _insert_completed_paper(
            self.db_path, "2401.00001", ["Quantum Error Correction"], first_day.isoformat()
        )
        _insert_completed_paper(
            self.db_path, "2401.00002", ["quantum error correction"], first_day.isoformat()
        )
        _insert_completed_paper(
            self.db_path, "2401.00003", ["QEC"], second_day.isoformat()
        )
        db = KeywordDatabase(self.db_path)

        canonical_id = db.get_or_create_normalized_keyword("quantum error correction")
        db.add_keyword_alias("quantum error correction", canonical_id)
        db.add_keyword_alias("qec", canonical_id)

        self.assertEqual(db.count_papers_with_keyword("QEC"), 1)
        self.assertEqual(db.get_unique_unnormalized_keywords(), [])

        db.update_daily_counts()

        # 别名覆盖后，第一天两篇、第二天一篇。
        trends = db.get_keyword_trends(days=30, keywords=["quantum error correction"])
        self.assertEqual(len(trends), 1)
        daily = trends[0].daily_counts
        self.assertEqual(daily[first_day], 2)
        self.assertEqual(daily[second_day], 1)

        top = db.get_top_keywords(days=30)
        self.assertEqual(top[0][0], "quantum error correction")
        self.assertEqual(top[0][1], 3)

    def test_malformed_score_json_is_skipped(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO daily_papers (source, paper_id, first_seen_at, last_seen_at,"
            " paper_json, score_json, completed_at)"
            " VALUES ('arxiv', 'bad', '2026-08-20T08:00:00', '2026-08-20T08:00:00',"
            " '{}', 'not-json', '2026-08-20T09:00:00')"
        )
        conn.commit()
        conn.close()
        _insert_completed_paper(self.db_path, "ok", ["qubit"], "2026-08-20")

        db = KeywordDatabase(self.db_path)
        self.assertEqual(db.get_unique_unnormalized_keywords(), ["qubit"])
        db.update_daily_counts()  # 不抛异常即可

    def test_trends_respect_window(self):
        old_day = (date.today() - timedelta(days=40)).isoformat()
        _insert_completed_paper(self.db_path, "old", ["qubit"], old_day)
        db = KeywordDatabase(self.db_path)
        canonical_id = db.get_or_create_normalized_keyword("qubit")
        db.add_keyword_alias("qubit", canonical_id)
        db.update_daily_counts()

        self.assertEqual(db.get_top_keywords(days=30), [])
        self.assertEqual(db.get_top_keywords(days=60)[0][1], 1)

    def test_v32_keywords_database_merges_raw_records_and_aliases_idempotently(self):
        legacy_path = Path(self._tmp.name) / "keywords.db"
        _create_v32_keyword_database(legacy_path)
        db = KeywordDatabase(self.db_path)

        # A v4 decision made after upgrade is authoritative over an old alias.
        current_id = db.get_or_create_normalized_keyword("current qec preference")
        db.add_keyword_alias("qec", current_id)

        first = db.import_legacy_database(legacy_path)

        self.assertEqual(first["state"], "imported")
        self.assertEqual(first["records_scanned"], 3)
        self.assertEqual(first["records_imported"], 2)
        self.assertEqual(first["records_invalid"], 1)
        self.assertEqual(first["normalized_terms_imported"], 1)
        self.assertEqual(first["aliases_imported"], 1)
        self.assertEqual(first["aliases_preserved"], 1)
        self.assertEqual(db.get_unique_unnormalized_keywords(), [])

        conn = sqlite3.connect(self.db_path)
        records = conn.execute(
            "SELECT source, paper_id, keyword, extracted_date "
            "FROM legacy_keyword_records ORDER BY paper_id"
        ).fetchall()
        qec_alias = conn.execute(
            "SELECT normalized_keyword_id FROM keyword_aliases WHERE raw_keyword = 'qec'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(
            records,
            [
                ("arxiv", "2401.00001", "qec", "2026-08-20"),
                ("arxiv", "2401.00002", "entanglement", "2026-08-21"),
            ],
        )
        self.assertEqual(qec_alias, current_id)
        self.assertEqual(db.count_papers_with_keyword("qec"), 1)

        second = db.import_legacy_database(legacy_path)
        self.assertEqual(second["state"], "imported")
        self.assertEqual(second["records_imported"], 0)

    def test_readonly_snapshot_fallback_handles_clean_wal_mode_archive(self):
        """A read-only bind mount can require immutable=1 without losing WAL data."""
        legacy_path = Path(self._tmp.name) / "keywords.db"
        _create_v32_keyword_database(legacy_path)
        original_connect = sqlite3.connect
        calls = []

        def fail_only_normal_readonly(database, *args, **kwargs):
            calls.append(str(database))
            if "mode=ro" in str(database) and "immutable=1" not in str(database):
                raise sqlite3.OperationalError("unable to open database file")
            return original_connect(database, *args, **kwargs)

        with patch("keyword_tracker.database.sqlite3.connect", side_effect=fail_only_normal_readonly):
            source, mode = KeywordDatabase._open_legacy_database_readonly(legacy_path)
        try:
            self.assertEqual(mode, "immutable_snapshot")
            self.assertEqual(source.execute("SELECT COUNT(*) FROM keywords").fetchone()[0], 3)
        finally:
            source.close()
        self.assertEqual(len(calls), 2)
        self.assertIn("immutable=1", calls[-1])

    def test_readonly_snapshot_refuses_an_uncheckpointed_wal(self):
        legacy_path = Path(self._tmp.name) / "keywords.db"
        _create_v32_keyword_database(legacy_path)
        legacy_path.with_name(legacy_path.name + "-wal").write_bytes(b"pending")

        with patch(
            "keyword_tracker.database.sqlite3.connect",
            side_effect=sqlite3.OperationalError("unable to open database file"),
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "未合并的 -wal"):
                KeywordDatabase._open_legacy_database_readonly(legacy_path)

    def test_missing_v32_keywords_database_is_an_explicit_non_error(self):
        db = KeywordDatabase(self.db_path)

        summary = db.import_legacy_database(Path(self._tmp.name) / "missing.db")

        self.assertEqual(summary["state"], "not_found")
        self.assertEqual(summary["records_imported"], 0)

    def test_v32_migration_emits_bounded_progress(self):
        legacy_path = Path(self._tmp.name) / "keywords.db"
        _create_v32_keyword_database(legacy_path)
        events = []

        KeywordDatabase(self.db_path).import_legacy_database(
            legacy_path,
            progress_callback=lambda **event: events.append(event),
        )

        self.assertTrue(events)
        self.assertTrue(all(event["phase"] == "legacy_keywords" for event in events))
        self.assertTrue(
            any(event.get("current") == 3 and event.get("total") == 3 for event in events)
        )


if __name__ == "__main__":
    unittest.main()
