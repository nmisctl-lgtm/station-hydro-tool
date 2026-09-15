from datetime import date, datetime, timezone

import pandas as pd

from station_hydro.quality import analyze_series


def test_quality_flags_negative_and_outlier_candidates() -> None:
    timestamps = pd.date_range("2026-01-01", periods=12, freq="D", tz="UTC")
    frame = pd.DataFrame(
        {
            "observed_at_utc": timestamps,
            "value": [1.0, 1.1, 1.0, 1.2, 1.1, 1.0, 1.2, 1.1, 1.0, 1.2, -1.0, 100.0],
            "quality_code": ["A"] * 12,
        }
    )
    result = analyze_series(
        frame,
        "USGS:09342500:dv:00060:00003:19544",
        "Discharge",
        declared_start=timestamps[0].date(),
        declared_end=timestamps[-1].date(),
        frequency="daily",
    )
    assert result.summary["expected_count"] == 12
    assert result.summary["completeness"] == 1.0
    assert result.summary["completeness_method"] == "provider_declared_daily_range"
    assert result.summary["negative_value_count"] == 1
    assert "negative_value" in set(result.flags["flag_type"])
    assert result.summary["status"] == "review_required"


def test_annual_peak_completeness_counts_years_through_as_of_year() -> None:
    frame = pd.DataFrame(
        {
            "observed_date_local": pd.to_datetime(
                ["2020-05-01", "2021-05-01", "2023-05-01"]
            ).date,
            "observed_at_utc": pd.to_datetime(
                ["2020-05-01", "2021-05-01", "2023-05-01"], utc=True
            ),
            "value": [100.0, 200.0, 150.0],
            "quality_code": [None, None, None],
        }
    )
    result = analyze_series(
        frame,
        "USGS:09342500:pk",
        "Annual peak discharge",
        declared_start=date(2020, 1, 1),
        declared_end=date(2023, 12, 31),
        frequency="annual_peak",
        as_of_date=date(2024, 12, 31),
    )
    assert result.summary["completeness_numerator"] == 3
    assert result.summary["completeness_denominator"] == 5
    assert result.summary["expected_count_unit"] == "years"
    assert result.summary["completeness"] == 0.6


def test_annual_peak_completeness_uses_water_year_not_peak_calendar_year() -> None:
    frame = pd.DataFrame(
        {
            "water_year": [1959, 1960],
            "observed_date_local": pd.to_datetime(
                ["1959-08-06", "1959-10-02"]
            ).date,
            "observed_at_utc": pd.to_datetime(
                ["1959-08-06", "1959-10-02"], utc=True
            ),
            "value": [4_950.0, 8_900.0],
            "quality_code": [None, None],
        }
    )
    result = analyze_series(
        frame,
        "USGS:09357000:pk",
        "Annual peak discharge",
        declared_start=date(1956, 8, 1),
        frequency="annual_peak",
        as_of_date=date(1960, 12, 31),
    )
    assert result.summary["completeness_numerator"] == 2
    assert result.summary["completeness_denominator"] == 5


def test_annual_peak_completeness_derives_water_year_from_peak_date() -> None:
    frame = pd.DataFrame(
        {
            "observed_date_local": pd.to_datetime(
                ["1959-08-06", "1959-10-02"]
            ).date,
            "observed_at_utc": pd.to_datetime(
                ["1959-08-06", "1959-10-02"], utc=True
            ),
            "value": [4_950.0, 8_900.0],
            "quality_code": [None, None],
        }
    )
    result = analyze_series(
        frame,
        "USGS:09357000:pk",
        "Annual peak discharge",
        declared_start=date(1956, 8, 1),
        frequency="annual_peak",
        as_of_date=date(1960, 12, 31),
    )
    assert result.summary["completeness_numerator"] == 2


def test_unit_completeness_uses_declared_15_minute_grid() -> None:
    timestamps = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "observed_at_utc": timestamps,
            "value": [1.0, 1.1, 1.2, 1.3],
            "quality_code": [None] * 4,
        }
    )
    result = analyze_series(
        frame,
        "USGS:09342500:uv:00060",
        "Discharge",
        declared_start=date(2024, 1, 1),
        declared_end=date(2024, 1, 2),
        frequency="unit",
        as_of_date=date(2024, 1, 2),
    )
    assert result.summary["completeness_numerator"] == 4
    assert result.summary["completeness_denominator"] == 192
    assert result.summary["expected_count_unit"] == "15-minute observations"
    assert result.summary["completeness_method"] == "provider_declared_15min_range_through_current_day"
