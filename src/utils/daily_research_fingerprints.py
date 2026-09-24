"""Deterministic cache keys for resumable daily-paper processing.

Persisted LLM output is useful only while the exact input and the relevant
configuration are unchanged.  These helpers deliberately never include API
keys or other credentials; they describe reproducible processing inputs, not
secrets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

from config import settings
from scoring_policy import WEIGHTED_KEYWORD_WITH_PENALTIES_V1
# New score audit records derive the identifier from settings for each actual
# decision; historical audit JSON continues to carry its own fixed value.
SCORE_PROMPT_REVISION = "daily-keyword-score-v4"


def configured_score_strategy_id() -> str:
    """Return the validated strategy selected for the current run."""
    return settings.normalized_score_strategy()


def _canonical_json(value: Any) -> str:
    """Serialize JSON-compatible input in one stable representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stage_input_fingerprint(value: Any) -> str:
    """Return a versioned SHA-256 key for one processing-stage input."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _model_settings(model: Any, temperature: Any) -> Dict[str, Any]:
    """Keep only non-secret model settings that affect generated output."""
    return {
        "model_name": str(getattr(model, "model_name", "")),
        "temperature": temperature,
    }


def _primary_keyword_entries() -> list[list[Any]]:
    """Return the configured per-keyword weights in prompt order."""
    weights = getattr(settings, "PRIMARY_KEYWORD_WEIGHTS", {})
    if not isinstance(weights, Mapping):
        weights = {}
    fallback = getattr(settings, "PRIMARY_KEYWORD_WEIGHT", 1.0)
    return [
        [str(keyword), weights.get(keyword, fallback)]
        for keyword in getattr(settings, "PRIMARY_KEYWORDS", [])
    ]


def _author_bonus_entries() -> list[list[Any]]:
    """Return author-specific bonuses without exposing unrelated settings."""
    values = getattr(settings, "AUTHOR_BONUS_BY_AUTHOR", {})
    if not isinstance(values, Mapping):
        values = {}
    fallback = getattr(settings, "AUTHOR_BONUS_POINTS", 0.0)
    return [
        [str(author), values.get(author, fallback)]
        for author in getattr(settings, "EXPERT_AUTHORS", [])
    ]


def _negative_keyword_entries() -> list[list[Any]]:
    """Only the penalty strategy depends on these configured terms."""
    if configured_score_strategy_id() != WEIGHTED_KEYWORD_WITH_PENALTIES_V1:
        return []
    weights = getattr(settings, "NEGATIVE_KEYWORD_WEIGHTS", {})
    if not isinstance(weights, Mapping):
        weights = {}
    return [
        [str(keyword), weights.get(keyword, 1.0)]
        for keyword in getattr(settings, "NEGATIVE_KEYWORDS", [])
    ]


def build_score_audit_metadata(
    paper: Any,
    keywords: Mapping[str, Any],
    score_input_fingerprint: str | None,
) -> Dict[str, Any]:
    """Return non-secret evidence needed to audit one persisted score.

    A hash alone establishes that an input changed, but it does not let a
    later human review identify which model, keyword weights, or threshold
    produced a historical decision.  Keep a deliberately small, safe
    snapshot alongside the score.  In particular, this function must never
    include an API key, provider URL, or the potentially private free-text
    research context itself.
    """
    policy_input = {
        "schema": "daily-research-score-policy-v1",
        "strategy_id": configured_score_strategy_id(),
        "keywords": [[str(keyword), weight] for keyword, weight in keywords.items()],
        # Primary-keyword membership changes the V2 qualification rule even
        # when the merged keyword dictionary happens to stay identical.
        "primary_keywords": [str(keyword) for keyword in settings.PRIMARY_KEYWORDS],
        "primary_keyword_entries": _primary_keyword_entries(),
        "negative_keyword_entries": _negative_keyword_entries(),
        "score_settings": {
            "max_score_per_keyword": settings.MAX_SCORE_PER_KEYWORD,
            "passing_score_base": settings.PASSING_SCORE_BASE,
            "passing_score_weight_coefficient": settings.PASSING_SCORE_WEIGHT_COEFFICIENT,
            "core_relevance_threshold": settings.CORE_RELEVANCE_THRESHOLD,
            "core_keyword_min_score": settings.CORE_KEYWORD_MIN_SCORE,
            "reference_ranking_weight": settings.REFERENCE_RANKING_WEIGHT,
            "enable_author_bonus": settings.ENABLE_AUTHOR_BONUS,
            "author_bonus_points": settings.AUTHOR_BONUS_POINTS,
            "author_bonus_entries": _author_bonus_entries(),
            "expert_authors_fingerprint": stage_input_fingerprint(
                [str(author) for author in settings.EXPERT_AUTHORS]
            ),
            "research_context_fingerprint": stage_input_fingerprint(
                str(settings.RESEARCH_CONTEXT)
            ),
        },
        "model": _model_settings(settings.CHEAP_LLM, settings.CHEAP_LLM.temperature),
        "prompt_revision": SCORE_PROMPT_REVISION,
    }
    return {
        "schema": "daily-research-score-audit-v1",
        "strategy_id": configured_score_strategy_id(),
        "policy_fingerprint": stage_input_fingerprint(policy_input),
        "score_input_fingerprint": score_input_fingerprint or "",
        "paper_identity": {
            "source": str(getattr(paper, "source", "")),
            "paper_id": str(getattr(paper, "paper_id", "")),
        },
        "keywords": [
            {"keyword": str(keyword), "weight": weight}
            for keyword, weight in keywords.items()
        ],
        "primary_keywords": [str(keyword) for keyword in settings.PRIMARY_KEYWORDS],
        "primary_keyword_entries": _primary_keyword_entries(),
        "negative_keyword_entries": _negative_keyword_entries(),
        # The actual configured expert list and free-text research context
        # intentionally stay out of the exportable audit evidence.  Their
        # fingerprints still distinguish policy changes without disclosure.
        "score_settings": policy_input["score_settings"],
        "model": policy_input["model"],
        "prompt_revision": SCORE_PROMPT_REVISION,
    }


def build_stage_input_fingerprints(
    paper: Any,
    keywords: Mapping[str, Any],
    deep_template: Mapping[str, Any],
) -> Dict[str, str]:
    """Build score, translation and deep-analysis cache keys for one paper.

    The explicit prompt revisions are intentional.  When code changes a
    prompt without adding a new configuration field, incrementing its revision
    invalidates only that stage's incomplete cached output.
    """
    paper_for_score = {
        "source": str(getattr(paper, "source", "")),
        "paper_id": str(getattr(paper, "paper_id", "")),
        "title": str(getattr(paper, "title", "")),
        "authors": [str(author) for author in getattr(paper, "authors", [])],
        "abstract": str(getattr(paper, "abstract", "")),
    }
    score_payload = {
        "schema": "daily-research-score-input-v1",
        "paper": paper_for_score,
        # Preserve configured order as it also defines the prompt's order.
        "keywords": [[str(key), value] for key, value in keywords.items()],
        "primary_keywords": [str(keyword) for keyword in settings.PRIMARY_KEYWORDS],
        "primary_keyword_entries": _primary_keyword_entries(),
        "negative_keyword_entries": _negative_keyword_entries(),
        "research_context": str(settings.RESEARCH_CONTEXT),
        "score_settings": {
            "max_score_per_keyword": settings.MAX_SCORE_PER_KEYWORD,
            "enable_author_bonus": settings.ENABLE_AUTHOR_BONUS,
            "expert_authors": [str(author) for author in settings.EXPERT_AUTHORS],
            "author_bonus_points": settings.AUTHOR_BONUS_POINTS,
            "author_bonus_entries": _author_bonus_entries(),
            "passing_score_base": settings.PASSING_SCORE_BASE,
            "passing_score_weight_coefficient": settings.PASSING_SCORE_WEIGHT_COEFFICIENT,
            "strategy_id": configured_score_strategy_id(),
            "core_relevance_threshold": settings.CORE_RELEVANCE_THRESHOLD,
            "core_keyword_min_score": settings.CORE_KEYWORD_MIN_SCORE,
            "reference_ranking_weight": settings.REFERENCE_RANKING_WEIGHT,
        },
        "model": _model_settings(settings.CHEAP_LLM, settings.CHEAP_LLM.temperature),
        "prompt_revision": SCORE_PROMPT_REVISION,
    }

    translation_payload = {
        "schema": "daily-research-translation-input-v1",
        "abstract": paper_for_score["abstract"],
        # _call_cheap_llm_plain intentionally uses this fixed temperature.
        "model": _model_settings(settings.CHEAP_LLM, 0.3),
        "prompt_revision": "daily-abstract-translation-v1",
    }

    analysis_payload = {
        "schema": "daily-research-analysis-input-v1",
        "paper": {
            "source": paper_for_score["source"],
            "paper_id": paper_for_score["paper_id"],
            "title": paper_for_score["title"],
            "abstract": paper_for_score["abstract"],
            "pdf_url": getattr(paper, "pdf_url", None),
            "arxiv_id": getattr(paper, "arxiv_id", None),
            "updated_date": (
                getattr(paper, "updated_date", None).isoformat()
                if getattr(paper, "updated_date", None) is not None
                else None
            ),
        },
        "research_context": str(settings.RESEARCH_CONTEXT),
        "pdf_parser": {
            "mode": settings.PDF_PARSER_MODE,
            "mineru_model_version": settings.MINERU_MODEL_VERSION,
            "fallback_to_abstract": True,
            "content_limit_characters": 15000,
            "pymupdf_page_limit": 20,
        },
        "model": _model_settings(settings.SMART_LLM, settings.SMART_LLM.temperature),
        "template": dict(deep_template),
        # v3 adds locally asserted PDF/abstract provenance and the optional
        # full-text TL;DR contract. Incomplete cache entries must be
        # regenerated so a fallback result can never masquerade as PDF-based.
        "prompt_revision": "daily-deep-analysis-v3",
    }

    return {
        "score": stage_input_fingerprint(score_payload),
        "translation": stage_input_fingerprint(translation_payload),
        "analysis": stage_input_fingerprint(analysis_payload),
    }
