"""Water Year and analysis-window rules used by the project."""

from __future__ import annotations

from datetime import date, datetime

from .models import DateWindow


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def water_year(value: date | datetime) -> int:
    """Return the Water Year number for a date.

    Water Year N runs from October 1 of calendar year N-1 through September
    30 of calendar year N. October, November, and December therefore belong
    to the following Water Year number.
    """

    current = _as_date(value)
    return current.year + 1 if current.month >= 10 else current.year


def water_year_start(wy: int) -> date:
    """Return October 1 starting Water Year ``wy``."""

    if wy < 1:
        raise ValueError("Water Year must be positive")
    return date(wy - 1, 10, 1)


def water_year_end(wy: int) -> date:
    """Return September 30 ending Water Year ``wy``."""

    if wy < 1:
        raise ValueError("Water Year must be positive")
    return date(wy, 9, 30)


def previous_water_year_start(latest: date | datetime) -> date:
    """Return the October 1 immediately preceding the current Water Year.

    For a date inside WY 2026, this returns 2024-10-01, the start of the
    previous Water Year WY 2025. This is distinct from the start of the
    Water Year containing ``latest``.
    """

    current_wy = water_year(latest)
    return water_year_start(current_wy - 1)


def recent_water_year_window(
    latest: date | datetime, count: int = 10
) -> DateWindow:
    """Return the latest ``count`` Water Years through the latest date.

    The window is aligned to October 1. For example, a latest date in WY
    2026 and ``count=10`` starts on 2016-10-01 (WY 2017) and ends at the
    supplied latest date.
    """

    if count < 1:
        raise ValueError("count must be at least 1")
    latest_date = _as_date(latest)
    end_wy = water_year(latest_date)
    start = water_year_start(end_wy - count + 1)
    return DateWindow(start=start, end=latest_date)
