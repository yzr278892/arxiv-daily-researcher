import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import AnalysisAgent, WeightedScoreResponse  # noqa: E402
from report.daily.modules.base_module import FormatHelper  # noqa: E402
from report.daily.modules.renderers import DeepAnalysisRenderer  # noqa: E402
from report.daily.reporter import Reporter  # noqa: E402
from sources.base_source import PaperMetadata  # noqa: E402
from utils.deep_analysis_contract import (  # noqa: E402
    ANALYSIS_META_KEY,
    CONTENT_SOURCE_ABSTRACT_FALLBACK,
    CONTENT_SOURCE_KEY,
    CONTENT_SOURCE_PDF,
)


def _template():
    return {
        "layout": {"section_title": "深度分析"},
        "modules": [
            {
                "id": "summary",
                "name": "内容摘要",
                "label": "摘要",
                "enabled": True,
                "order": 1,
                "format": "quote",
                "prompt": "概括论文内容",
            },
            {
                "id": "full_text_tldr",
                "name": "全文 TL;DR",
                "label": "全文 TL;DR（基于 PDF）",
                "enabled": True,
                "order": 2,
                "format": "quote",
                "prompt": "仅根据全文总结论文",
            },
        ],
        "prompts": {"analysis_template": "{field_prompts}"},
    }


def _paper_data():
    paper = PaperMetadata(
        paper_id="2501.12345v1",
        title="A paper",
        authors=["Author"],
        abstract="Abstract",
        published_date=datetime.now(timezone.utc),
        url="https://arxiv.org/abs/2501.12345v1",
        source="arxiv",
    )
    score = WeightedScoreResponse(
        total_score=9,
        keyword_scores={"keyword": 9},
        author_bonus=0,
        expert_authors_found=[],
        passing_score=5,
        is_qualified=True,
        reasoning="relevant",
        tldr="score tldr",
        extracted_keywords=["keyword"],
    )
    return {
        "paper_metadata": paper,
        "paper_id": paper.paper_id,
        "title": paper.title,
        "authors": paper.get_authors_string(),
        "abstract": paper.abstract,
        "abstract_cn": "中文摘要",
        "url": paper.url,
        "published": paper.published_date.strftime("%Y-%m-%d"),
        "score_response": score,
    }


class FullTextTldrProvenanceTests(unittest.TestCase):
    def _agent(self, parsed_text, response, prompts, system_prompts):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.deep_template = _template()
        agent._download_and_parse_pdf = lambda _url: parsed_text

        def _call(prompt, **kwargs):
            prompts.append(prompt)
            system_prompts.append(kwargs.get("system_prompt", ""))
            return response

        agent._call_smart_llm = _call
        return agent

    def test_analysis_marks_pdf_and_abstract_fallback_without_asking_for_fake_full_text(self):
        pdf_prompts = []
        pdf_system_prompts = []
        pdf_agent = self._agent(
            "parsed PDF text",
            '{"summary": "PDF summary", "full_text_tldr": "PDF-only TLDR"}',
            pdf_prompts,
            pdf_system_prompts,
        )
        pdf_result = pdf_agent.deep_analyze("A paper", "https://example.test/paper.pdf", "Abstract")
        self.assertEqual(
            pdf_result[ANALYSIS_META_KEY][CONTENT_SOURCE_KEY], CONTENT_SOURCE_PDF
        )
        self.assertIn("full_text_tldr", pdf_system_prompts[0])
        self.assertIn("parsed PDF text", pdf_prompts[0])

        fallback_prompts = []
        fallback_system_prompts = []
        fallback_agent = self._agent(
            None,
            # A nonconforming provider can still send the field; the renderer
            # must not trust it after a local abstract fallback.
            '{"summary": "Abstract summary", "full_text_tldr": "must stay hidden"}',
            fallback_prompts,
            fallback_system_prompts,
        )
        fallback_result = fallback_agent.deep_analyze(
            "A paper", "https://example.test/paper.pdf", "Abstract"
        )
        self.assertEqual(
            fallback_result[ANALYSIS_META_KEY][CONTENT_SOURCE_KEY],
            CONTENT_SOURCE_ABSTRACT_FALLBACK,
        )
        self.assertNotIn("full_text_tldr", fallback_system_prompts[0])

    def test_markdown_renderer_only_shows_full_text_tldr_for_pdf_provenance(self):
        renderer = DeepAnalysisRenderer(FormatHelper("mkdocs"), _template())
        fallback = {
            "summary": "Abstract summary",
            "full_text_tldr": "must stay hidden",
            ANALYSIS_META_KEY: {CONTENT_SOURCE_KEY: CONTENT_SOURCE_ABSTRACT_FALLBACK},
        }
        fallback_rendered = "\n".join(renderer.render({"analysis": fallback}, {}))
        self.assertIn("深度分析（摘要降级）", fallback_rendered)
        self.assertIn("Abstract summary", fallback_rendered)
        self.assertNotIn("must stay hidden", fallback_rendered)

        pdf = {
            "summary": "PDF summary",
            "full_text_tldr": "PDF-only TLDR",
            ANALYSIS_META_KEY: {CONTENT_SOURCE_KEY: CONTENT_SOURCE_PDF},
        }
        pdf_rendered = "\n".join(renderer.render({"analysis": pdf}, {}))
        self.assertIn("深度分析（PDF 全文）", pdf_rendered)
        self.assertIn("全文 TL;DR（基于 PDF）", pdf_rendered)
        self.assertIn("PDF-only TLDR", pdf_rendered)

    def test_html_renderer_hides_metadata_and_unverified_full_text_tldr(self):
        reporter = Reporter()
        reporter.deep_template = _template()
        paper = _paper_data()
        fallback = {
            "summary": "Abstract summary",
            "full_text_tldr": "must stay hidden",
            ANALYSIS_META_KEY: {CONTENT_SOURCE_KEY: CONTENT_SOURCE_ABSTRACT_FALLBACK},
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "report.html"
            reporter._generate_html_report(
                html_path,
                "arxiv",
                "ArXiv",
                [paper],
                {"keyword": 1.0},
                [{"paper_id": paper["paper_id"], "analysis": fallback}],
                has_deep_analysis=True,
            )
            fallback_html = html_path.read_text(encoding="utf-8")

            pdf = {
                "summary": "PDF summary",
                "full_text_tldr": "PDF-only TLDR",
                ANALYSIS_META_KEY: {CONTENT_SOURCE_KEY: CONTENT_SOURCE_PDF},
            }
            reporter._generate_html_report(
                html_path,
                "arxiv",
                "ArXiv",
                [paper],
                {"keyword": 1.0},
                [{"paper_id": paper["paper_id"], "analysis": pdf}],
                has_deep_analysis=True,
            )
            pdf_html = html_path.read_text(encoding="utf-8")

        self.assertIn("深度分析（摘要降级）", fallback_html)
        self.assertIn("Abstract summary", fallback_html)
        self.assertNotIn("must stay hidden", fallback_html)
        self.assertNotIn("__meta", fallback_html)
        self.assertIn("深度分析（PDF 全文）", pdf_html)
        self.assertIn("全文 TL;DR（基于 PDF）", pdf_html)
        self.assertIn("PDF-only TLDR", pdf_html)


if __name__ == "__main__":
    unittest.main()
