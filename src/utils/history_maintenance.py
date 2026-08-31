"""Shared configuration helpers for historical maintenance tasks."""

from __future__ import annotations

import re
from datetime import datetime, time as clock_time
from typing import Optional


HISTORY_MAINTENANCE_RUN_MODES = frozenset({"idle", "time_window"})
DEFAULT_HISTORY_MAINTENANCE_RUN_MODE = "idle"
DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START = "00:00"
DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END = "06:00"
_CLOCK_TIME_RE = re.compile(r"\d{1,2}:\d{2}")


def normalize_history_maintenance_run_mode(value: object) -> str:
    """Validate one persisted maintenance scheduling mode."""
    if not isinstance(value, str):
        raise ValueError(
            "history_maintenance.run_mode 必须是 idle 或 time_window"
        )
    normalized = value.strip().lower()
    if normalized not in HISTORY_MAINTENANCE_RUN_MODES:
        raise ValueError(
            "history_maintenance.run_mode 必须是 idle 或 time_window"
        )
    return normalized


def normalize_history_maintenance_clock_time(value: object, *, field: str) -> str:
    """Normalize a portable ``HH:MM`` time value used by the scheduler."""
    if not isinstance(value, str) or not _CLOCK_TIME_RE.fullmatch(value.strip()):
        raise ValueError(f"{field} 必须是 HH:MM 格式")
    hour, minute = (int(part) for part in value.strip().split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{field} 超出有效时间范围")
    return f"{hour:02d}:{minute:02d}"


def resolve_history_maintenance_schedule(
    run_mode: object = DEFAULT_HISTORY_MAINTENANCE_RUN_MODE,
    time_window_start: object = DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START,
    time_window_end: object = DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END,
) -> tuple[str, str, str]:
    """Return the normalized history-maintenance scheduling configuration.

    A window whose endpoints are equal is deliberately rejected instead of
    treating it as either an empty or all-day period.  Cross-midnight windows
    (for example ``22:00`` to ``06:00``) are valid.
    """
    normalized_mode = normalize_history_maintenance_run_mode(run_mode)
    normalized_start = normalize_history_maintenance_clock_time(
        time_window_start, field="history_maintenance.time_window_start"
    )
    normalized_end = normalize_history_maintenance_clock_time(
        time_window_end, field="history_maintenance.time_window_end"
    )
    if normalized_start == normalized_end:
        raise ValueError(
            "history_maintenance.time_window_start 与 "
            "history_maintenance.time_window_end 不能相同"
        )
    return normalized_mode, normalized_start, normalized_end


def history_maintenance_window_is_open(
    now: datetime | clock_time,
    time_window_start: object = DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_START,
    time_window_end: object = DEFAULT_HISTORY_MAINTENANCE_TIME_WINDOW_END,
) -> bool:
    """Whether a local clock instant is inside a configured maintenance window."""
    if not isinstance(now, (datetime, clock_time)):
        raise TypeError("now 必须是 datetime 或 time")
    start = normalize_history_maintenance_clock_time(
        time_window_start, field="history_maintenance.time_window_start"
    )
    end = normalize_history_maintenance_clock_time(
        time_window_end, field="history_maintenance.time_window_end"
    )
    if start == end:
        raise ValueError(
            "history_maintenance.time_window_start 与 "
            "history_maintenance.time_window_end 不能相同"
        )

    current_minutes = now.hour * 60 + now.minute
    start_hour, start_minute = (int(part) for part in start.split(":"))
    end_hour, end_minute = (int(part) for part in end.split(":"))
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def resolve_history_maintenance_paper_limit(paper_limit: Optional[int] = None) -> int:
    """Return a validated historical-maintenance cap; zero means unlimited."""
    if paper_limit is None:
        # This helper is also used by config validation in the thin WebUI
        # image.  Keep the settings import lazy so pure schedule helpers do
        # not pull in the full worker configuration (or create an import loop).
        from config import settings

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
