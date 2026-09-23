"""v3.2 旧历史导入：HTML 卡片解析、最新覆盖、交付账本与补充积压。"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.daily_research_store import DailyResearchStore  # noqa: E402
from utils.legacy_history import (  # noqa: E402
    import_legacy_history,
    load_legacy_history_files,
    parse_legacy_report_cards,
)


def _card_fail(idx: int, paper_id: str, title: str) -> str:
    return f"""<div class="card fail">
<div class="card-title"><a href="http://arxiv.org/abs/{paper_id}" target="_blank">{idx}. {title}</a>
<span class="badge fail">FAIL</span></div>
<div class="field"><span class="field-label">Score:</span> <span class="score">2.4</span> / 16.4</div>
<div class="field"><span class="field-label">Authors:</span> Yonghae Lee, Taewan Kim</div>
<div class="field"><span class="field-label">Published:</span> 2026-03-01</div>
<div class="tldr"><strong>TL;DR:</strong> 该论文提出了一个测试摘要总结。</div>
<details open><summary>摘要翻译</summary>
<div class="analysis-content"><p>这是一段中文翻译摘要。</p></div></details>
<details><summary>Abstract</summary>
<div class="analysis-content"><p>This is the original abstract.</p></div></details>
<details><summary>评分详情</summary>
<div class="analysis-content"><table style="width:100%;border-collapse:collapse;font-size:0.85em;">
<tr style="border-bottom:2px solid var(--color-border);"><th style="text-align:left;padding:4px 8px;">关键词</th><th style="text-align:center;padding:4px 8px;">权重</th><th style="text-align:center;padding:4px 8px;">相关度</th><th style="text-align:center;padding:4px 8px;">得分</th></tr>
<tr style="border-bottom:1px solid var(--color-border);"><td style="padding:4px 8px;">Entanglement</td><td style="text-align:center;padding:4px 8px;">1.0</td><td style="text-align:center;padding:4px 8px;">2.4/10</td><td style="text-align:center;padding:4px 8px;">2.4</td></tr>
</table>
<p style="margin-top:8px;"><strong>评分理由:</strong> 关键词匹配度较低。</p></div></details>
<details><summary>关键词</summary>
<div class="analysis-content"><p>entanglement, state preparation</p></div></details>
</div>"""


_CARD_PASS_WITH_ANALYSIS = """<div class="card pass">
<div class="card-title"><a href="http://arxiv.org/abs/2603.11111v1" target="_blank" rel="noopener noreferrer">1. A Quantum Advantage Paper</a>
<span class="badge pass">PASS</span></div>
<div class="field"><span class="field-label">Score:</span> <span class="score">20.0</span> / 11.0</div>
<div class="field"><span class="field-label">Authors:</span> Bob Author</div>
<div class="field"><span class="field-label">Published:</span> 2026-03-02</div>
<div class="field"><span class="field-label">Version:</span> v1</div>
<div class="tldr"><strong>TL;DR:</strong> 中文一句话总结。</div>
<details open><summary>摘要翻译</summary>
<div class="analysis-content"><p>中文摘要翻译内容。</p></div></details>
<details><summary>Abstract</summary>
<div class="analysis-content"><p>Original abstract text.</p></div></details>
<details><summary>深度分析</summary>
<div class="analysis-content">
<p><strong>Chinese Title:</strong> 一篇量子优势论文</p>
<p><strong>Summary:</strong> 概括论文的主要内容。</p>
<p><strong>Innovations:</strong></p><ul>
<li>创新点一</li>
<li>创新点二</li>
</ul>
<p><strong>Key Results:</strong> 关键结果描述</p>
</div></details>
</div>"""

_CARD_PASS_MISSING_ANALYSIS = """<div class="card pass">
<div class="card-title"><a href="http://arxiv.org/abs/2603.22222v1" target="_blank">1. Pass Without Analysis</a>
<span class="badge pass">PASS</span></div>
<div class="field"><span class="field-label">Score:</span> <span class="score">18.0</span> / 11.0</div>
<div class="field"><span class="field-label">Authors:</span> Carol Author</div>
<div class="field"><span class="field-label">Published:</span> 2026-03-04</div>
<details open><summary>摘要翻译</summary>
<div class="analysis-content"><p>翻译好的摘要。</p></div></details>
<details><summary>Abstract</summary>
<div class="analysis-content"><p>Abstract text.</p></div></details>
</div>"""

_CARD_DOI = """<div class="card pass">
<div class="card-title"><a href="https://doi.org/10.1103/ab12-cd34" target="_blank">1. Journal Paper Title</a>
<span class="badge pass">PASS</span></div>
<div class="field"><span class="field-label">Score:</span> <span class="score">15.0</span> / 11.0</div>
<div class="field"><span class="field-label">Authors:</span> Dana Author</div>
<div class="field"><span class="field-label">Published:</span> 2026-03-05</div>
<details open><summary>摘要翻译</summary>
<div class="analysis-content"><p>期刊论文的中文摘要。</p></div></details>
<details><summary>Abstract</summary>
<div class="analysis-content"><p>Journal abstract.</p></div></details>
</div>"""


def _report_html(body: str, generated: str, passing: float) -> str:
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><style>"
        ".card{border:1px solid}</style></head><body>"
        "<h1>ArXiv Research Report</h1>"
        f'<p class="meta">Generated: {generated} | Passing score: {passing}</p>'
        '<div class="stats-bar"><div class="stat"><div class="num">1</div>'
        '<div class="label">Total</div></div></div>'
        "<h2>Papers</h2>"
        f"{body}</body></html>"
    )


def _write_report(root: Path, source: str, stamp: str, body: str) -> Path:
    directory = root / "html" / source
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source.upper()}_Report_{stamp}.html"
    path.write_text(_report_html(body, stamp.replace("_", " ").replace("-", ":")[:19], 11.0), encoding="utf-8")
    return path


def _card_without_keyword_section(idx: int, paper_id: str, title: str) -> str:
    """Build a normally scored card whose report did not expose raw terms."""
    card = _card_fail(idx, paper_id, title)
    marker = "<details><summary>关键词</summary>"
    start = card.index(marker)
    end = card.index("</details>", start) + len("</details>")
    return card[:start] + card[end:]


def _write_v32_keyword_cache(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """Create just enough v3.2 cache schema for report-scoped fallback tests."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE keywords (keyword TEXT, paper_id TEXT, source TEXT, extracted_date TEXT)"
    )
    conn.executemany(
        "INSERT INTO keywords(keyword, paper_id, source, extracted_date) VALUES (?, ?, ?, '2026-03-03')",
        rows,
    )
    conn.commit()
    conn.close()


class LegacyCardParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parses_fail_card_fields_and_identity(self):
        _write_report(
            self.root,
            "arxiv",
            "2026-03-03_16-10-08",
            _card_fail(1, "2603.00845v1", "Three-Qubit State Preparation"),
        )
        cards = parse_legacy_report_cards(self.root / "html")
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["source"], "arxiv")
        self.assertEqual(card["paper_id"], "2603.00845v1")
        self.assertEqual(card["canonical_id"], "2603.00845")
        self.assertEqual(card["version"], 1)
        self.assertEqual(card["title"], "Three-Qubit State Preparation")
        self.assertEqual(card["authors"], ["Yonghae Lee", "Taewan Kim"])
        self.assertEqual(card["abstract"], "This is the original abstract.")
        self.assertEqual(card["abstract_cn"], "这是一段中文翻译摘要。")
        self.assertFalse(card["score_payload"]["is_qualified"])
        self.assertAlmostEqual(card["score_payload"]["total_score"], 2.4)
        self.assertAlmostEqual(card["score_payload"]["passing_score"], 16.4)
        self.assertEqual(card["score_payload"]["keyword_scores"], {"Entanglement": 2.4})
        self.assertEqual(card["score_payload"]["extracted_keywords"], ["entanglement", "state preparation"])
        self.assertEqual(card["report_at"], datetime(2026, 3, 3, 16, 10, 8))

    def test_penalty_report_keeps_negative_score_and_separate_keyword_details(self):
        card = _card_fail(1, "2603.00845v1", "Downweighted Topic").replace(
            '<span class="score">2.4</span>', '<span class="score">-2.1</span>'
        ).replace(
            '</table>',
            '<tr><td>不关注：Noise</td><td>-0.50</td><td>9.0/10</td><td>-4.5</td></tr></table>',
        )
        _write_report(self.root, "arxiv", "2026-03-03_16-10-08", card)

        payload = parse_legacy_report_cards(self.root / "html")[0]["score_payload"]
        self.assertEqual(payload["total_score"], -2.1)
        self.assertEqual(payload["strategy_id"], "weighted_keyword_with_penalties_v1")
        self.assertEqual(payload["keyword_scores"], {"Entanglement": 2.4})
        self.assertEqual(payload["negative_keyword_scores"], {"Noise": 9.0})
        self.assertEqual(payload["negative_keyword_weights"], {"Noise": 0.5})
        self.assertEqual(payload["negative_keyword_penalty"], 4.5)

    def test_parses_deep_analysis_sections_into_field_ids(self):
        _write_report(self.root, "arxiv", "2026-04-17_08-15-37", _CARD_PASS_WITH_ANALYSIS)
        cards = parse_legacy_report_cards(self.root / "html")
        analysis = cards[0]["analysis"]
        self.assertEqual(analysis["chinese_title"], "一篇量子优势论文")
        self.assertEqual(analysis["summary"], "概括论文的主要内容。")
        self.assertEqual(analysis["innovations"], ["创新点一", "创新点二"])
        self.assertEqual(analysis["key_results"], "关键结果描述")

    def test_doi_card_identity_uses_normalized_doi(self):
        _write_report(self.root, "prl", "2026-04-11_08-13-24", _CARD_DOI)
        cards = parse_legacy_report_cards(self.root / "html")
        card = cards[0]
        self.assertEqual(card["source"], "prl")
        self.assertEqual(card["canonical_id"], "10.1103/ab12-cd34")
        self.assertEqual(card["version"], 0)
        self.assertEqual(card["paper_id"], "https://doi.org/10.1103/ab12-cd34")

    def test_score_json_is_valid_weighted_score_response(self):
        _write_report(self.root, "arxiv", "2026-03-03_16-10-08", _card_fail(1, "2603.00845v1", "Title"))
        cards = parse_legacy_report_cards(self.root / "html")
        from agents.analysis_agent import WeightedScoreResponse

        payload = {**cards[0]["score_payload"]}
        payload.setdefault("strategy_id", "legacy_weighted_keyword_v1")
        model = WeightedScoreResponse.model_validate(payload)
        self.assertAlmostEqual(model.total_score, 2.4)
        self.assertEqual(model.keyword_scores, {"Entanglement": 2.4})

    def test_report_parser_emits_file_level_progress(self):
        _write_report(
            self.root,
            "arxiv",
            "2026-03-03_16-10-08",
            _card_fail(1, "2603.00845v1", "Three-Qubit State Preparation"),
        )
        events = []

        parse_legacy_report_cards(
            self.root / "html",
            progress_callback=lambda **event: events.append(event),
        )

        self.assertTrue(events)
        self.assertEqual(events[0]["phase"], "legacy_reports")
        self.assertTrue(
            any(
                event["current"] == 1 and event["total"] == 1
                for event in events
            )
        )


class LegacyHistoryLoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_arxiv_and_openalex_keys(self):
        (self.root / "arxiv_history.json").write_text(
            json.dumps(
                {
                    "2602.03848v1": "2026-02-04T23:48:23.286228",
                    "2602.03843@v2": "2026-02-06T01:00:00.000000",
                }
            ),
            encoding="utf-8",
        )
        (self.root / "openalex_history.json").write_text(
            json.dumps({"https://doi.org/10.1103/sl32-jn82": "2026-02-05T01:12:51.385030"}),
            encoding="utf-8",
        )
        histories = load_legacy_history_files(self.root)
        self.assertEqual(histories["arxiv"][("2602.03848", 1)], "2026-02-04T23:48:23.286228")
        self.assertEqual(histories["arxiv"][("2602.03843", 2)], "2026-02-06T01:00:00.000000")
        self.assertIn(("10.1103/sl32-jn82", 0), histories["openalex"])

    def test_skips_archived_v1_history_file(self):
        (self.root / "history_old_v1.0.json").write_text(json.dumps({"x": "1"}), encoding="utf-8")
        histories = load_legacy_history_files(self.root)
        self.assertEqual(histories, {})


class LegacyImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.history_dir = self.root / "history"
        self.history_dir.mkdir()
        self.reports_dir = self.root / "reports"
        self.store = DailyResearchStore(self.root / "daily_research.db")
        self.run_id = self.store.start_run(0, run_kind="legacy_import")

    def tearDown(self):
        self.tmp.cleanup()

    def _import(self, *, full_repair: bool = False) -> dict:
        return import_legacy_history(
            self.store,
            history_dir=self.history_dir,
            reports_html_dir=self.reports_dir / "html",
            delivery_run_id=self.run_id,
            full_repair=full_repair,
        )

    def test_complete_cards_get_paper_rows_and_delivery_ledger(self):
        (self.history_dir / "arxiv_history.json").write_text(
            json.dumps({"2603.00845v1": "2026-03-03T23:48:23.286228"}), encoding="utf-8"
        )
        _write_report(
            self.reports_dir,
            "arxiv",
            "2026-03-03_16-10-08",
            _card_fail(1, "2603.00845v1", "Three-Qubit State Preparation"),
        )
        summary = self._import()
        self.assertEqual(summary["cards_found"], 1)
        self.assertEqual(summary["delivered_ledger_rows"], 1)
        record = self.store.get_paper_record("arxiv", "2603.00845v1")
        self.assertIsNotNone(record)
        self.assertEqual(record["score_status"], "succeeded")
        self.assertEqual(record["translation_status"], "succeeded")
        self.assertEqual(record["analysis_status"], "not_required")  # FAIL 不需要分析
        self.assertEqual(record["completed_at"], "2026-03-03T16:10:08")
        self.assertTrue(self.store.is_paper_delivered("arxiv", "2603.00845v1"))
        self.assertEqual(self.store.supplement_backlog_summary()["pending"], 0)

    def test_newest_report_overwrites_older_duplicate(self):
        (self.history_dir / "arxiv_history.json").write_text(
            json.dumps({"2603.00845v1": "2026-03-04T23:48:23.286228"}), encoding="utf-8"
        )
        _write_report(
            self.reports_dir, "arxiv", "2026-03-03_16-10-08",
            _card_fail(1, "2603.00845v1", "Old Title"),
        )
        _write_report(
            self.reports_dir, "arxiv", "2026-03-04_22-33-07",
            _card_fail(1, "2603.00845v1", "New Title"),
        )
        self._import()
        record = self.store.get_paper_record("arxiv", "2603.00845v1")
        self.assertIn("New Title", record["paper_json"])

    def test_later_report_reimport_wins_when_json_delivery_time_is_unchanged(self):
        """Newest-wins must use the HTML artifact time, not JSON delivery time."""
        (self.history_dir / "arxiv_history.json").write_text(
            json.dumps({"2603.00845v1": "2026-03-10T23:48:23.286228"}),
            encoding="utf-8",
        )
        old_path = _write_report(
            self.reports_dir,
            "arxiv",
            "2026-03-03_16-10-08",
            _card_fail(1, "2603.00845v1", "Old Imported Title"),
        )
        self._import(full_repair=True)
        self.store.complete_run(self.run_id, {})

        newer_path = _write_report(
            self.reports_dir,
            "arxiv",
            "2026-03-09_18-30-00",
            _card_fail(1, "2603.00845v1", "Newly Added Report Title"),
        )
        rerun_id = self.store.start_run(0, run_kind="legacy_import")
        summary = import_legacy_history(
            self.store,
            history_dir=self.history_dir,
            reports_html_dir=self.reports_dir / "html",
            delivery_run_id=rerun_id,
            full_repair=True,
        )

        self.assertEqual(summary["imported"], 1)
        record = self.store.get_paper_record("arxiv", "2603.00845v1")
        self.assertIn("Newly Added Report Title", record["paper_json"])
        self.assertEqual(record["legacy_report_at"], "2026-03-09T18:30:00")
        with self.store._connect() as conn:
            delivery = conn.execute(
                "SELECT report_path, report_at, delivered_at FROM paper_deliveries "
                "WHERE source = 'arxiv' AND canonical_id = '2603.00845' AND version = 1"
            ).fetchone()
        self.assertEqual(delivery["report_path"], str(newer_path))
        self.assertEqual(delivery["report_at"], "2026-03-09T18:30:00")
        # The user-visible original delivery timestamp remains the JSON value.
        self.assertEqual(delivery["delivered_at"], "2026-03-10T23:48:23.286228")
        self.assertNotEqual(old_path, newer_path)

    def test_missing_fields_stay_delivered_and_become_sqlite_repair_candidates(self):
        _write_report(self.reports_dir, "arxiv", "2026-05-01_08-00-00", _CARD_PASS_MISSING_ANALYSIS)
        summary = self._import()
        self.assertEqual(summary["missing_analysis"], 1)
        self.assertEqual(summary["missing_tldr"], 1)
        self.assertEqual(summary["delivered_ledger_rows"], 1)
        # 已有 HTML 卡片始终是已交付论文：未来日报不会再次推送；缺失
        # 字段由 SQLite 驱动的原报告修补任务处理。
        with self.store._connect() as conn:
            ledger_rows = conn.execute(
                "SELECT COUNT(*) FROM paper_deliveries WHERE source = 'arxiv' "
                "AND canonical_id = '2603.22222'"
            ).fetchone()[0]
        self.assertEqual(ledger_rows, 1)
        record = self.store.get_paper_record("arxiv", "2603.22222v1")
        self.assertIsNotNone(record["completed_at"])
        self.assertEqual(self.store.supplement_backlog_summary()["pending"], 0)
        candidates = self.store.history_repair_candidates()
        candidate = next(item for item in candidates if item["paper_id"] == "2603.22222v1")
        self.assertIn("tldr", candidate["needs"])
        self.assertIn("analysis", candidate["needs"])

    def test_qualified_legacy_journal_card_is_not_false_missing_analysis(self):
        # v3.2 only performed PDF deep analysis for arXiv.  A qualified
        # journal/OpenAlex card is complete without a deep-analysis section.
        _write_report(
            self.reports_dir,
            "prl",
            "2026-05-02_08-00-00",
            _CARD_DOI,
        )

        summary = self._import(full_repair=True)

        self.assertEqual(summary["missing_analysis"], 0)
        self.assertEqual(summary["delivered_ledger_rows"], 1)
        self.assertEqual(self.store.supplement_backlog_summary()["pending"], 0)
        record = self.store.get_paper_record("prl", "https://doi.org/10.1103/ab12-cd34")
        self.assertEqual(record["analysis_status"], "not_required")

    def test_history_entry_without_card_becomes_missing_data(self):
        (self.history_dir / "arxiv_history.json").write_text(
            json.dumps({"2602.03848v1": "2026-02-04T23:48:23.286228"}), encoding="utf-8"
        )
        summary = self._import(full_repair=True)
        self.assertEqual(summary["missing_cards"], 1)
        rows = self.store.claim_supplement_backlog(10)
        self.assertEqual(rows[0]["paper_id"], "2602.03848v1")
        self.assertEqual(rows[0]["reason"], "missing_data")

    def test_lightweight_import_indexes_non_arxiv_html_without_reading_json(self):
        (self.history_dir / "arxiv_history.json").write_text(
            json.dumps({"2602.03848v1": "2026-02-04T23:48:23.286228"}),
            encoding="utf-8",
        )
        _write_report(
            self.reports_dir, "prl", "2026-05-02_08-00-00", _CARD_DOI
        )
        summary = self._import()
        self.assertFalse(summary["full_repair_enabled"])
        self.assertEqual(summary["cards_found"], 1)
        self.assertEqual(summary["cards_selected"], 1)
        self.assertEqual(summary["history_files"], {})
        self.assertEqual(summary["delivered_ledger_rows"], 1)
        self.assertEqual(summary["source_breakdown"], {"prl": 1})
        record = self.store.get_paper_record("prl", "https://doi.org/10.1103/ab12-cd34")
        self.assertIsNotNone(record)
        self.assertEqual(json.loads(record["paper_json"])["doi"], "10.1103/ab12-cd34")
        self.assertEqual(self.store.supplement_backlog_summary()["pending"], 0)

    def test_lightweight_import_never_reads_old_keyword_cache(self):
        _write_report(
            self.reports_dir,
            "arxiv",
            "2026-05-03_08-00-00",
            _card_without_keyword_section(1, "2603.30001v1", "No HTML Terms"),
        )
        legacy_cache = self.root / "keywords.db"
        _write_v32_keyword_cache(
            legacy_cache,
            [("cache-only", "2603.30001v1", "arxiv")],
        )

        with patch("utils.legacy_history._open_legacy_keywords_readonly") as opener:
            summary = import_legacy_history(
                self.store,
                history_dir=self.history_dir,
                reports_html_dir=self.reports_dir / "html",
                delivery_run_id=self.run_id,
                legacy_keywords_db_path=legacy_cache,
                full_repair=False,
            )

        opener.assert_not_called()
        self.assertEqual(summary["report_keywords"]["state"], "html_only")
        record = self.store.get_paper_record("arxiv", "2603.30001v1")
        self.assertEqual(json.loads(record["score_json"])["extracted_keywords"], [])

    def test_full_repair_uses_old_keyword_cache_only_for_matching_html_gap(self):
        _write_report(
            self.reports_dir,
            "arxiv",
            "2026-05-04_08-00-00",
            _card_fail(1, "2603.30002v1", "HTML Terms Win")
            + _card_without_keyword_section(2, "2603.30003v1", "Cache Fallback"),
        )
        legacy_cache = self.root / "keywords.db"
        _write_v32_keyword_cache(
            legacy_cache,
            [
                ("must-not-overwrite-html", "2603.30002v1", "arxiv"),
                ("cache-only", "2603.30003v1", "arxiv"),
                ("cache-extra", "2603.30003v1", "arxiv"),
                ("unreported-term", "2603.99999v1", "arxiv"),
            ],
        )

        summary = import_legacy_history(
            self.store,
            history_dir=self.history_dir,
            reports_html_dir=self.reports_dir / "html",
            delivery_run_id=self.run_id,
            legacy_keywords_db_path=legacy_cache,
            full_repair=True,
        )

        html_record = self.store.get_paper_record("arxiv", "2603.30002v1")
        fallback_record = self.store.get_paper_record("arxiv", "2603.30003v1")
        self.assertEqual(
            json.loads(html_record["score_json"])["extracted_keywords"],
            ["entanglement", "state preparation"],
        )
        self.assertEqual(
            json.loads(fallback_record["score_json"])["extracted_keywords"],
            ["cache-only", "cache-extra"],
        )
        self.assertEqual(summary["report_keywords"]["state"], "supplemented")
        self.assertEqual(summary["report_keywords"]["html_papers"], 1)
        self.assertEqual(summary["report_keywords"]["fallback_papers"], 1)
        self.assertEqual(summary["report_keywords"]["fallback_terms"], 2)

        # Old normalized terms, aliases, and unrelated raw entries never
        # enter the new SQLite database through the history importer.
        with self.store._connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertNotIn("legacy_keyword_records", tables)
        self.assertNotIn("normalized_keywords", tables)

        # The current SQLite keyword tracker sees exactly the report-scoped
        # terms and can normalize them through its normal workflow.
        from keyword_tracker.database import KeywordDatabase

        tracked_terms = KeywordDatabase(self.store.db_path).get_unique_unnormalized_keywords()
        self.assertEqual(
            set(tracked_terms),
            {"entanglement", "state preparation", "cache-only", "cache-extra"},
        )

    def test_v4_rows_are_never_downgraded_by_legacy_data(self):
        run_id = self.store.start_run(0)
        from sources.base_source import PaperMetadata

        paper = PaperMetadata(
            paper_id="2603.00845v1",
            title="V4 Title",
            authors=["A"],
            abstract="abs",
            published_date=datetime(2026, 8, 1),
            url="https://arxiv.org/abs/2603.00845v1",
            source="arxiv",
        )
        self.store.upsert_paper_seen(run_id, "arxiv", paper, stage_fingerprints={
            "score": "f1", "translation": "f2", "analysis": "f3",
        })
        (self.history_dir / "arxiv_history.json").write_text(
            json.dumps({"2603.00845v1": "2026-03-03T23:48:23.286228"}), encoding="utf-8"
        )
        _write_report(
            self.reports_dir, "arxiv", "2026-03-03_16-10-08",
            _card_fail(1, "2603.00845v1", "Old Title"),
        )
        summary = self._import()
        self.assertEqual(summary["skipped_v4_rows"], 1)
        record = self.store.get_paper_record("arxiv", "2603.00845v1")
        self.assertIn("V4 Title", record["paper_json"])
        # 账本仍按身份补写，防止同一版本再次推送。
        self.assertTrue(self.store.is_paper_delivered("arxiv", "2603.00845v1"))

    def test_run_kind_column_and_import_run_flow(self):
        (self.history_dir / "arxiv_history.json").write_text("{}", encoding="utf-8")
        self._import()
        self.store.complete_run(self.run_id, {})
        import sqlite3

        conn = sqlite3.connect(self.store.db_path)
        kind = conn.execute(
            "SELECT run_kind FROM daily_runs WHERE run_id = ?", (self.run_id,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(kind, "legacy_import")


class SupplementBacklogStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DailyResearchStore(Path(self.tmp.name) / "db.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_backlog_upsert_is_idempotent_and_keeps_delivered(self):
        entry = {
            "source": "arxiv",
            "canonical_id": "2603.1",
            "version": 1,
            "paper_id": "2603.1v1",
            "reason": "missing_data",
            "detail": "d1",
        }
        self.assertEqual(self.store.record_supplement_backlog([entry]), 1)
        self.assertEqual(self.store.record_supplement_backlog([dict(entry, detail="d2")]), 0)
        rows = self.store.claim_supplement_backlog(5)
        self.assertEqual(len(rows), 1)

        self.store.resolve_supplement_backlog(
            "run_x", [("arxiv", "2603.1", 1)], status="delivered"
        )
        # 交付后的行不再被重新排队。
        self.assertEqual(self.store.claim_supplement_backlog(5), [])
        self.assertEqual(self.store.record_supplement_backlog([dict(entry)]), 0)

    def test_claim_orders_data_repair_before_missed_scan(self):
        self.store.record_supplement_backlog([
            {"source": "arxiv", "canonical_id": "2603.9", "version": 1,
             "paper_id": "2603.9v1", "reason": "missed_scan"},
            {"source": "arxiv", "canonical_id": "2603.2", "version": 1,
             "paper_id": "2603.2v1", "reason": "missing_analysis"},
        ])
        rows = self.store.claim_supplement_backlog(1)
        self.assertEqual(rows[0]["canonical_id"], "2603.2")

    def test_failed_repair_does_not_starve_pending_missed_scan_rows(self):
        self.store.record_supplement_backlog([
            {"source": "arxiv", "canonical_id": "2603.1", "version": 1,
             "paper_id": "2603.1v1", "reason": "missing_data"},
            {"source": "arxiv", "canonical_id": "2603.2", "version": 1,
             "paper_id": "2603.2v1", "reason": "missed_scan"},
        ])
        self.store.resolve_supplement_backlog(
            "run_x", [("arxiv", "2603.1", 1)], status="failed"
        )

        rows = self.store.claim_supplement_backlog(1)

        self.assertEqual(rows[0]["canonical_id"], "2603.2")

    def test_app_state_round_trip(self):
        self.assertIsNone(self.store.get_app_state("k"))
        self.store.set_app_state("k", "v1")
        self.store.set_app_state("k", "v2")
        self.assertEqual(self.store.get_app_state("k"), "v2")


if __name__ == "__main__":
    unittest.main()
