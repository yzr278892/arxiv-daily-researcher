"""Shared workload-limit helpers for historical maintenance tasks."""

from __future__ import annotations

from typing import Optional

from config import settings


def resolve_history_maintenance_paper_limit(paper_limit: Optional[int] = None) -> int:
    """Return a validated historical-maintenance cap; zero means unlimited."""
    if paper_limit is None:
        paper_limit = getattr(
            settings,
            "HISTORY_MAINTENANCE_MAX_PAPERS_PER_RUN",
            getattr(settings, "DAILY_MAX_PAPERS_PER_RUN", 0),
        )
    if (
        isinstance(paper_limit, bool)
        or not isinstance(paper_limit, int)
        or paper_limit < 0
    ):
        raise ValueError("历史维护每次处理论文数必须是非负整数（0 表示不限）")
    return paper_limit
