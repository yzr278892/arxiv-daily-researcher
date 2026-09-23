import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import WeightedScoreResponse  # noqa: E402
from config import settings  # noqa: E402
from modes.daily_research import _select_top_papers, _validate_report_paths  # noqa: E402
from report.daily.reporter import ReportGenerationError, Reporter  # noqa: E402
from scoring_policy import CORE_RELEVANCE_V2  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402


def _scored_paper(paper_id: str, score: float, qualified: bool):
    paper = PaperMetadata(
        paper_id=paper_id,
        title=f"Title {paper_id}",
        authors=["Author"],
        abstract="Abstract",
        published_date=datetime.now(timezone.utc),
        url=f"https://arxiv.org/abs/{paper_id}",
        source="arxiv",
    )
    score_response = WeightedScoreResponse(
        total_score=score,
        keyword_scores={"keyword": score},
        author_bonus=0,
        expert_authors_found=[],
        passing_score=5,
        is_qualified=qualified,
        reasoning="test",
        tldr="test tldr",
        extracted_keywords=["keyword"],
    )
    return {
        "paper_metadata": paper,
        "paper_id": paper_id,
        "title": paper.title,
        "authors": paper.get_authors_string(),
        "abstract": paper.abstract,
        "abstract_cn": "中文摘要",
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "published": paper.published_date.strftime("%Y-%m-%d"),
        "score_response": score_response,
    }


class ReportReliabilityTests(unittest.TestCase):
    def test_atomic_write_keeps_old_report_and_cleans_temporary_file_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.md"
            report_path.write_text("old report", encoding="utf-8")

            with patch("report.daily.reporter.os.replace", side_effect=OSError("disk error")):
                with self.assertRaisesRegex(OSError, "disk error"):
                    Reporter._write_text_atomic(report_path, "new report")

            self.assertEqual(report_path.read_text(encoding="utf-8"), "old report")
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])

    def test_report_generation_raises_when_an_enabled_format_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            paper = _scored_paper("2501.12345v1", 8, True)

            with patch.object(settings, "ENABLE_MARKDOWN_REPORT", True), patch.object(
                settings, "ENABLE_HTML_REPORT", False
            ), patch.object(reporter, "_write_text_atomic", side_effect=OSError("read-only")):
                with self.assertRaisesRegex(ReportGenerationError, "read-only"):
                    reporter.generate_reports_by_source({"arxiv": [paper]}, {"keyword": 1.0})

    def test_qualified_papers_are_not_rendered_twice_in_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            reporter.basic_template = {
                "layout": {
                    "show_config_section": False,
                    "show_stats_section": False,
                    "show_qualified_section": True,
                    "show_all_papers_section": True,
                },
                "modules": [],
            }
            qualified = _scored_paper("2501.12345v1", 9, True)
            unqualified = _scored_paper("2501.99999v1", 1, False)

            def render_stub(paper, *_args, **_kwargs):
                return [f"paper-id: {paper['paper_id']}"]

            with patch.object(settings, "ENABLE_MARKDOWN_REPORT", True), patch.object(
                settings, "ENABLE_HTML_REPORT", False
            ), patch.object(reporter, "_render_paper_section", side_effect=render_stub):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [qualified, unqualified]}, {"keyword": 1.0}
                )

            content = paths["arxiv"].read_text(encoding="utf-8")
            self.assertEqual(content.count("paper-id: 2501.12345v1"), 1)
            self.assertEqual(content.count("paper-id: 2501.99999v1"), 1)
            self.assertIn("其他论文列表", content)

    def test_html_report_renders_semantic_scholar_tldr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            paper = _scored_paper("2501.12345v1", 9, True)
            paper["paper_metadata"].semantic_scholar_tldr = "External <TLDR>"

            with patch.object(settings, "ENABLE_MARKDOWN_REPORT", False), patch.object(
                settings, "ENABLE_HTML_REPORT", True
            ):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [paper]}, {"keyword": 1.0}
                )

            content = paths["arxiv_html"].read_text(encoding="utf-8")
            self.assertIn("Semantic Scholar TL;DR:", content)
            self.assertIn("External &lt;TLDR&gt;", content)

    def test_html_report_shows_penalty_without_confusing_positive_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            paper = _scored_paper("2501.12345v1", -1, False)
            score = paper["score_response"]
            score.strategy_id = "weighted_keyword_with_penalties_v1"
            score.negative_keyword_scores = {"Noise": 8.0}
            score.negative_keyword_weights = {"Noise": 0.5}
            score.negative_keyword_penalty = 4.0

            with patch.object(settings, "ENABLE_MARKDOWN_REPORT", False), patch.object(
                settings, "ENABLE_HTML_REPORT", True
            ):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [paper]}, {"keyword": 1.0}
                )

            content = paths["arxiv_html"].read_text(encoding="utf-8")
            self.assertIn("不关注：Noise", content)
            self.assertIn("<td style=\"text-align:center;padding:4px 8px;\">-4.0</td>", content)

    def test_html_report_escapes_math_like_text_and_rejects_unsafe_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            paper = _scored_paper("2501.12345v1", 9, True)
            paper["url"] = "javascript:alert(1)"
            paper["paper_metadata"].url = paper["url"]
            paper["score_response"].tldr = "$<img src=x onerror=alert(1)>$"

            with patch.object(settings, "ENABLE_MARKDOWN_REPORT", False), patch.object(
                settings, "ENABLE_HTML_REPORT", True
            ):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [paper]}, {"keyword": 1.0}
                )

            content = paths["arxiv_html"].read_text(encoding="utf-8")
            self.assertNotIn('href="javascript:', content)
            self.assertIn("$&lt;img src=x onerror=alert(1)&gt;$", content)
            self.assertNotIn("<img src=x onerror=alert(1)>", content)

    def test_qualified_only_reports_hide_failed_papers_but_keep_full_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            qualified = _scored_paper("2501.12345v1", 9, True)
            unqualified = _scored_paper("2501.99999v1", 1, False)

            with patch.object(settings, "INCLUDE_ALL_IN_REPORT", False), patch.object(
                settings, "ENABLE_MARKDOWN_REPORT", True
            ), patch.object(settings, "ENABLE_HTML_REPORT", True):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [qualified, unqualified]}, {"keyword": 1.0}
                )

            markdown = paths["arxiv"].read_text(encoding="utf-8")
            html = paths["arxiv_html"].read_text(encoding="utf-8")
            self.assertIn("Title 2501.12345v1", markdown)
            self.assertNotIn("Title 2501.99999v1", markdown)
            self.assertIn("仅及格论文（1/2 篇）", markdown)
            self.assertIn("总抓取**: 2 篇", markdown)
            self.assertIn("Title 2501.12345v1", html)
            self.assertNotIn("Title 2501.99999v1", html)
            self.assertIn("Showing qualified papers only: 1/2", html)

    def test_qualified_only_reports_are_emitted_when_nothing_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            unqualified = _scored_paper("2501.99999v1", 1, False)

            with patch.object(settings, "INCLUDE_ALL_IN_REPORT", False), patch.object(
                settings, "ENABLE_MARKDOWN_REPORT", True
            ), patch.object(settings, "ENABLE_HTML_REPORT", False):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [unqualified]}, {"keyword": 1.0}
                )

            content = paths["arxiv"].read_text(encoding="utf-8")
            self.assertIn("本次没有达到通过分数的论文", content)
            self.assertNotIn("Title 2501.99999v1", content)

    def test_report_path_validation_rejects_missing_or_empty_enabled_outputs(self):
        paper = _scored_paper("2501.12345v1", 9, True)
        papers = {"arxiv": [paper]}
        with patch.object(settings, "ENABLE_MARKDOWN_REPORT", True), patch.object(
            settings, "ENABLE_HTML_REPORT", False
        ):
            with self.assertRaisesRegex(RuntimeError, "缺少输出"):
                _validate_report_paths({}, papers)

            with tempfile.TemporaryDirectory() as temp_dir:
                empty_path = Path(temp_dir) / "empty.md"
                empty_path.touch()
                with self.assertRaisesRegex(RuntimeError, "空文件或不可访问"):
                    _validate_report_paths({"arxiv": empty_path}, papers)

    def test_report_path_validation_rejects_a_daily_run_with_no_output_format(self):
        paper = _scored_paper("2501.12345v1", 9, True)
        with patch.object(settings, "ENABLE_MARKDOWN_REPORT", False), patch.object(
            settings, "ENABLE_HTML_REPORT", False
        ):
            with self.assertRaisesRegex(RuntimeError, "无有效输出格式"):
                _validate_report_paths({}, {"arxiv": [paper]})

    def test_notification_top_papers_only_contains_qualified_results(self):
        selected = _select_top_papers(
            {
                "arxiv": [
                    _scored_paper("high-but-fail", 99, False),
                    _scored_paper("qualified-low", 6, True),
                    _scored_paper("qualified-high", 10, True),
                ]
            },
            5,
        )
        self.assertEqual([item["title"] for item in selected], ["Title qualified-high", "Title qualified-low"])

    def test_v2_reports_and_notifications_sort_by_ranking_not_qualification_score(self):
        high_relevance = _scored_paper("core-high", 8, True)
        author_preferred = _scored_paper("author-preferred", 7, True)
        for paper, relevance, ranking in (
            (high_relevance, 8.0, 8.0),
            (author_preferred, 7.0, 10.0),
        ):
            score = paper["score_response"]
            score.strategy_id = CORE_RELEVANCE_V2
            score.relevance_score = relevance
            score.qualification_threshold = 6.0
            score.ranking_score = ranking
            score.total_score = ranking
            score.author_preference_bonus = ranking - relevance

        selected = _select_top_papers(
            {"arxiv": [high_relevance, author_preferred]}, limit=2
        )
        self.assertEqual(
            [item["title"] for item in selected],
            ["Title author-preferred", "Title core-high"],
        )
        self.assertEqual(selected[0]["relevance_score"], 7.0)
        self.assertEqual(selected[0]["score"], 10.0)

        with tempfile.TemporaryDirectory() as temp_dir:
            reporter = Reporter()
            reporter.report_base_dir = Path(temp_dir)
            with patch.object(settings, "SCORE_STRATEGY", CORE_RELEVANCE_V2), patch.object(
                settings, "ENABLE_MARKDOWN_REPORT", True
            ), patch.object(settings, "ENABLE_HTML_REPORT", False):
                paths = reporter.generate_reports_by_source(
                    {"arxiv": [high_relevance, author_preferred]}, {"keyword": 1.0}
                )
            content = paths["arxiv"].read_text(encoding="utf-8")

        self.assertLess(
            content.index("Title author-preferred"), content.index("Title core-high")
        )
        self.assertIn("核心相关度", content)

    def test_old_score_json_hydration_defaults_to_legacy_fields(self):
        old = WeightedScoreResponse.model_validate(
            {
                "total_score": 8,
                "keyword_scores": {"keyword": 8},
                "author_bonus": 0,
                "expert_authors_found": [],
                "passing_score": 5,
                "is_qualified": True,
                "reasoning": "legacy",
                "tldr": "legacy tldr",
                "extracted_keywords": ["keyword"],
            }
        )
        self.assertEqual(old.strategy_id, "legacy_weighted_keyword_v1")
        self.assertIsNone(old.relevance_score)
        self.assertEqual(old.ranking_score, None)


if __name__ == "__main__":
    unittest.main()
