"""Small, dependency-free helpers shared by scoring, reports and delivery.

The daily pipeline stores score responses for a long time.  These helpers keep
new V2 fields optional at every read boundary, so historical
``legacy_weighted_keyword_v1`` JSON can still be rendered, sorted and reviewed
without a data migration.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence


LEGACY_WEIGHTED_KEYWORD_V1 = "legacy_weighted_keyword_v1"
CORE_RELEVANCE_V2 = "core_relevance_v2"
LEARNED_PREFERENCE_V1 = "learned_preference_v1"
WEIGHTED_KEYWORD_WITH_PENALTIES_V1 = "weighted_keyword_with_penalties_v1"
SUPPORTED_SCORE_STRATEGIES = frozenset(
    {
        LEGACY_WEIGHTED_KEYWORD_V1,
        CORE_RELEVANCE_V2,
        LEARNED_PREFERENCE_V1,
        WEIGHTED_KEYWORD_WITH_PENALTIES_V1,
    }
)


def normalized_term_key(value: Any) -> str:
    """Deterministic comparison key for a keyword or an author name.

    Case/whitespace/punctuation-insensitive exact matching, mirroring the
    expert-author check: similar-looking terms must not merge accidentally.
    """
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def compute_learned_adjustment(
    *,
    extracted_keywords: Sequence[str],
    author_names: Sequence[str],
    learned_terms: Dict[str, Dict[str, float]],
    configured_keywords: Iterable[str],
    dampening: float,
    term_weight_cap: float,
) -> Dict[str, Any]:
    """Apply the learned keyword/author library to one paper.

    Learned weights are deliberately weaker than directly configured ones:
    each term's net signal is clamped to ``±term_weight_cap`` and then scaled
    by ``dampening`` (< 1).  Terms already present as direct scoring keywords
    are skipped so a preference never double-counts a configured weight.
    Returns the total adjustment plus the matched terms for audit display.
    """
    keyword_map = {
        normalized_term_key(term): (term, float(weight or 0.0))
        for term, weight in (learned_terms.get("keyword") or {}).items()
        if isinstance(term, str) and term.strip()
    }
    author_map = {
        normalized_term_key(term): (term, float(weight or 0.0))
        for term, weight in (learned_terms.get("author") or {}).items()
        if isinstance(term, str) and term.strip()
    }
    configured_keys = {
        normalized_term_key(keyword)
        for keyword in configured_keywords
        if isinstance(keyword, str) and keyword.strip()
    }

    def effective(raw_weight: float) -> float:
        clamped = max(-term_weight_cap, min(term_weight_cap, raw_weight))
        return clamped * dampening

    matched_keywords: List[str] = []
    matched_authors: List[str] = []
    contributions: Dict[str, float] = {}

    for keyword in extracted_keywords:
        key = normalized_term_key(keyword)
        if not key or key in configured_keys:
            continue
        entry = keyword_map.get(key)
        if entry is None:
            continue
        contribution = effective(entry[1])
        if contribution:
            matched_keywords.append(entry[0])
            contributions[entry[0]] = contribution

    for author in author_names:
        key = normalized_term_key(author)
        if not key:
            continue
        entry = author_map.get(key)
        if entry is None:
            continue
        contribution = effective(entry[1])
        if contribution:
            matched_authors.append(entry[0])
            contributions[entry[0]] = contribution

    return {
        "adjustment": math.fsum(contributions.values()),
        "keywords": matched_keywords,
        "authors": matched_authors,
        "contributions": contributions,
    }


def finite_score_or_none(value: Any) -> Optional[float]:
    """Return a finite numeric score, treating absent/invalid values as absent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def response_strategy_id(response: Any) -> str:
    """Return a persisted strategy id, defaulting legacy rows safely."""
    value = getattr(response, "strategy_id", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return LEGACY_WEIGHTED_KEYWORD_V1


def uses_core_relevance_v2(response: Any) -> bool:
    """Whether a response has V2 qualification semantics."""
    return response_strategy_id(response) == CORE_RELEVANCE_V2


def ranking_score_for(response: Any) -> float:
    """Return the stable sort key for a score response.

    New responses provide ``ranking_score`` explicitly.  Old SQLite rows and
    third-party renderer fixtures only have ``total_score``; retaining that
    fallback is essential for backwards-compatible history hydration.
    """
    ranking = finite_score_or_none(getattr(response, "ranking_score", None))
    if ranking is not None:
        return ranking
    legacy_total = finite_score_or_none(getattr(response, "total_score", None))
    return legacy_total if legacy_total is not None else 0.0


def qualification_score_for(response: Any) -> float:
    """Return the score that controls a relevance decision.

    In V2 this is content-only ``relevance_score``.  Legacy history did not
    persist such a field, so its original total remains the correct fallback.
    """
    relevance = finite_score_or_none(getattr(response, "relevance_score", None))
    if relevance is not None:
        return relevance
    legacy_total = finite_score_or_none(getattr(response, "total_score", None))
    return legacy_total if legacy_total is not None else 0.0


def optional_score_value(response: Any, field_name: str) -> Optional[float]:
    """Read an optional finite response field without conflating ``None`` and 0."""
    return finite_score_or_none(getattr(response, field_name, None))


def qualification_threshold_for(response: Any) -> float:
    """Return the persisted qualification threshold with legacy fallback."""
    threshold = finite_score_or_none(getattr(response, "qualification_threshold", None))
    if threshold is not None:
        return threshold
    passing = finite_score_or_none(getattr(response, "passing_score", None))
    return passing if passing is not None else 0.0
