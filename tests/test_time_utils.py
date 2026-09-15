from datetime import date, datetime

import pytest

from station_hydro.time_utils import (
    previous_water_year_start,
    recent_water_year_window,
    water_year,
    water_year_end,
    water_year_start,
)


def test_water_year_definition() -> None:
    assert water_year(date(2025, 9, 30)) == 2025
    assert water_year(date(2025, 10, 1)) == 2026
    assert water_year(datetime(2026, 8, 27, 12, 0)) == 2026
    assert water_year_start(2026) == date(2025, 10, 1)
    assert water_year_end(2026) == date(2026, 9, 30)


def test_previous_water_year_start_is_explicitly_distinct() -> None:
    assert previous_water_year_start(date(2026, 8, 27)) == date(2024, 10, 1)


def test_recent_ten_water_years_aligns_to_october_first() -> None:
    window = recent_water_year_window(date(2026, 8, 27), count=10)
    assert window.start == date(2016, 10, 1)
    assert window.end == date(2026, 8, 27)


def test_recent_window_requires_positive_count() -> None:
    with pytest.raises(ValueError):
        recent_water_year_window(date(2026, 8, 27), count=0)
