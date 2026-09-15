from datetime import date
import warnings

import pandas as pd

from station_hydro.plots import (
    _observed_intervals,
    _reindex_daily_values,
    plot_consistency_evidence,
    plot_coverage,
    plot_flood_frequency,
    plot_water_management_summary,
)
from station_hydro.models import AvailableDataRecord


def test_observed_intervals_split_downloaded_gaps(tmp_path) -> None:
    observation_dir = tmp_path / "observations"
    observation_dir.mkdir()
    frame = pd.DataFrame(
        {
            "series_id": ["daily"] * 5,
            "observed_date_local": pd.to_datetime(
                [
                    "1956-09-29",
                    "1956-09-30",
                    "2006-08-01",
                    "2006-08-02",
                    "2006-08-03",
                ]
            ),
        }
    )
    frame.to_parquet(observation_dir / "daily_discharge.parquet", index=False)

    intervals = _observed_intervals(tmp_path)

    assert [
        (start.date().isoformat(), end.date().isoformat())
        for start, end in intervals["daily"]
    ] == [
        ("1956-09-29", "1956-09-30"),
        ("2006-08-01", "2006-08-03"),
    ]


def test_reindex_daily_values_preserves_missing_calendar_days() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-01-01", "2025-01-03"]),
            "value": [10.0, 30.0],
        }
    )

    series = _reindex_daily_values(
        frame, "date", "value", "2025-01-01", "2025-01-03"
    )

    assert series.index.tolist() == list(pd.date_range("2025-01-01", "2025-01-03"))
    assert series.iloc[0] == 10.0
    assert pd.isna(series.iloc[1])
    assert series.iloc[2] == 30.0


def test_multi_year_coverage_uses_stable_year_ticks(tmp_path) -> None:
    record = AvailableDataRecord(
        series_id="daily",
        variable="Discharge",
        data_type="Daily values",
        provider_data_type="dv",
        frequency="daily",
        parameter_code="00060",
        declared_start=date(1968, 10, 1),
        declared_end=date(1975, 9, 29),
        analysis_role="core",
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        plot_coverage([record], tmp_path / "figures", "09341200")
    assert not any("AutoDateLocator was unable" in str(item.message) for item in caught)


def test_consistency_plot_accepts_empty_change_point_file(tmp_path) -> None:
    annual_path = tmp_path / "annual_hydrology.csv"
    pd.DataFrame(
        {
            "end_date": ["2025-09-30"],
            "mean_discharge_cfs": [100.0],
            "is_complete": [True],
            "hydrologic_year_class": ["normal"],
        }
    ).to_csv(annual_path, index=False)
    change_points_path = tmp_path / "change_point_candidates.csv"
    change_points_path.write_text("")

    figures = plot_consistency_evidence(
        annual_path,
        tmp_path / "missing_field_measurements.parquet",
        tmp_path / "missing_method_evidence.csv",
        change_points_path,
        tmp_path / "missing_rating_summary.csv",
        tmp_path / "figures",
        "09362520",
    )

    assert (tmp_path / "figures" / "consistency_evidence.png").exists()
    assert figures["png"].endswith("consistency_evidence.png")


def test_consistency_plot_normalizes_mixed_timezone_evidence(tmp_path) -> None:
    annual_path = tmp_path / "annual_hydrology.csv"
    pd.DataFrame(
        {
            "end_date": ["2025-09-30"],
            "mean_discharge_cfs": [100.0],
            "is_complete": [True],
            "hydrologic_year_class": ["normal"],
        }
    ).to_csv(annual_path, index=False)
    method_path = tmp_path / "method_evidence_summary.csv"
    pd.DataFrame(
        {
            "evidence_dimension": ["control_condition"],
            "evidence_value": ["Clear"],
            "record_count": [2],
            "first_observed_at_utc": ["2020-01-01 00:00:00+00:00"],
            "last_observed_at_utc": ["2025-01-01 00:00:00+00:00"],
        }
    ).to_csv(method_path, index=False)
    rating_path = tmp_path / "rating_version_summary.csv"
    pd.DataFrame({"effective_begin": ["2019-01-01 00:00:00"]}).to_csv(
        rating_path, index=False
    )
    change_points_path = tmp_path / "change_point_candidates.csv"
    change_points_path.write_text("")

    figures = plot_consistency_evidence(
        annual_path,
        tmp_path / "missing_field_measurements.parquet",
        method_path,
        change_points_path,
        rating_path,
        tmp_path / "figures",
        "09349800",
    )

    assert (tmp_path / "figures" / "consistency_evidence.png").exists()
    assert figures["png"].endswith("consistency_evidence.png")


def test_flood_frequency_plot_accepts_empty_result_file(tmp_path) -> None:
    flood_frequency_path = tmp_path / "flood_frequency.csv"
    flood_frequency_path.write_text("")

    figures = plot_flood_frequency(
        flood_frequency_path,
        tmp_path / "figures",
        "09379700",
    )

    assert (tmp_path / "figures" / "flood_frequency.png").exists()
    assert figures["png"].endswith("flood_frequency.png")


def test_water_management_plot_accepts_empty_result_files(tmp_path) -> None:
    management_path = tmp_path / "water_management_baselines.csv"
    low_flow_path = tmp_path / "low_flow_management.csv"
    annual_runoff_path = tmp_path / "annual_runoff_exceedance.csv"
    for path in (management_path, low_flow_path, annual_runoff_path):
        path.write_text("")

    figures = plot_water_management_summary(
        management_path,
        low_flow_path,
        tmp_path / "figures",
        "09379700",
        annual_runoff_path,
    )

    assert (tmp_path / "figures" / "water_management_summary.png").exists()
    assert figures["png"].endswith("water_management_summary.png")
