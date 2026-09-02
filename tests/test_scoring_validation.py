import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import AnalysisAgent, ScoreValidationError  # noqa: E402
from config import settings  # noqa: E402
from scoring_policy import CORE_RELEVANCE_V2  # noqa: E402
from report.daily.modules.base_module import FormatHelper  # noqa: E402
from report.daily.modules.renderers import ScoringRenderer  # noqa: E402


def _score_payload(**overrides):
    payload = {
        "keyword_scores": {"quantum sensing": 8, "noise": 2.5},
        "reasoning": "The paper directly studies quantum sensing under noise.",
        "tldr": "It improves a quantum sensing protocol under realistic noise.",
        "extracted_keywords": ["quantum sensing", "noise", "metrology"],
    }
    payload.update(overrides)
    return json.dumps(payload)


class ScoringValidationTests(unittest.TestCase):
    def _agent_with_response(self, payload):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent._call_cheap_llm = lambda _prompt, **_kwargs: payload
        return agent

    def test_rejects_missing_extra_and_out_of_range_keyword_scores(self):
        keywords = {"quantum sensing": 1.0, "noise": 0.5}

        cases = [
            _score_payload(keyword_scores={"quantum sensing": 8}),
            _score_payload(keyword_scores={"quantum sensing": 8, "noise": 2, "fake": 10}),
            _score_payload(keyword_scores={"quantum sensing": 11, "noise": 2}),
            _score_payload(keyword_scores={"quantum sensing": float("nan"), "noise": 2}),
        ]
        for payload in cases:
            with self.subTest(payload=payload), patch.object(settings, "MAX_SCORE_PER_KEYWORD", 10):
                with self.assertRaisesRegex(RuntimeError, "论文评分失败"):
                    self._agent_with_response(payload).score_paper_with_keywords(
                        "title", ["Alice"], "abstract", keywords
                    )

    def test_rejects_missing_tldr_instead_of_persisting_a_placeholder(self):
        payload = _score_payload(tldr="")
        with self.assertRaisesRegex(RuntimeError, "tldr 必须是非空字符串"):
            self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Alice"], "abstract", {"quantum sensing": 1, "noise": 1}
            )

    def test_expert_bonus_uses_only_real_configured_authors_once(self):
        payload = _score_payload(
            expert_authors_found=["Imaginary Person", "Alice Smith", "Alice Smith"]
        )
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 10), patch.object(
            settings, "ENABLE_AUTHOR_BONUS", True
        ), patch.object(settings, "EXPERT_AUTHORS", ["alice-smith", "Bob Jones"]), patch.object(
            settings, "AUTHOR_BONUS_POINTS", 3.0
        ), patch.object(
            settings, "SCORE_STRATEGY", "legacy_weighted_keyword_v1"
        ):
            result = self._agent_with_response(payload).score_paper_with_keywords(
                "title",
                ["Alice Smith", "Alice Smith", "Unrelated Author"],
                "abstract",
                {"quantum sensing": 1.0, "noise": 0.5},
            )

        self.assertEqual(result.expert_authors_found, ["Alice Smith"])
        self.assertEqual(result.author_bonus, 3.0)
        self.assertEqual(result.total_score, 12.25)

    def test_expert_bonus_uses_each_authors_configured_points(self):
        payload = _score_payload(expert_authors_found=["Alice Smith", "Bob Jones"])
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 10), patch.object(
            settings, "ENABLE_AUTHOR_BONUS", True
        ), patch.object(
            settings, "EXPERT_AUTHORS", ["Alice Smith", "Bob Jones"]
        ), patch.object(
            settings, "AUTHOR_BONUS_POINTS", 3.0
        ), patch.object(
            settings, "AUTHOR_BONUS_BY_AUTHOR", {"Alice Smith": 1.5, "Bob Jones": 4.0}
        ), patch.object(
            settings, "SCORE_STRATEGY", "legacy_weighted_keyword_v1"
        ):
            result = self._agent_with_response(payload).score_paper_with_keywords(
                "title",
                ["Alice Smith", "Bob Jones"],
                "abstract",
                {"quantum sensing": 1.0, "noise": 0.5},
            )

        self.assertEqual(result.expert_authors_found, ["Alice Smith", "Bob Jones"])
        self.assertEqual(result.author_bonus, 5.5)
        self.assertEqual(result.total_score, 14.75)

    def test_invalid_score_configuration_fails_before_an_llm_call(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent._call_cheap_llm = lambda _prompt, **_kwargs: self.fail("LLM must not be called")
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 0):
            with self.assertRaises(ScoreValidationError):
                agent.score_paper_with_keywords("title", ["Alice"], "abstract", {"kw": 1})

    def test_v2_reference_keywords_cannot_lower_core_qualification(self):
        payload = _score_payload(
            keyword_scores={"core": 5.9, "reference": 10},
        )
        with patch.object(settings, "SCORE_STRATEGY", CORE_RELEVANCE_V2), patch.object(
            settings, "PRIMARY_KEYWORDS", ["core"]
        ), patch.object(settings, "CORE_RELEVANCE_THRESHOLD", 6.0), patch.object(
            settings, "CORE_KEYWORD_MIN_SCORE", 5.0
        ), patch.object(settings, "REFERENCE_RANKING_WEIGHT", 1.0):
            result = self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Alice"], "abstract", {"core": 1.0, "reference": 0.01}
            )

        self.assertFalse(result.is_qualified)
        self.assertEqual(result.relevance_score, 5.9)
        self.assertEqual(result.qualification_threshold, 6.0)
        self.assertEqual(result.core_keywords_used, ["core"])

    def test_v2_expert_author_cannot_qualify_zero_core_relevance(self):
        payload = _score_payload(keyword_scores={"core": 0, "reference": 10})
        with patch.object(settings, "SCORE_STRATEGY", CORE_RELEVANCE_V2), patch.object(
            settings, "PRIMARY_KEYWORDS", ["core"]
        ), patch.object(settings, "CORE_RELEVANCE_THRESHOLD", 6.0), patch.object(
            settings, "CORE_KEYWORD_MIN_SCORE", 7.0), patch.object(
            settings, "REFERENCE_RANKING_WEIGHT", 1.0
        ), patch.object(settings, "ENABLE_AUTHOR_BONUS", True), patch.object(
            settings, "EXPERT_AUTHORS", ["Alice Smith"]
        ), patch.object(settings, "AUTHOR_BONUS_POINTS", 50.0):
            result = self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Alice Smith"], "abstract", {"core": 1.0, "reference": 1.0}
            )

        self.assertFalse(result.is_qualified)
        self.assertEqual(result.author_bonus, 0.0)
        self.assertEqual(result.author_preference_bonus, 0.0)
        self.assertEqual(result.ranking_score, 10.0)

    def test_v2_strong_primary_match_qualifies_and_author_only_changes_ranking(self):
        payload = _score_payload(keyword_scores={"core": 8, "reference": 2})
        common = dict(
            SCORE_STRATEGY=CORE_RELEVANCE_V2,
            PRIMARY_KEYWORDS=["core"],
            CORE_RELEVANCE_THRESHOLD=6.0,
            CORE_KEYWORD_MIN_SCORE=7.0,
            REFERENCE_RANKING_WEIGHT=0.5,
            ENABLE_AUTHOR_BONUS=True,
            EXPERT_AUTHORS=["Alice Smith"],
            AUTHOR_BONUS_POINTS=3.0,
        )
        with patch.multiple(settings, **common):
            expert = self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Alice Smith"], "abstract", {"core": 1.0, "reference": 1.0}
            )
            nonexpert = self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Other Author"], "abstract", {"core": 1.0, "reference": 1.0}
            )

        self.assertTrue(expert.is_qualified)
        self.assertTrue(nonexpert.is_qualified)
        self.assertEqual(expert.relevance_score, nonexpert.relevance_score)
        self.assertEqual(expert.ranking_score, nonexpert.ranking_score + 3.0)

    def test_v2_without_primary_keywords_has_explicit_safe_fallback(self):
        payload = _score_payload(keyword_scores={"reference": 8, "other": 1})
        with patch.object(settings, "SCORE_STRATEGY", CORE_RELEVANCE_V2), patch.object(
            settings, "PRIMARY_KEYWORDS", []
        ), patch.object(settings, "CORE_RELEVANCE_THRESHOLD", 4.0), patch.object(
            settings, "CORE_KEYWORD_MIN_SCORE", 7.0), patch.object(
            settings, "REFERENCE_RANKING_WEIGHT", 0.25):
            result = self._agent_with_response(payload).score_paper_with_keywords(
                "title", ["Alice"], "abstract", {"reference": 1.0, "other": 1.0}
            )

        self.assertTrue(result.is_qualified)
        self.assertEqual(result.core_keywords_used, ["reference", "other"])
        self.assertIn("未配置主要关键词", result.qualification_reason)

    def test_scoring_renderer_uses_configured_maximum_not_hardcoded_ten(self):
        renderer = ScoringRenderer(FormatHelper("mkdocs"))
        response = type(
            "Score", (),
            {
                "total_score": 4.0,
                "passing_score": 3.0,
                "is_qualified": True,
                "keyword_scores": {"kw": 4.0},
                "author_bonus": 0.0,
                "expert_authors_found": [],
                "reasoning": "test",
            },
        )()
        with patch.object(settings, "MAX_SCORE_PER_KEYWORD", 5):
            lines = renderer.render(
                {"score_response": response, "keywords_dict": {"kw": 1.0}},
                {"format": "list", "show_details": True, "show_reasoning": False},
            )
        self.assertIn("4.0/5", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
