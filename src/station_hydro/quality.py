"""Non-destructive station-series quality checks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .retrieval import available_data_from_json
from .storage import write_json
from .field_measurements import validate_field_pairs_against_daily
from .paths import output_station_root


@dataclass
class QualityResult:
    summary: dict[str, Any]
    flags: pd.DataFrame


def _iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _robust_outlier_mask(values: pd.Series, threshold: float = 6.0) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.notna()
    if valid.sum() < 10:
        return pd.Series(False, index=values.index)
    transformed = np.log1p(numeric[valid]) if (numeric[valid] >= 0).all() else numeric[valid]
    median = float(transformed.median())
    mad = float(np.median(np.abs(transformed - median)))
    if mad == 0:
        return pd.Series(False, index=values.index)
    score = 0.6745 * (transformed - median) / mad
    result = pd.Series(False, index=values.index)
    result.loc[valid] = score.abs() > threshold
    return result


def _gap_stats(timestamps: pd.Series, frequency: str) -> tuple[int, float | None, float | None]:
    values = pd.to_datetime(timestamps, errors="coerce", utc=True).dropna().sort_values()
    if len(values) < 2:
        return 0, None, None
    deltas = values.diff().dropna().dt.total_seconds()
    median_seconds = float(deltas.median())
    if frequency == "daily":
        threshold = 1.5 * 24 * 3600
    else:
        threshold = max(3 * median_seconds, 3600)
    gaps = deltas[deltas > threshold]
    return int(len(gaps)), float(deltas.max() / 3600), median_seconds


def analyze_series(
    frame: pd.DataFrame,
    series_id: str,
    variable: str,
    declared_start: date | None = None,
    declared_end: date | None = None,
    frequency: str = "unknown",
    as_of_date: date | None = None,
) -> QualityResult:
    """Calculate structural checks and a frequency-aware completeness metric.

    ``as_of_date`` makes the denominator reproducible in tests and prevents a
    provider range that ends in the future from inflating an expected count.
    For a continuous USGS series the Phase 1 convention is a 15-minute grid
    (96 observations per day); for annual peaks it is one expected value per
    calendar year, including the current year.
    """

    as_of_date = as_of_date or date.today()
    value = pd.to_numeric(frame.get("value"), errors="coerce")
    timestamps = pd.to_datetime(frame.get("observed_at_utc"), errors="coerce", utc=True)
    quality_codes = (
        frame.get("quality_code", pd.Series(dtype="string"))
        .fillna("<none>")
        .astype(str)
        .value_counts()
        .to_dict()
    )
    duplicate_count = int(timestamps.duplicated().sum())
    unsorted_count = int((timestamps.dropna().diff().dt.total_seconds() < 0).sum())
    negative_mask = value < 0
    outlier_mask = _robust_outlier_mask(value)
    gap_count, largest_gap_hours, median_interval_seconds = _gap_stats(timestamps, frequency)
    valid_timestamps = timestamps.dropna()
    valid_values = value.notna()
    observed_start = valid_timestamps.min() if not valid_timestamps.empty else None
    observed_end = valid_timestamps.max() if not valid_timestamps.empty else None

    expected_count: int | None = None
    completeness: float | None = None
    completeness_method: str | None = None
    expected_count_unit: str | None = None
    completeness_end: date | None = None
    peak_years = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    if frequency == "daily" and declared_start and declared_end:
        expected_count = (declared_end - declared_start).days + 1
        completeness = float(valid_values.sum() / expected_count) if expected_count else None
        completeness_method = "provider_declared_daily_range"
        expected_count_unit = "days"
        completeness_end = declared_end
    elif frequency == "annual_peak" and declared_start:
        # Annual peak completeness is a coverage-of-years statistic.  The
        # current year is included even when its annual peak is not available
        # yet, because the user-facing question is "how much of the record
        # through today is represented?".
        expected_count = max(as_of_date.year - declared_start.year + 1, 0)
        if "water_year" in frame:
            peak_years = pd.to_numeric(frame["water_year"], errors="coerce")
        elif "observed_date_local" in frame:
            peak_dates = pd.to_datetime(frame["observed_date_local"], errors="coerce")
            peak_years = peak_dates.dt.year + (peak_dates.dt.month >= 10).astype("Int64")
        else:
            peak_years = timestamps.dt.year + (timestamps.dt.month >= 10).astype("Int64")
        observed_years = peak_years[valid_values].dropna().nunique()
        completeness = float(observed_years / expected_count) if expected_count else None
        completeness_method = "provider_declared_start_through_current_year"
        expected_count_unit = "years"
        completeness_end = as_of_date
    elif frequency == "unit" and declared_start:
        # USGS unit/continuous data are normally quarter-hour observations for
        # this station.  Count non-null values against the complete declared
        # calendar-day grid from the first day through today (or the provider's
        # end date when it is earlier than today).
        end_date = min(declared_end or as_of_date, as_of_date)
        expected_days = max((end_date - declared_start).days + 1, 0)
        expected_count = expected_days * 24 * 4
        completeness = float(valid_values.sum() / expected_count) if expected_count else None
        completeness_method = "provider_declared_15min_range_through_current_day"
        expected_count_unit = "15-minute observations"
        completeness_end = end_date
    elif (
        frequency == "unit"
        and median_interval_seconds
        and observed_start is not None
        and observed_end is not None
    ):
        duration_seconds = (observed_end - observed_start).total_seconds()
        expected_count = int(round(duration_seconds / median_interval_seconds)) + 1
        completeness = float(valid_values.sum() / expected_count) if expected_count else None
        completeness_method = "inferred_from_median_observed_interval"
        expected_count_unit = "observations"

    flags: list[dict[str, Any]] = []
    for index in frame.index[negative_mask.fillna(False)]:
        flags.append(
            {
                "series_id": series_id,
                "variable": variable,
                "flag_type": "negative_value",
                "observed_at_utc": frame.loc[index, "observed_at_utc"],
                "value": frame.loc[index, "value"],
                "quality_code": frame.loc[index, "quality_code"],
                "message": "Value is below zero; review against variable semantics.",
            }
        )
    for index in frame.index[outlier_mask]:
        flags.append(
            {
                "series_id": series_id,
                "variable": variable,
                "flag_type": "robust_outlier_candidate",
                "observed_at_utc": frame.loc[index, "observed_at_utc"],
                "value": frame.loc[index, "value"],
                "quality_code": frame.loc[index, "quality_code"],
                "message": "Robust statistical screen candidate; not automatically invalid.",
            }
        )

    summary = {
        "series_id": series_id,
        "variable": variable,
        "frequency": frequency,
        "declared_start": declared_start,
        "declared_end": declared_end,
        "observed_start": observed_start,
        "observed_end": observed_end,
        "row_count": int(len(frame)),
        "valid_value_count": int(valid_values.sum()),
        "missing_value_count": int(value.isna().sum()),
        "expected_count": expected_count,
        "expected_count_unit": expected_count_unit,
        "completeness_numerator": int(valid_values.sum())
        if frequency != "annual_peak"
        else int(peak_years[valid_values].dropna().nunique()),
        "completeness_denominator": expected_count,
        "completeness_end": completeness_end,
        "completeness": completeness,
        "completeness_method": completeness_method,
        "median_interval_seconds": median_interval_seconds,
        "duplicate_timestamp_count": duplicate_count,
        "unsorted_timestamp_count": unsorted_count,
        "negative_value_count": int(negative_mask.fillna(False).sum()),
        "outlier_candidate_count": int(outlier_mask.sum()),
        "gap_count": gap_count,
        "largest_gap_hours": largest_gap_hours,
        "quality_code_counts": quality_codes,
        "status": "review_required" if flags else "no_flags",
    }
    return QualityResult(summary, pd.DataFrame(flags))


def analyze_station_package(station_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    inventory = available_data_from_json(station_root / "metadata" / "available_data.json")
    by_series = {record.series_id: record for record in inventory}
    summaries: list[dict[str, Any]] = []
    flag_frames: list[pd.DataFrame] = []
    for path in sorted((station_root / "observations").glob("*.parquet")):
        frame = pd.read_parquet(path)
        if frame.empty:
            continue
        # Validation tables (field measurements and paired diagnostics) are
        # handled separately; only canonical provider series enter this loop.
        required_columns = {"series_id", "variable", "frequency", "value", "observed_at_utc"}
        if not required_columns.issubset(frame.columns):
            continue
        series_id = str(frame["series_id"].iloc[0])
        record = by_series.get(series_id)
        result = analyze_series(
            frame,
            series_id=series_id,
            variable=str(frame["variable"].iloc[0]),
            declared_start=record.declared_start if record else None,
            declared_end=record.declared_end if record else None,
            frequency=str(frame["frequency"].iloc[0]),
        )
        summaries.append(result.summary)
        if not result.flags.empty:
            flag_frames.append(result.flags)
    summary_frame = pd.DataFrame(summaries)
    flags = (
        pd.concat(flag_frames, ignore_index=True)
        if flag_frames
        else pd.DataFrame(
            columns=[
                "series_id",
                "variable",
                "flag_type",
                "observed_at_utc",
                "value",
                "quality_code",
                "message",
            ]
        )
    )
    payload = {
        "station_id": station_root.name.removeprefix("USGS_"),
        "generated_at": datetime.now(timezone.utc),
        "series_count": len(summary_frame),
        "flag_count": len(flags),
        "series_with_flags": int((summary_frame["status"] == "review_required").sum())
        if not summary_frame.empty
        else 0,
    }
    return summary_frame, flags, payload


def write_station_quality(station_root: Path, output_root: Path | None = None) -> Path:
    summary, flags, payload = analyze_station_package(station_root)
    field_pairs_path = station_root / "observations" / "field_stage_discharge_pairs.parquet"
    daily_path = station_root / "observations" / "daily_discharge.parquet"
    field_validation = pd.DataFrame()
    if field_pairs_path.exists() and daily_path.exists():
        field_validation = validate_field_pairs_against_daily(
            pd.read_parquet(field_pairs_path), daily_path
        )
        if not field_validation.empty:
            valid_error = field_validation["relative_error_pct"].dropna()
            payload["field_visit_pairs"] = len(field_validation)
            payload["field_validation_matched_days"] = int(
                field_validation["discharge_cfs_published"].notna().sum()
            )
            payload["field_validation_median_relative_error_pct"] = (
                float(valid_error.median()) if not valid_error.empty else None
            )
            payload["field_validation_median_absolute_error_cfs"] = (
                float(field_validation["absolute_error_cfs"].abs().median())
                if not field_validation["absolute_error_cfs"].dropna().empty
                else None
            )
    roots = [station_root / "quality"]
    if output_root is not None:
        roots.append(output_station_root(output_root, station_root) / "quality")
    for root in set(roots):
        root.mkdir(parents=True, exist_ok=True)
        summary.to_csv(root / "series_summary.csv", index=False)
        flags.to_csv(root / "quality_flags.csv", index=False)
        field_validation.to_csv(root / "field_measurement_validation.csv", index=False)
        write_json(root / "quality_summary.json", payload)
    return roots[0]
