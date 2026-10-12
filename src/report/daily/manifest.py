"""Machine-readable report manifest (contract schema v1.0).

Producer side of the ``arxiv-daily-researcher`` → ``research-assistant``
contract.  Each daily report gets a companion ``manifest.json`` containing:

* report metadata (source, date, run id, keywords, passing score)
* summary statistics (fetched / passed / deep-analyzed / pass rate)
* token usage
* a lightweight per-paper summary

Scope note (agreed with the consumer): only *statistics + paper summaries*.
Rich fields — per-keyword score breakdown, scoring rationale, deep-analysis
sections — stay in the Markdown/HTML reports.  ``tldr`` and the original
``abstract`` are included **only for high-scoring papers** (``qualified``) to
keep the manifest small.

This module is pure stdlib and has no project imports, so it is trivially
testable.  Paths are passed in already-resolved; the caller decides layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
MANIFEST_SUBDIR = "manifest"


def _as_author_list(authors: Any) -> List[str]:
    """Normalize authors to a list of strings (paper dicts store a CSV string)."""
    if isinstance(authors, (list, tuple)):
        return [str(a) for a in authors]
    if isinstance(authors, str):
        return [a.strip() for a in authors.split(",") if a.strip()]
    return []


def _rel(path: Optional[Path], root: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    if root is not None:
        try:
            return str(path.relative_to(root))
        except ValueError:
            pass
    return str(path)


def build_paper_record(paper: Dict[str, Any], analysis_ids: set) -> Dict[str, Any]:
    """Build one lightweight paper summary from an internal paper dict."""
    meta = paper.get("paper_metadata")
    sr = paper.get("score_response")

    keyword_scores = getattr(sr, "keyword_scores", None) or {}
    matched = [
        k
        for k, v in keyword_scores.items()
        if isinstance(v, (int, float)) and v > 0
    ]

    published = paper.get("published")
    if meta is not None and getattr(meta, "published_date", None):
        try:
            published = meta.published_date.strftime("%Y-%m-%d")
        except Exception:
            pass

    paper_id = paper.get("paper_id") or getattr(meta, "paper_id", None)
    qualified = bool(getattr(sr, "is_qualified", False))

    record: Dict[str, Any] = {
        "paper_id": paper_id,
        "title": paper.get("title") or getattr(meta, "title", None),
        "authors": _as_author_list(getattr(meta, "authors", None) or paper.get("authors")),
        "source": getattr(meta, "source", None) or paper.get("source"),
        "published_date": published,
        "url": paper.get("url") or getattr(meta, "url", None),
        "doi": getattr(meta, "doi", None),
        "arxiv_id": getattr(meta, "arxiv_id", None),
        "score": float(getattr(sr, "total_score", 0.0) or 0.0),
        "qualified": qualified,
        "matched_keywords": matched,
        "keywords": list(getattr(sr, "extracted_keywords", None) or []),
        "has_deep_analysis": paper_id in analysis_ids,
        # Text fields are populated for qualified (high-scoring) papers only.
        "tldr": None,
        "abstract": None,
    }

    if qualified:
        record["tldr"] = getattr(sr, "tldr", None) or None
        record["abstract"] = (
            paper.get("abstract") or getattr(meta, "abstract", None) or None
        )

    return record


def _tokens(token_usage: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not token_usage or not token_usage.get("has_data"):
        return None
    by_model = token_usage.get("by_model", {}) or {}
    return {
        "total": token_usage.get("total", 0),
        "prompt": token_usage.get("total_prompt", 0),
        "completion": token_usage.get("total_completion", 0),
        "by_model": by_model,
    }


def build_source_manifest(
    *,
    mode: str,
    run_id: str,
    timestamp: str,
    source: str,
    display_name: str,
    date: str,
    scan_days: Optional[int],
    passing_score: float,
    keywords_dict: Dict[str, float],
    papers: List[Dict[str, Any]],
    analyses: Optional[List[Dict[str, Any]]],
    token_usage: Optional[Dict[str, Any]],
    md_path: Optional[Path] = None,
    html_path: Optional[Path] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble the manifest document for one source's daily report."""
    analyses = analyses or []
    analysis_ids = {
        a.get("paper_id") for a in analyses if isinstance(a, dict) and a.get("paper_id")
    }

    records = [build_paper_record(p, analysis_ids) for p in papers]
    records.sort(key=lambda r: r["score"], reverse=True)

    fetched = len(papers)
    passed = sum(1 for r in records if r["qualified"])

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "arxiv-daily-researcher",
        "mode": mode,
        "report": {
            "run_id": run_id,
            "timestamp": timestamp,
            "source": source,
            "source_display": display_name,
            "date": date,
            "scan_days": scan_days,
            "passing_score": float(passing_score),
            "keywords": [
                {"name": name, "weight": float(weight)}
                for name, weight in (keywords_dict or {}).items()
            ],
        },
        "stats": {
            "fetched": fetched,
            "passed": passed,
            "deep_analyzed": len(analysis_ids),
            "pass_rate": round(passed / fetched, 4) if fetched else 0.0,
        },
        "tokens": _tokens(token_usage),
        "papers": records,
        "outputs": {
            "markdown": _rel(md_path, project_root),
            "html": _rel(html_path, project_root),
        },
    }


def write_manifest(path: Path, data: Dict[str, Any]) -> Path:
    """Write ``data`` as pretty UTF-8 JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
