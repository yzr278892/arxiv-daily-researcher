"""学习模式评分：信号持久化、聚合、偏好联动与评分修正。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import AnalysisAgent  # noqa: E402
from config import settings  # noqa: E402
from scoring_policy import (  # noqa: E402
    LEARNED_PREFERENCE_V1,
    LEGACY_WEIGHTED_KEYWORD_V1,
    compute_learned_adjustment,
)
from utils.daily_research_store import (  # noqa: E402
    DailyResearchStore,
    V1_PASS_SIGNAL_STRENGTH,
)


def _seed_scored_paper(
    store: DailyResearchStore,
    paper_id: str,
    *,
    authors: list[str],
    extracted: list[str],
    completed_at: str = "2026-08-20T08:00:00",
) -> None:
    paper_json = json.dumps(
        {
            "paper_id": paper_id,
            "source": "arxiv",
            "title": f"Paper {paper_id}",
            "authors": authors,
        },
        ensure_ascii=False,
    )
    score_json = json.dumps(
        {
            "total_score": 12.0,
            "is_qualified": True,
            "strategy_id": "legacy_weighted_keyword_v1",
            "tldr": "tldr",
            "extracted_keywords": extracted,
        },
        ensure_ascii=False,
    )
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_papers(
                source, paper_id, canonical_id, first_seen_at, last_seen_at,
                paper_json, score_json, completed_at
            ) VALUES (?, ?, '', ?, ?, ?, ?, ?)
            """,
            ("arxiv", paper_id, completed_at, completed_at, paper_json, score_json, completed_at),
        )


class LearningSignalStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.store = DailyResearchStore(Path(self._tmp.name) / "daily_research.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_and_aggregate(self):
        self.store.record_learning_signals(
            "arxiv", "p1", ["diffusion", "speech"], "keyword", "preference", 1.0
        )
        self.store.record_learning_signals(
            "arxiv", "p2", ["diffusion"], "keyword", "v1_pass", V1_PASS_SIGNAL_STRENGTH
        )
        self.store.record_learning_signals(
            "arxiv", "p3", ["diffusion"], "keyword", "preference", -1.0
        )
        self.store.record_learning_signals(
            "arxiv", "p1", ["Alice Smith"], "author", "preference", 1.0
        )

        terms = {row["term"]: row for row in self.store.get_learned_preference_terms()}
        self.assertAlmostEqual(terms["diffusion"]["weight"], 0.25)
        self.assertAlmostEqual(terms["speech"]["weight"], 1.0)
        self.assertAlmostEqual(terms["Alice Smith"]["weight"], 1.0)

        keywords_only = self.store.get_learned_preference_terms(term_type="keyword")
        self.assertNotIn("Alice Smith", {row["term"] for row in keywords_only})

    def test_upsert_is_idempotent_per_paper(self):
        for _ in range(3):
            self.store.record_learning_signals(
                "arxiv", "p1", ["diffusion"], "keyword", "preference", 1.0
            )
        terms = self.store.get_learned_preference_terms()
        self.assertEqual(len(terms), 1)
        self.assertAlmostEqual(terms[0]["weight"], 1.0)

    def test_cancelled_signals_are_hidden(self):
        self.store.record_learning_signals(
            "arxiv", "p1", ["diffusion"], "keyword", "preference", 1.0
        )
        self.store.record_learning_signals(
            "arxiv", "p2", ["diffusion"], "keyword", "preference", -1.0
        )
        self.assertEqual(self.store.get_learned_preference_terms(), [])

    def test_invalid_arguments_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record_learning_signals(
                "arxiv", "p1", ["x"], "topic", "preference", 1.0
            )
        with self.assertRaises(ValueError):
            self.store.record_learning_signals(
                "arxiv", "p1", ["x"], "keyword", "unknown", 1.0
            )

    def test_set_paper_preference_feeds_signals_from_stored_metadata(self):
        _seed_scored_paper(
            self.store, "a1", authors=["Alice Smith"], extracted=["diffusion", "speech"]
        )
        self.store.set_paper_preference(
            "arxiv", "a1", preference="like", title="Paper a1"
        )
        terms = {row["term"]: row["weight"] for row in self.store.get_learned_preference_terms()}
        self.assertAlmostEqual(terms["diffusion"], 1.0)
        self.assertAlmostEqual(terms["speech"], 1.0)
        self.assertAlmostEqual(terms["Alice Smith"], 1.0)

        # 改为不喜欢后，同一论文的偏好信号被改写而不是叠加
        self.store.set_paper_preference(
            "arxiv", "a1", preference="dislike", title="Paper a1"
        )
        terms = {row["term"]: row["weight"] for row in self.store.get_learned_preference_terms()}
        self.assertAlmostEqual(terms["diffusion"], -1.0)

        # 清除偏好把信号中和为 0，聚合中消失
        self.store.set_paper_preference(
            "arxiv", "a1", preference="none", title="Paper a1"
        )
        self.assertEqual(self.store.get_learned_preference_terms(), [])


class ComputeLearnedAdjustmentTests(unittest.TestCase):
    def test_dampening_and_cap(self):
        result = compute_learned_adjustment(
            extracted_keywords=["diffusion"],
            author_names=[],
            learned_terms={"keyword": {"diffusion": 9.0}, "author": {}},
            configured_keywords=[],
            dampening=0.5,
            term_weight_cap=2.0,
        )
        # 9.0 截断到 2.0，再乘 0.5
        self.assertAlmostEqual(result["adjustment"], 1.0)
        self.assertEqual(result["keywords"], ["diffusion"])

    def test_negative_weights_subtract(self):
        result = compute_learned_adjustment(
            extracted_keywords=["quantization"],
            author_names=[],
            learned_terms={"keyword": {"quantization": -3.0}, "author": {}},
            configured_keywords=[],
            dampening=1.0,
            term_weight_cap=2.0,
        )
        self.assertAlmostEqual(result["adjustment"], -2.0)

    def test_configured_keywords_not_double_counted(self):
        result = compute_learned_adjustment(
            extracted_keywords=["diffusion"],
            author_names=[],
            learned_terms={"keyword": {"Diffusion": 5.0}, "author": {}},
            configured_keywords=["diffusion"],
            dampening=0.5,
            term_weight_cap=2.0,
        )
        self.assertAlmostEqual(result["adjustment"], 0.0)
        self.assertEqual(result["keywords"], [])

    def test_author_matching_is_normalized(self):
        result = compute_learned_adjustment(
            extracted_keywords=[],
            author_names=["Alice  Smith"],
            learned_terms={"keyword": {}, "author": {"alice smith": 1.0}},
            configured_keywords=[],
            dampening=0.5,
            term_weight_cap=2.0,
        )
        self.assertAlmostEqual(result["adjustment"], 0.5)
        self.assertEqual(result["authors"], ["alice smith"])


def _score_payload():
    return json.dumps(
        {
            "keyword_scores": {"quantum sensing": 8, "noise": 2.5},
            "reasoning": "The paper directly studies quantum sensing under noise.",
            "tldr": "It improves a quantum sensing protocol under realistic noise.",
            "extracted_keywords": ["quantum sensing", "noise", "metrology"],
        }
    )


class LearnedScoringAgentTests(unittest.TestCase):
    def _agent(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent._call_cheap_llm = lambda _prompt, **_kwargs: _score_payload()
        return agent

    def test_learned_adjustment_adds_to_legacy_total(self):
        agent = self._agent()
        learned = {
            "keyword": {"metrology": 1.0},
            "author": {"Alice Smith": 2.0},
        }
        with patch.object(settings, "SCORE_STRATEGY", LEARNED_PREFERENCE_V1), patch.object(
            settings, "LEARNED_WEIGHT_DAMPENING", 0.5
        ), patch.object(settings, "LEARNED_TERM_WEIGHT_CAP", 2.0), patch.object(
            settings, "ENABLE_AUTHOR_BONUS", False
        ):
            learned_response = agent.score_paper_with_keywords(
                title="Quantum sensing",
                authors=["Alice Smith"],
                abstract="abstract",
                keywords_dict={"quantum sensing": 1.0, "noise": 0.5},
                learned_terms=learned,
            )
            legacy_response = agent.score_paper_with_keywords(
                title="Quantum sensing",
                authors=["Alice Smith"],
                abstract="abstract",
                keywords_dict={"quantum sensing": 1.0, "noise": 0.5},
            )

        base = (
            8 * 1.0 + 2.5 * 0.5
        )  # keyword_scores × weights，无作者加分
        self.assertAlmostEqual(legacy_response.total_score, base)
        # metrology 1.0×0.5 + Alice Smith 2.0×0.5 = 1.5
        self.assertAlmostEqual(learned_response.total_score, base + 1.5)
        self.assertAlmostEqual(learned_response.learned_adjustment, 1.5)
        self.assertIn("metrology", learned_response.learned_keywords_matched)
        self.assertIn("Alice Smith", learned_response.learned_authors_matched)
        self.assertEqual(learned_response.strategy_id, LEARNED_PREFERENCE_V1)
        self.assertIn("学习", learned_response.qualification_reason)

    def test_legacy_response_keeps_learned_fields_defaulted(self):
        agent = self._agent()
        with patch.object(
            settings, "SCORE_STRATEGY", LEGACY_WEIGHTED_KEYWORD_V1
        ), patch.object(settings, "ENABLE_AUTHOR_BONUS", False):
            response = agent.score_paper_with_keywords(
                title="Quantum sensing",
                authors=["Alice Smith"],
                abstract="abstract",
                keywords_dict={"quantum sensing": 1.0, "noise": 0.5},
            )
        self.assertIsNone(response.learned_adjustment)
        self.assertEqual(response.learned_keywords_matched, [])

    def test_learned_settings_validated(self):
        agent = self._agent()
        agent._call_cheap_llm = lambda _prompt, **_kwargs: self.fail("LLM must not be called")
        from agents.analysis_agent import ScoreValidationError

        with patch.object(settings, "SCORE_STRATEGY", LEARNED_PREFERENCE_V1), patch.object(
            settings, "LEARNED_WEIGHT_DAMPENING", 1.5
        ):
            with self.assertRaises(ScoreValidationError):
                agent.score_paper_with_keywords(
                    title="t",
                    authors=[],
                    abstract="a",
                    keywords_dict={"k": 1.0},
                )


if __name__ == "__main__":
    unittest.main()
