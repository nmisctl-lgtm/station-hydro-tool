"""Transparent hydrologic statistics for one station.

The module deliberately keeps descriptive statistics separate from provider
data.  It does not revise USGS values, and its flood-frequency result is a
screening estimate rather than a Bulletin 17C publication unless the required
historical-peak inputs and procedures are added explicitly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import pearson3, skew

from .storage import write_json
from .time_utils import water_year, water_year_end, water_year_start


ACRE_FEET_PER_CFS_DAY = 1.9834710743801653
MIN_COMPLETE_WY_COMPLETENESS = 0.90
HIGH_FLOW_QUANTILE = 0.95
FLOOD_RETURN_PERIODS = (2, 5, 10, 25, 50, 100)
BASEFLOW_ALPHA = 0.925
BASEFLOW_PASSES = 3
FDC_EXCEEDANCE_LEVELS = (1, 5, 10, 25, 50, 75, 90, 95, 99)
MANAGEMENT_EXCEEDANCE_LEVELS = (50, 75, 90, 95)
LOW_FLOW_DURATIONS = (7, 14, 30)


def _daily_values(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a date-indexable daily discharge frame without imputing values."""

    if frame.empty:
        return pd.DataFrame(columns=["observed_date", "discharge_cfs"])
    result = pd.DataFrame()
    if "observed_date_local" in frame:
        result["observed_date"] = pd.to_datetime(
            frame["observed_date_local"], errors="coerce"
        ).dt.date
    elif "observed_date" in frame:
        result["observed_date"] = pd.to_datetime(
            frame["observed_date"], errors="coerce"
        ).dt.date
    elif "observed_at_utc" in frame:
        result["observed_date"] = pd.to_datetime(
            frame["observed_at_utc"], errors="coerce", utc=True
        ).dt.date
    else:
        raise ValueError("Daily discharge frame needs observed_date_local or observed_at_utc")
    result["discharge_cfs"] = pd.to_numeric(
        frame.get("value", frame.get("discharge_cfs")), errors="coerce"
    )
    result = result.dropna(subset=["observed_date"])
    # A duplicate daily date is retained as a single QA candidate, not silently
    # averaged into the hydrologic record.
    return result.drop_duplicates("observed_date", keep="first").sort_values(
        "observed_date"
    ).reset_index(drop=True)


def _daily_gap_summary(daily: pd.DataFrame, long_gap_days: int = 7) -> dict[str, Any]:
    """Describe calendar gaps without filling or interpolating observations."""

    prepared = _daily_values(daily)
    dates = pd.DatetimeIndex(
        pd.to_datetime(prepared["observed_date"], errors="coerce").dropna()
    )
    if dates.empty:
        return {
            "observed_day_count": 0,
            "calendar_span_days": 0,
            "missing_day_count": 0,
            "long_gap_threshold_days": long_gap_days,
            "long_gap_count": 0,
            "long_gaps": [],
        }
    dates = dates.drop_duplicates().sort_values()
    calendar = pd.date_range(dates.min(), dates.max(), freq="D")
    missing = calendar.difference(dates)
    gaps: list[dict[str, Any]] = []
    if len(missing):
        missing_frame = pd.DataFrame({"date": missing})
        missing_frame["run"] = missing_frame["date"].diff().dt.days.ne(1).cumsum()
        for _, group in missing_frame.groupby("run", sort=True):
            count = int(len(group))
            if count >= long_gap_days:
                gaps.append(
                    {
                        "start_date": group["date"].iloc[0].date().isoformat(),
                        "end_date": group["date"].iloc[-1].date().isoformat(),
                        "missing_days": count,
                    }
                )
    return {
        "observed_start": dates.min().date().isoformat(),
        "observed_end": dates.max().date().isoformat(),
        "observed_day_count": int(len(dates)),
        "calendar_span_days": int(len(calendar)),
        "missing_day_count": int(len(missing)),
        "long_gap_threshold_days": long_gap_days,
        "long_gap_count": len(gaps),
        "long_gaps": gaps,
    }


def _with_water_year(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["water_year"] = result["observed_date"].map(water_year)
    result["wy_month"] = result["observed_date"].map(
        lambda value: value.month - 9 if value.month >= 10 else value.month + 3
    )
    return result


def _expected_days(wy: int) -> int:
    return (water_year_end(wy) - water_year_start(wy)).days + 1


def _rolling_extrema(
    group: pd.DataFrame, window_days: int
) -> tuple[float | None, float | None]:
    if group.empty:
        return None, None
    indexed = group.set_index(pd.to_datetime(group["observed_date"]))["discharge_cfs"]
    full_index = pd.date_range(
        water_year_start(int(group["water_year"].iloc[0])),
        water_year_end(int(group["water_year"].iloc[0])),
        freq="D",
    )
    rolling = (
        indexed.reindex(full_index)
        .rolling(window_days, min_periods=window_days)
        .mean()
        .dropna()
    )
    if rolling.empty:
        return None, None
    return float(rolling.min()), float(rolling.max())


def _seven_day_extrema(group: pd.DataFrame) -> tuple[float | None, float | None]:
    """Backward-compatible wrapper for the original seven-day fields."""

    return _rolling_extrema(group, 7)


def summarize_annual_hydrology(
    daily: pd.DataFrame,
    min_completeness: float = MIN_COMPLETE_WY_COMPLETENESS,
) -> pd.DataFrame:
    """Summarize annual flow, completeness, high/low statistics, and WY class."""

    prepared = _with_water_year(_daily_values(daily))
    if prepared.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for wy, group in prepared.groupby("water_year", sort=True):
        valid = group.dropna(subset=["discharge_cfs"])
        expected = _expected_days(int(wy))
        valid_days = int(len(valid))
        completeness = valid_days / expected
        max_date = None
        if not valid.empty:
            max_index = valid["discharge_cfs"].idxmax()
            max_date = valid.loc[max_index, "observed_date"]
        extrema = {
            duration: _rolling_extrema(group, duration)
            for duration in LOW_FLOW_DURATIONS
        }
        rows.append(
            {
                "water_year": int(wy),
                "start_date": water_year_start(int(wy)),
                "end_date": water_year_end(int(wy)),
                "valid_days": valid_days,
                "expected_days": expected,
                "completeness": completeness,
                "is_complete": completeness >= min_completeness,
                "mean_discharge_cfs": float(valid["discharge_cfs"].mean())
                if not valid.empty
                else None,
                "std_daily_discharge_cfs": float(valid["discharge_cfs"].std(ddof=1))
                if len(valid) > 1
                else None,
                "total_runoff_acre_ft": float(valid["discharge_cfs"].sum())
                * ACRE_FEET_PER_CFS_DAY
                if not valid.empty
                else None,
                "min_daily_discharge_cfs": float(valid["discharge_cfs"].min())
                if not valid.empty
                else None,
                "max_daily_discharge_cfs": float(valid["discharge_cfs"].max())
                if not valid.empty
                else None,
                "date_of_max_daily_discharge": max_date,
                "min_7day_mean_cfs": extrema[7][0],
                "max_7day_mean_cfs": extrema[7][1],
                "min_14day_mean_cfs": extrema[14][0],
                "max_14day_mean_cfs": extrema[14][1],
                "min_30day_mean_cfs": extrema[30][0],
                "max_30day_mean_cfs": extrema[30][1],
            }
        )
    result = pd.DataFrame(rows)
    complete = result.loc[result["is_complete"] & result["mean_discharge_cfs"].notna()]
    if complete.empty:
        result["hydrologic_year_class"] = pd.NA
        return result
    q25, q75 = complete["mean_discharge_cfs"].quantile([0.25, 0.75])

    def classify(value: float | None) -> str | None:
        if value is None or pd.isna(value):
            return None
        if value <= q25:
            return "dry"
        if value >= q75:
            return "wet"
        return "normal"

    result["hydrologic_year_class"] = result["mean_discharge_cfs"].map(classify)
    return result


def summarize_monthly_hydrology(
    daily: pd.DataFrame,
    annual: pd.DataFrame | None = None,
    min_completeness: float = MIN_COMPLETE_WY_COMPLETENESS,
) -> pd.DataFrame:
    """Return Water-Year-month climatology and current-WY-to-date values.

    Historical means and standard deviations are calculated from Water-Year
    monthly means over complete Water Years.  The standard deviation therefore
    describes interannual spread, not the storm-scale spread of all pooled
    daily observations.
    """

    prepared = _with_water_year(_daily_values(daily))
    if prepared.empty:
        return pd.DataFrame()
    annual = annual if annual is not None else summarize_annual_hydrology(prepared)
    complete_wys = set(
        annual.loc[annual["is_complete"], "water_year"].astype(int).tolist()
    )
    full_mean = float(prepared["discharge_cfs"].mean())
    current_wy = int(prepared["water_year"].max())
    month_rows: list[dict[str, Any]] = []
    for (wy, wy_month), group in prepared.groupby(["water_year", "wy_month"], sort=True):
        valid = group.dropna(subset=["discharge_cfs"])
        month_rows.append(
            {
                "water_year": int(wy),
                "wy_month": int(wy_month),
                "valid_days": int(len(valid)),
                "mean_discharge_cfs": float(valid["discharge_cfs"].mean())
                if not valid.empty
                else None,
                "total_runoff_acre_ft": float(valid["discharge_cfs"].sum())
                * ACRE_FEET_PER_CFS_DAY
                if not valid.empty
                else None,
            }
        )
    monthly_by_wy = pd.DataFrame(month_rows)
    rows: list[dict[str, Any]] = []
    for wy_month in range(1, 13):
        historical = monthly_by_wy[
            (monthly_by_wy["wy_month"] == wy_month)
            & monthly_by_wy["water_year"].isin(complete_wys)
        ]
        current = monthly_by_wy[
            (monthly_by_wy["wy_month"] == wy_month)
            & (monthly_by_wy["water_year"] == current_wy)
        ]
        historical_q = historical["mean_discharge_cfs"].dropna()
        historical_v = historical["total_runoff_acre_ft"].dropna()
        current_row = current.iloc[0] if not current.empty else None
        rows.append(
            {
                "wy_month": wy_month,
                "month_label": [
                    "Oct",
                    "Nov",
                    "Dec",
                    "Jan",
                    "Feb",
                    "Mar",
                    "Apr",
                    "May",
                    "Jun",
                    "Jul",
                    "Aug",
                    "Sep",
                ][wy_month - 1],
                "historical_water_year_count": int(len(historical_q)),
                "historical_mean_discharge_cfs": float(historical_q.mean())
                if not historical_q.empty
                else None,
                "historical_std_discharge_cfs": float(historical_q.std(ddof=1))
                if len(historical_q) > 1
                else None,
                "historical_mean_runoff_acre_ft": float(historical_v.mean())
                if not historical_v.empty
                else None,
                "historical_std_runoff_acre_ft": float(historical_v.std(ddof=1))
                if len(historical_v) > 1
                else None,
                "normalized_mean_discharge": float(historical_q.mean() / full_mean)
                if full_mean and not historical_q.empty
                else None,
                "normalized_std_discharge": float(historical_q.std(ddof=1) / full_mean)
                if full_mean and len(historical_q) > 1
                else None,
                "current_water_year": current_wy,
                "current_wy_valid_days": int(current_row["valid_days"])
                if current_row is not None
                else 0,
                "current_wy_mean_discharge_cfs": float(current_row["mean_discharge_cfs"])
                if current_row is not None and pd.notna(current_row["mean_discharge_cfs"])
                else None,
                "current_wy_normalized_discharge": float(
                    current_row["mean_discharge_cfs"] / full_mean
                )
                if current_row is not None
                and pd.notna(current_row["mean_discharge_cfs"])
                and full_mean
                else None,
                "current_wy_to_date": True,
                "full_period_mean_discharge_cfs": full_mean,
                "historical_completeness_rule": f">={min_completeness:.0%} valid daily values per Water Year",
            }
        )
    return pd.DataFrame(rows)


def _daily_series(daily: pd.DataFrame) -> pd.Series:
    """Return valid daily discharge indexed by local calendar date."""

    prepared = _daily_values(daily).dropna(subset=["discharge_cfs"])
    if prepared.empty:
        return pd.Series(dtype="float64", name="discharge_cfs")
    series = pd.Series(
        prepared["discharge_cfs"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(prepared["observed_date"]),
        name="discharge_cfs",
    )
    return series.sort_index()


def _lyne_hollick_pass(values: np.ndarray, alpha: float) -> np.ndarray:
    """Apply one forward pass of the Lyne-Hollick recursive filter."""

    if len(values) == 0:
        return values.copy()
    quickflow = np.zeros(len(values), dtype=float)
    for index in range(1, len(values)):
        candidate = alpha * quickflow[index - 1] + (1 + alpha) / 2 * (
            values[index] - values[index - 1]
        )
        quickflow[index] = min(max(candidate, 0.0), values[index])
    return np.clip(values - quickflow, 0.0, values)


def separate_baseflow(
    daily: pd.DataFrame,
    alpha: float = BASEFLOW_ALPHA,
    passes: int = BASEFLOW_PASSES,
) -> pd.DataFrame:
    """Estimate baseflow without filling missing daily observations.

    This is a descriptive Lyne-Hollick recursive digital-filter estimate. Each
    contiguous observed segment is filtered independently, using forward,
    reverse, forward passes by default. Gaps remain missing so the result is
    not a synthetic discharge record. Baseflow separation is an indicator of
    delayed flow contribution, not a regulatory ecological-flow threshold.
    """

    series = _daily_series(daily)
    if series.empty:
        return pd.DataFrame(
            columns=[
                "observed_date_local",
                "discharge_cfs",
                "baseflow_cfs",
                "quickflow_cfs",
                "baseflow_method",
                "alpha",
                "passes",
            ]
        )
    if not 0 < float(alpha) < 1:
        raise ValueError("alpha must be between 0 and 1")
    if int(passes) < 1:
        raise ValueError("passes must be at least one")
    full_index = pd.date_range(series.index.min(), series.index.max(), freq="D")
    observed = series.reindex(full_index)
    valid = observed.notna()
    baseflow = pd.Series(np.nan, index=full_index, dtype=float)
    groups = (valid != valid.shift(fill_value=False)).cumsum()
    for _, segment in observed[valid].groupby(groups[valid]):
        if len(segment) < 2:
            baseflow.loc[segment.index] = segment.to_numpy(dtype=float)
            continue
        filtered = segment.to_numpy(dtype=float)
        directions = [1 if index % 2 == 0 else -1 for index in range(int(passes))]
        for direction in directions:
            working = filtered if direction == 1 else filtered[::-1]
            filtered = _lyne_hollick_pass(working, float(alpha))[::1 if direction == 1 else -1]
        baseflow.loc[segment.index] = filtered
    result = pd.DataFrame(
        {
            "observed_date_local": full_index.date,
            "discharge_cfs": observed.to_numpy(dtype=float),
            "baseflow_cfs": baseflow.to_numpy(dtype=float),
        }
    )
    result["quickflow_cfs"] = result["discharge_cfs"] - result["baseflow_cfs"]
    result.loc[result["discharge_cfs"].isna(), "quickflow_cfs"] = np.nan
    result["baseflow_method"] = "Lyne-Hollick recursive digital filter"
    result["alpha"] = float(alpha)
    result["passes"] = int(passes)
    return result


def summarize_baseflow(baseflow_daily: pd.DataFrame) -> pd.DataFrame:
    """Return annual baseflow index and descriptive ecological-flow indicators."""

    if baseflow_daily.empty:
        return pd.DataFrame()
    frame = baseflow_daily.copy()
    frame["date"] = pd.to_datetime(frame["observed_date_local"], errors="coerce")
    frame["discharge_cfs"] = pd.to_numeric(frame["discharge_cfs"], errors="coerce")
    frame["baseflow_cfs"] = pd.to_numeric(frame["baseflow_cfs"], errors="coerce")
    frame = frame.dropna(subset=["date", "discharge_cfs", "baseflow_cfs"])
    if frame.empty:
        return pd.DataFrame()
    frame["water_year"] = frame["date"].map(lambda value: water_year(value.date()))
    annual_rows: list[dict[str, Any]] = []
    for wy, group in frame.groupby("water_year", sort=True):
        discharge_volume = float(group["discharge_cfs"].sum())
        baseflow_volume = float(group["baseflow_cfs"].sum())
        annual_rows.append(
            {
                "water_year": int(wy),
                "valid_days": int(len(group)),
                "mean_discharge_cfs": float(group["discharge_cfs"].mean()),
                "mean_baseflow_cfs": float(group["baseflow_cfs"].mean()),
                "baseflow_index": baseflow_volume / discharge_volume
                if discharge_volume
                else None,
                "baseflow_q90_cfs": float(group["baseflow_cfs"].quantile(0.10)),
                "baseflow_q95_cfs": float(group["baseflow_cfs"].quantile(0.05)),
                "method": str(group["baseflow_method"].iloc[0]),
            }
        )
    return pd.DataFrame(annual_rows)


def baseflow_statistics(baseflow_daily: pd.DataFrame) -> dict[str, Any]:
    """Return whole-period baseflow and ecological-screening summary values."""

    if baseflow_daily.empty:
        return {
            "method": "Lyne-Hollick recursive digital filter",
            "alpha": BASEFLOW_ALPHA,
            "passes": BASEFLOW_PASSES,
            "valid_days": 0,
            "baseflow_index": None,
            "baseflow_interpretation": "No valid daily data available for baseflow separation.",
        }
    frame = baseflow_daily.dropna(subset=["discharge_cfs", "baseflow_cfs"]).copy()
    discharge_total = float(frame["discharge_cfs"].sum())
    baseflow_total = float(frame["baseflow_cfs"].sum())
    return {
        "method": str(frame["baseflow_method"].iloc[0]),
        "alpha": float(frame["alpha"].iloc[0]),
        "passes": int(frame["passes"].iloc[0]),
        "valid_days": int(len(frame)),
        "baseflow_index": baseflow_total / discharge_total if discharge_total else None,
        "mean_discharge_cfs": float(frame["discharge_cfs"].mean()),
        "mean_baseflow_cfs": float(frame["baseflow_cfs"].mean()),
        "baseflow_q90_cfs": float(frame["baseflow_cfs"].quantile(0.10)),
        "baseflow_q95_cfs": float(frame["baseflow_cfs"].quantile(0.05)),
        "baseflow_interpretation": (
            "BFI and baseflow quantiles are screening indicators of delayed flow contribution; "
            "they are not site-specific ecological flow targets."
        ),
    }


def flow_duration_curve(
    daily: pd.DataFrame,
    exceedance_levels: Iterable[int] = FDC_EXCEEDANCE_LEVELS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the empirical daily-flow duration curve and selected quantiles.

    Exceedance probability is the percentage of observed days on which the
    discharge is equaled or exceeded. No date order is used in an FDC.
    """

    series = _daily_series(daily)
    if series.empty:
        return pd.DataFrame(), pd.DataFrame()
    values = np.sort(series.to_numpy(dtype=float))[::-1]
    n = len(values)
    curve = pd.DataFrame(
        {
            "exceedance_probability_pct": 100 * np.arange(1, n + 1) / (n + 1),
            "discharge_cfs": values,
            "n_valid_days": n,
            "method": "empirical daily flow-duration curve; plotting position rank/(n+1)",
        }
    )
    quantile_rows: list[dict[str, Any]] = []
    for level in sorted({int(value) for value in exceedance_levels if 0 < int(value) < 100}):
        discharge = float(np.quantile(values, 1 - level / 100))
        quantile_rows.append(
            {
                "exceedance_probability_pct": level,
                "nonexceedance_probability_pct": 100 - level,
                "discharge_cfs": discharge,
                "n_valid_days": n,
                "method": "empirical daily FDC quantile",
                "interpretation": f"Daily discharge equaled or exceeded {discharge:.2f} ft³/s on approximately {level}% of observed days.",
            }
        )
    return curve, pd.DataFrame(quantile_rows)


def summarize_water_management_baselines(
    annual: pd.DataFrame,
    exceedance_levels: Iterable[int] = MANAGEMENT_EXCEEDANCE_LEVELS,
) -> pd.DataFrame:
    """Estimate annual-runoff planning baselines at selected exceedance levels."""

    if annual.empty:
        return pd.DataFrame()
    value_frame = annual[
        annual["is_complete"].fillna(False)
        & annual["total_runoff_acre_ft"].notna()
        & annual["mean_discharge_cfs"].notna()
    ].copy()
    if value_frame.empty:
        return pd.DataFrame()
    runoff = value_frame["total_runoff_acre_ft"].to_numpy(dtype=float)
    means = value_frame["mean_discharge_cfs"].to_numpy(dtype=float)
    rows: list[dict[str, Any]] = []
    labels = {50: "normal", 75: "dry", 90: "very_dry", 95: "extremely_dry"}
    for level in sorted({int(value) for value in exceedance_levels if 0 < int(value) < 100}):
        fraction = 1 - level / 100
        rows.append(
            {
                "exceedance_probability_pct": level,
                "nonexceedance_probability_pct": 100 - level,
                "year_type": labels.get(level, f"P{level}"),
                "annual_runoff_acre_ft": float(np.quantile(runoff, fraction)),
                "annual_mean_discharge_cfs": float(np.quantile(means, fraction)),
                "n_complete_water_years": int(len(value_frame)),
                "method": "empirical quantile of complete Water-Year annual values",
                "interpretation": (
                    f"A P{level} planning baseline is exceeded in approximately {level}% of complete Water Years; "
                    "it is a planning statistic, not a guaranteed future supply."
                ),
            }
        )
    return pd.DataFrame(rows)


def summarize_annual_runoff_exceedance(annual: pd.DataFrame) -> pd.DataFrame:
    """Pair every complete Water Year runoff value with its empirical exceedance probability."""

    columns = [
        "water_year",
        "annual_runoff_acre_ft",
        "annual_mean_discharge_cfs",
        "rank_descending_runoff",
        "exceedance_probability_pct",
        "nonexceedance_probability_pct",
        "hydrologic_year_class",
        "n_complete_water_years",
        "method",
    ]
    if annual.empty or "total_runoff_acre_ft" not in annual:
        return pd.DataFrame(columns=columns)
    value_frame = annual[
        annual["is_complete"].fillna(False)
        & annual["total_runoff_acre_ft"].notna()
    ].copy()
    if value_frame.empty:
        return pd.DataFrame(columns=columns)
    value_frame["total_runoff_acre_ft"] = pd.to_numeric(
        value_frame["total_runoff_acre_ft"], errors="coerce"
    )
    value_frame = value_frame.dropna(subset=["total_runoff_acre_ft"])
    value_frame = value_frame.sort_values(
        ["total_runoff_acre_ft", "water_year"], ascending=[False, True]
    ).reset_index(drop=True)
    n = len(value_frame)
    value_frame["rank_descending_runoff"] = np.arange(1, n + 1)
    value_frame["exceedance_probability_pct"] = (
        100 * value_frame["rank_descending_runoff"] / (n + 1)
    )
    value_frame["nonexceedance_probability_pct"] = 100 - value_frame[
        "exceedance_probability_pct"
    ]
    value_frame["annual_runoff_acre_ft"] = value_frame["total_runoff_acre_ft"]
    value_frame["annual_mean_discharge_cfs"] = value_frame["mean_discharge_cfs"]
    value_frame["n_complete_water_years"] = n
    value_frame["method"] = (
        "Empirical annual-runoff exceedance; descending rank/(n+1) plotting position"
    )
    return value_frame[columns]


def summarize_low_flow_management(
    annual: pd.DataFrame,
    durations: Iterable[int] = LOW_FLOW_DURATIONS,
) -> pd.DataFrame:
    """Return descriptive annual k-day low-flow percentiles, without fitting kQ10."""

    if annual.empty or "is_complete" not in annual:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for duration in sorted({int(value) for value in durations if int(value) >= 1}):
        column = f"min_{duration}day_mean_cfs"
        if column not in annual:
            continue
        values = annual.loc[
            annual["is_complete"].fillna(False), column
        ].dropna()
        if values.empty:
            continue
        for nonexceedance in (5, 10, 25, 50):
            value = float(np.quantile(values, nonexceedance / 100))
            rows.append(
                {
                    "duration_days": duration,
                    "nonexceedance_probability_pct": nonexceedance,
                    "annual_min_mean_discharge_cfs": value,
                    "annual_min_7day_mean_cfs": value if duration == 7 else None,
                    "n_complete_water_years": int(len(values)),
                    "method": f"Empirical percentile of annual minimum {duration}-day mean discharge",
                    "interpretation": (
                        f"Descriptive {duration}-day low-flow screen; not a fitted {duration}Q10 "
                        "or ecological-flow standard."
                    ),
                }
            )
    return pd.DataFrame(rows)


def identify_high_flow_events(
    daily: pd.DataFrame,
    threshold_quantile: float = HIGH_FLOW_QUANTILE,
) -> tuple[pd.DataFrame, float | None]:
    """Identify consecutive daily exceedance episodes above a transparent threshold."""

    prepared = _with_water_year(_daily_values(daily)).dropna(subset=["discharge_cfs"])
    if prepared.empty:
        return pd.DataFrame(), None
    threshold = float(prepared["discharge_cfs"].quantile(threshold_quantile))
    prepared = prepared.sort_values("observed_date").reset_index(drop=True)
    above = prepared["discharge_cfs"] > threshold
    event_id: list[int | None] = []
    current_id = -1
    previous_date: date | None = None
    in_event = False
    for row, is_above in zip(prepared.itertuples(), above.tolist()):
        if not is_above:
            event_id.append(None)
            in_event = False
            previous_date = row.observed_date
            continue
        if (
            not in_event
            or previous_date is None
            or (row.observed_date - previous_date).days > 1
        ):
            current_id += 1
            in_event = True
        event_id.append(current_id)
        previous_date = row.observed_date
    prepared["event_id"] = event_id
    rows: list[dict[str, Any]] = []
    for event_id_value, group in prepared.dropna(subset=["event_id"]).groupby("event_id"):
        peak_index = group["discharge_cfs"].idxmax()
        rows.append(
            {
                "event_id": int(event_id_value),
                "water_year": int(group["water_year"].iloc[0]),
                "start_date": group["observed_date"].min(),
                "end_date": group["observed_date"].max(),
                "duration_days": int(len(group)),
                "peak_discharge_cfs": float(group["discharge_cfs"].max()),
                "peak_date": group.loc[peak_index, "observed_date"],
                "excess_volume_acre_ft": float(
                    (group["discharge_cfs"] - threshold).clip(lower=0).sum()
                    * ACRE_FEET_PER_CFS_DAY
                ),
                "threshold_cfs": threshold,
                "threshold_definition": f"daily discharge > full-record {threshold_quantile:.0%} quantile",
            }
        )
    return pd.DataFrame(rows), threshold


def summarize_high_flow_annual(events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate high-flow episodes by Water Year for annual comparison."""

    if events.empty:
        return pd.DataFrame(
            columns=[
                "water_year",
                "high_flow_event_count",
                "high_flow_days",
                "largest_high_flow_peak_cfs",
                "total_excess_volume_acre_ft",
            ]
        )
    return (
        events.groupby("water_year", as_index=False)
        .agg(
            high_flow_event_count=("event_id", "count"),
            high_flow_days=("duration_days", "sum"),
            largest_high_flow_peak_cfs=("peak_discharge_cfs", "max"),
            total_excess_volume_acre_ft=("excess_volume_acre_ft", "sum"),
        )
        .sort_values("water_year")
    )


def summarize_annual_peaks(peak: pd.DataFrame) -> pd.DataFrame:
    """Normalize provider annual peaks and retain historical qualifiers."""

    if peak.empty:
        return pd.DataFrame()
    if "observed_date_local" in peak:
        dates = pd.to_datetime(peak["observed_date_local"], errors="coerce").dt.date
    elif "observed_date" in peak:
        dates = pd.to_datetime(peak["observed_date"], errors="coerce").dt.date
    else:
        dates = pd.to_datetime(peak["observed_at_utc"], errors="coerce", utc=True).dt.date
    prepared = pd.DataFrame(
        {
            "observed_date": dates,
            "discharge_cfs": pd.to_numeric(peak.get("value"), errors="coerce"),
            "quality_code": peak.get(
                "quality_code", pd.Series(pd.NA, index=peak.index, dtype="string")
            ),
        }
    ).dropna(subset=["observed_date", "discharge_cfs"])
    prepared = prepared[prepared["discharge_cfs"] > 0]
    prepared["water_year"] = prepared["observed_date"].map(water_year)
    prepared["is_historical_peak"] = prepared["quality_code"].fillna("").astype(str).str.contains(
        "HISTORIC", case=False, na=False
    )
    if prepared.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for wy, group in prepared.groupby("water_year", sort=True):
        index = group["discharge_cfs"].idxmax()
        rows.append(
            {
                "water_year": int(wy),
                "annual_peak_discharge_cfs": float(group.loc[index, "discharge_cfs"]),
                "annual_peak_date": group.loc[index, "observed_date"],
                "quality_code": group.loc[index, "quality_code"],
                "is_historical_peak": bool(group.loc[index, "is_historical_peak"]),
                "is_complete": True,
                "source": "USGS annual peak measurements",
            }
        )
    return pd.DataFrame(rows)


def _fitted_return_period_years(
    discharge_cfs: float,
    fit_skew: float,
    fit_location: float,
    fit_scale: float,
) -> float | None:
    """Invert a fitted LP3 upper-tail curve at a supplied discharge."""

    if discharge_cfs <= 0 or fit_scale <= 0:
        return None
    exceedance = float(
        pearson3.sf(
            np.log10(discharge_cfs),
            fit_skew,
            loc=fit_location,
            scale=fit_scale,
        )
    )
    if not np.isfinite(exceedance) or exceedance <= 0:
        return None
    return float(1 / exceedance)


def flood_frequency_analysis(
    annual: pd.DataFrame,
    return_periods: Iterable[int] = FLOOD_RETURN_PERIODS,
    source: str = "daily_annual_maxima",
    systematic_only: bool = True,
    confidence_level: float = 0.95,
    bootstrap_replicates: int = 2000,
    random_seed: int = 20260828,
) -> pd.DataFrame:
    """Return systematic annual maxima and a transparent B17C-style fit.

    With no historical/censored information, Bulletin 17C's EMA input is a
    systematic annual-maximum record.  This implementation uses at-site
    moments of log10 flow and the Pearson III quantile function, which is the
    systematic-only LP3/EMA-equivalent curve.  Confidence limits are obtained
    by a seeded parametric bootstrap so the result is reproducible.  It is
    explicitly labelled as a screening implementation until it is cross-run
    against the official USGS PeakFQ R package.
    """

    if annual.empty:
        return pd.DataFrame()
    value_column = (
        "annual_peak_discharge_cfs"
        if "annual_peak_discharge_cfs" in annual
        else "max_daily_discharge_cfs"
    )
    complete = annual[
        annual["is_complete"].fillna(False)
        & annual[value_column].notna()
        & (annual[value_column] > 0)
    ].copy()
    if "is_historical_peak" in complete:
        historical_mask = complete["is_historical_peak"].fillna(False).astype(bool)
    else:
        historical_mask = complete.get(
            "quality_code", pd.Series("", index=complete.index)
        ).fillna("").astype(str).str.contains("HISTORIC", case=False, na=False)
    historical = complete[historical_mask].copy()
    systematic = complete[~historical_mask].copy() if systematic_only else complete.copy()
    values = systematic[value_column].astype(float).sort_values(ascending=False)
    n = len(values)
    if n == 0:
        return pd.DataFrame()
    excluded_count = int(len(historical)) if systematic_only else 0
    confidence_level = min(max(float(confidence_level), 0.5), 0.999)
    lower_quantile = (1 - confidence_level) / 2
    upper_quantile = 1 - lower_quantile
    rows: list[dict[str, Any]] = []
    for rank, (index, value) in enumerate(values.items(), start=1):
        rows.append(
            {
                "method": "empirical_ams_weibull_plotting_position",
                "return_period_years": (n + 1) / rank,
                "exceedance_probability": rank / (n + 1),
                "fitted_return_period_years": None,
                "fitted_exceedance_probability": None,
                "estimate_discharge_cfs": float(value),
                "lower_confidence_cfs": None,
                "upper_confidence_cfs": None,
                "water_year": int(systematic.loc[index, "water_year"]),
                "annual_peak_date": systematic.loc[index].get("annual_peak_date"),
                "quality_code": systematic.loc[index].get("quality_code"),
                "is_systematic": True,
                "n_complete_water_years": n,
                "n_systematic_years": n,
                "excluded_historic_count": excluded_count,
                "source": source,
                "warning": "Descriptive annual-maximum ranking; not a fitted design flood.",
            }
        )
    for index, row in historical.iterrows():
        if systematic_only:
            rows.append(
                {
                    "method": "excluded_historical_peak",
                    "return_period_years": None,
                    "exceedance_probability": None,
                    "fitted_return_period_years": None,
                    "fitted_exceedance_probability": None,
                    "estimate_discharge_cfs": float(row[value_column]),
                    "lower_confidence_cfs": None,
                    "upper_confidence_cfs": None,
                    "water_year": int(row["water_year"]),
                    "annual_peak_date": row.get("annual_peak_date"),
                    "quality_code": row.get("quality_code"),
                    "is_systematic": False,
                    "n_complete_water_years": n,
                    "n_systematic_years": n,
                    "excluded_historic_count": excluded_count,
                    "source": source,
                    "warning": "Excluded from systematic-only B17C fit because provider quality code contains HISTORIC.",
                }
            )
    warning = (
        "Systematic-only B17C-style LP3/EMA-equivalent screening: at-site skew only; "
        "no historical/censored record, regional skew, or low-outlier information supplied. "
        "95% limits use a seeded parametric bootstrap and should be cross-checked with PeakFQ."
    )
    if n >= 8:
        log_values = np.log10(values.to_numpy())
        try:
            location = float(log_values.mean())
            scale = float(log_values.std(ddof=1))
            fit_skew = float(skew(log_values, bias=False)) if scale > 0 else 0.0
            if not np.isfinite(fit_skew):
                fit_skew = 0.0

            periods = sorted({int(period) for period in return_periods if int(period) > 1})
            estimates: dict[int, float] = {}
            for period in periods:
                estimates[period] = 10 ** float(
                    pearson3.ppf(1 - 1 / period, fit_skew, loc=location, scale=scale)
                )

            bootstrap = np.full((max(int(bootstrap_replicates), 0), len(periods)), np.nan)
            rng = np.random.default_rng(random_seed)
            for replicate in range(len(bootstrap)):
                sample = pearson3.rvs(
                    fit_skew,
                    loc=location,
                    scale=scale,
                    size=n,
                    random_state=rng,
                )
                sample_scale = float(np.std(sample, ddof=1))
                if not np.isfinite(sample_scale) or sample_scale <= 0:
                    continue
                sample_skew = float(skew(sample, bias=False))
                if not np.isfinite(sample_skew):
                    sample_skew = 0.0
                sample_location = float(np.mean(sample))
                for column, period in enumerate(periods):
                    bootstrap[replicate, column] = 10 ** float(
                        pearson3.ppf(
                            1 - 1 / period,
                            sample_skew,
                            loc=sample_location,
                            scale=sample_scale,
                        )
                    )

            # Store the fitted-line inverse for every observed annual maximum.
            # This is deliberately separate from the empirical plotting
            # position: the two return periods answer different questions.
            for output_row in rows:
                if output_row["method"] == "empirical_ams_weibull_plotting_position":
                    fitted_period = _fitted_return_period_years(
                        float(output_row["estimate_discharge_cfs"]),
                        fit_skew,
                        location,
                        scale,
                    )
                    output_row["fitted_return_period_years"] = fitted_period
                    output_row["fitted_exceedance_probability"] = (
                        1 / fitted_period
                        if fitted_period is not None and fitted_period > 0
                        else None
                    )

            for column, period in enumerate(periods):
                valid_bootstrap = bootstrap[:, column]
                valid_bootstrap = valid_bootstrap[np.isfinite(valid_bootstrap)]
                lower = (
                    float(np.quantile(valid_bootstrap, lower_quantile))
                    if len(valid_bootstrap)
                    else None
                )
                upper = (
                    float(np.quantile(valid_bootstrap, upper_quantile))
                    if len(valid_bootstrap)
                    else None
                )
                rows.append(
                    {
                        "method": "b17c_systematic_ema_station_skew",
                        "return_period_years": period,
                        "exceedance_probability": 1 / period,
                        "fitted_return_period_years": None,
                        "fitted_exceedance_probability": None,
                        "estimate_discharge_cfs": estimates[period],
                        "lower_confidence_cfs": lower,
                        "upper_confidence_cfs": upper,
                        "water_year": None,
                        "annual_peak_date": None,
                        "quality_code": None,
                        "is_systematic": True,
                        "n_complete_water_years": n,
                        "n_systematic_years": n,
                        "excluded_historic_count": excluded_count,
                        "source": source,
                        "fit_log10_skew": fit_skew,
                        "fit_log10_location": location,
                        "fit_log10_scale": scale,
                        "confidence_level": confidence_level,
                        "ci_method": "seeded_parametric_bootstrap",
                        "warning": warning,
                    }
                )
        except (ValueError, FloatingPointError) as exc:
            rows.append(
                {
                    "method": "b17c_systematic_ema_station_skew_failed",
                    "return_period_years": None,
                    "exceedance_probability": None,
                    "fitted_return_period_years": None,
                    "fitted_exceedance_probability": None,
                    "estimate_discharge_cfs": None,
                    "lower_confidence_cfs": None,
                    "upper_confidence_cfs": None,
                    "water_year": None,
                    "annual_peak_date": None,
                    "quality_code": None,
                    "is_systematic": True,
                    "n_complete_water_years": n,
                    "n_systematic_years": n,
                    "excluded_historic_count": excluded_count,
                    "source": source,
                    "warning": f"B17C-style fit failed: {exc}",
                }
            )
    else:
        rows.append(
            {
                "method": "b17c_systematic_ema_station_skew_failed",
                "return_period_years": None,
                "exceedance_probability": None,
                "fitted_return_period_years": None,
                "fitted_exceedance_probability": None,
                "estimate_discharge_cfs": None,
                "lower_confidence_cfs": None,
                "upper_confidence_cfs": None,
                "water_year": None,
                "annual_peak_date": None,
                "quality_code": None,
                "is_systematic": True,
                "n_complete_water_years": n,
                "n_systematic_years": n,
                "excluded_historic_count": excluded_count,
                "source": source,
                "warning": "Fewer than 8 complete Water Years; B17C-style fit withheld.",
            }
        )
    return pd.DataFrame(rows)


def _correlation_row(
    analysis: str,
    comparison: str,
    x: pd.Series,
    y: pd.Series | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if y is None:
        y = x
    pair = pd.concat([pd.to_numeric(x, errors="coerce"), pd.to_numeric(y, errors="coerce")], axis=1)
    pair = pair.dropna()
    if len(pair) < 3:
        return {
            "analysis": analysis,
            "comparison": comparison,
            "n": int(len(pair)),
            "pearson_r": None,
            "spearman_r": None,
            "notes": notes or "Fewer than 3 paired observations.",
        }
    return {
        "analysis": analysis,
        "comparison": comparison,
        "n": int(len(pair)),
        "pearson_r": float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="pearson")),
        "spearman_r": float(pair.iloc[:, 0].corr(pair.iloc[:, 1], method="spearman")),
        "notes": notes,
    }


def summarize_correlations(
    daily: pd.DataFrame,
    field_pairs: pd.DataFrame | None = None,
    unit_values: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Summarize temporal persistence and stage-discharge associations."""

    prepared = _daily_values(daily)
    if prepared.empty:
        return pd.DataFrame()
    indexed = prepared.set_index(pd.to_datetime(prepared["observed_date"]))["discharge_cfs"]
    full = indexed.reindex(pd.date_range(indexed.index.min(), indexed.index.max(), freq="D"))
    rows = []
    for lag in (1, 7, 30):
        rows.append(
            _correlation_row(
                "daily_discharge_autocorrelation",
                f"lag_{lag}_calendar_days",
                full,
                full.shift(lag),
                "Missing dates are not imputed; pairs require both dates to be observed.",
            )
        )

    if unit_values is not None and not unit_values.empty:
        stage = _daily_values(
            unit_values.rename(columns={"value": "discharge_cfs"})
        ).rename(columns={"discharge_cfs": "gage_height_ft"})
        # _daily_values only uses the value column; make the stage frame
        # explicitly to avoid treating stage as discharge in downstream code.
        stage = pd.DataFrame(
            {
                "observed_date": pd.to_datetime(
                    unit_values["observed_date_local"], errors="coerce"
                ).dt.date,
                "gage_height_ft": pd.to_numeric(unit_values["value"], errors="coerce"),
            }
        ).groupby("observed_date", as_index=False)["gage_height_ft"].mean()
        paired = prepared.merge(stage, on="observed_date", how="inner").dropna()
        paired = paired[(paired["discharge_cfs"] > 0) & (paired["gage_height_ft"] > 0)]
        rows.append(
            _correlation_row(
                "stage_discharge_daily_pairs",
                "linear_scale",
                paired["gage_height_ft"],
                paired["discharge_cfs"],
                "Daily discharge paired with daily mean gage height from unit values.",
            )
        )
        rows.append(
            _correlation_row(
                "stage_discharge_daily_pairs",
                "log10_scale",
                np.log10(paired["gage_height_ft"]),
                np.log10(paired["discharge_cfs"]),
                "Log-transformed descriptive association; not a fitted rating curve.",
            )
        )
    if field_pairs is not None and not field_pairs.empty:
        valid = field_pairs.dropna(subset=["gage_height_ft", "discharge_cfs"])
        valid = valid[(valid["gage_height_ft"] > 0) & (valid["discharge_cfs"] > 0)]
        rows.append(
            _correlation_row(
                "stage_discharge_field_pairs",
                "linear_scale",
                valid["gage_height_ft"],
                valid["discharge_cfs"],
                "Independent field measurements retained as the ground-truth layer.",
            )
        )
        if "relative_error_pct" in valid:
            dates = pd.to_datetime(valid.get("discharge_time_utc"), errors="coerce", utc=True)
            ordinal_days = (dates - dates.min()).dt.total_seconds() / 86400
            rows.append(
                _correlation_row(
                    "field_published_discharge_error",
                    "relative_error_vs_time",
                    ordinal_days,
                    valid["relative_error_pct"],
                    "Screening trend only; a changing rating or method is not inferred automatically.",
                )
            )
    return pd.DataFrame(rows)


def screen_change_points(annual: pd.DataFrame, min_segment_years: int = 10) -> pd.DataFrame:
    """Find descriptive annual mean shifts that require source-record review."""

    if annual.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for metric in ("mean_discharge_cfs", "max_daily_discharge_cfs"):
        frame = annual[annual["is_complete"].fillna(False)][["water_year", metric]].dropna()
        if len(frame) < 2 * min_segment_years:
            continue
        values = frame[metric].to_numpy(dtype=float)
        candidates: list[dict[str, Any]] = []
        for split in range(min_segment_years, len(values) - min_segment_years + 1):
            before = values[:split]
            after = values[split:]
            pooled = np.sqrt((np.var(before, ddof=1) + np.var(after, ddof=1)) / 2)
            effect = (float(after.mean()) - float(before.mean())) / pooled if pooled else None
            if effect is None:
                continue
            candidates.append(
                {
                    "metric": metric,
                    "candidate_water_year": int(frame.iloc[split]["water_year"]),
                    "before_mean": float(before.mean()),
                    "after_mean": float(after.mean()),
                    "mean_ratio_after_to_before": float(after.mean() / before.mean())
                    if before.mean()
                    else None,
                    "standardized_mean_shift": float(effect),
                    "before_years": int(len(before)),
                    "after_years": int(len(after)),
                    "method": "maximum_absolute_annual_mean_shift_screen",
                    "interpretation": "Candidate only; inspect datum, rating, method, and engineering records.",
                }
            )
        rows.extend(sorted(candidates, key=lambda row: abs(row["standardized_mean_shift"]), reverse=True)[:5])
    return pd.DataFrame(rows)


def summarize_field_method_evidence(field_measurements: pd.DataFrame) -> pd.DataFrame:
    """Summarize available field-method/control evidence and its time span."""

    if field_measurements.empty:
        return pd.DataFrame()
    frame = field_measurements.copy()
    frame["measurement_time_utc"] = pd.to_datetime(
        frame.get("measurement_time_utc"), errors="coerce", utc=True
    )
    rows: list[dict[str, Any]] = []
    dimensions = (
        "observing_procedure",
        "measurement_rated",
        "control_condition",
        "approval_status",
        "measuring_agency",
    )
    for dimension in dimensions:
        if dimension not in frame:
            continue
        values = frame[dimension].fillna("<missing>").astype(str).str.strip()
        grouped = frame.assign(_value=values).groupby("_value", dropna=False)
        for value, group in grouped:
            rows.append(
                {
                    "evidence_dimension": dimension,
                    "evidence_value": value,
                    "record_count": int(len(group)),
                    "first_observed_at_utc": group["measurement_time_utc"].min(),
                    "last_observed_at_utc": group["measurement_time_utc"].max(),
                    "source": "USGS OGC field-measurements collection",
                    "interpretation": "Observed metadata evidence; absence of a value does not prove no historical change.",
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["evidence_dimension", "record_count"], ascending=[True, False]
    ).reset_index(drop=True)


def analyze_station_hydrology(station_root: Path) -> dict[str, Any]:
    """Calculate all hydrologic tables from the canonical station artifacts."""

    daily_path = station_root / "observations" / "daily_discharge.parquet"
    if not daily_path.exists():
        raise FileNotFoundError(f"Missing daily discharge: {daily_path}")
    daily = pd.read_parquet(daily_path)
    annual = summarize_annual_hydrology(daily)
    monthly = summarize_monthly_hydrology(daily, annual)
    baseflow_daily = separate_baseflow(daily)
    baseflow_annual = summarize_baseflow(baseflow_daily)
    baseflow_summary = baseflow_statistics(baseflow_daily)
    fdc, fdc_quantiles = flow_duration_curve(daily)
    events, threshold = identify_high_flow_events(daily)
    high_flow_annual = summarize_high_flow_annual(events)
    if not annual.empty and not high_flow_annual.empty:
        annual = annual.merge(high_flow_annual, on="water_year", how="left")
        for column in (
            "high_flow_event_count",
            "high_flow_days",
            "largest_high_flow_peak_cfs",
            "total_excess_volume_acre_ft",
        ):
            annual[column] = annual[column].fillna(0)
    peak_path = station_root / "observations" / "annual_peak_discharge.parquet"
    peak = pd.read_parquet(peak_path) if peak_path.exists() else None
    annual_peaks = summarize_annual_peaks(peak) if peak is not None else pd.DataFrame()
    frequency_source = "provider_annual_peak_measurements"
    if annual_peaks.empty:
        annual_peaks = annual[
            ["water_year", "max_daily_discharge_cfs", "date_of_max_daily_discharge", "is_complete"]
        ].rename(
            columns={
                "max_daily_discharge_cfs": "annual_peak_discharge_cfs",
                "date_of_max_daily_discharge": "annual_peak_date",
            }
        ).copy()
        frequency_source = "daily_annual_maxima_fallback"
    frequency = flood_frequency_analysis(annual_peaks, source=frequency_source)
    unit_path = station_root / "observations" / "unit_00065.parquet"
    unit = pd.read_parquet(unit_path) if unit_path.exists() else None
    field_path = station_root / "observations" / "field_stage_discharge_pairs.parquet"
    field = pd.read_parquet(field_path) if field_path.exists() else None
    correlations = summarize_correlations(daily, field, unit)
    fields_path = station_root / "observations" / "field_measurements.parquet"
    field_measurements = pd.read_parquet(fields_path) if fields_path.exists() else pd.DataFrame()
    method_evidence = summarize_field_method_evidence(field_measurements)
    change_points = screen_change_points(annual)
    management_baselines = summarize_water_management_baselines(annual)
    low_flow_management = summarize_low_flow_management(annual)
    annual_runoff_exceedance = summarize_annual_runoff_exceedance(annual)
    daily_coverage = _daily_gap_summary(daily)

    complete = annual[annual["is_complete"].fillna(False)] if not annual.empty else annual
    mean_values = complete["mean_discharge_cfs"].dropna() if not complete.empty else pd.Series(dtype=float)
    dry_threshold = float(mean_values.quantile(0.25)) if not mean_values.empty else None
    wet_threshold = float(mean_values.quantile(0.75)) if not mean_values.empty else None
    fitted_frequency = frequency[
        frequency["method"] == "b17c_systematic_ema_station_skew"
    ] if not frequency.empty else pd.DataFrame()
    empirical_frequency = frequency[
        frequency["method"] == "empirical_ams_weibull_plotting_position"
    ] if not frequency.empty else pd.DataFrame()
    historical_exclusions = frequency[
        frequency["method"] == "excluded_historical_peak"
    ] if not frequency.empty else pd.DataFrame()
    largest_event = (
        events.loc[events["peak_discharge_cfs"].idxmax()]
        if not events.empty
        else None
    )
    payload = {
        "station_id": station_root.name.removeprefix("USGS_"),
        "generated_at": datetime.now(timezone.utc),
        "daily_source": str(daily_path),
        "daily_coverage": daily_coverage,
        "water_year_definition": "October 1 through September 30, named by ending calendar year.",
        "complete_water_year_rule": f">={MIN_COMPLETE_WY_COMPLETENESS:.0%} valid daily discharge values",
        "water_year_count": int(len(annual)),
        "complete_water_year_count": int(len(complete)),
        "interannual_mean_discharge_cfs": float(mean_values.mean()) if not mean_values.empty else None,
        "interannual_std_discharge_cfs": float(mean_values.std(ddof=1)) if len(mean_values) > 1 else None,
        "interannual_cv": float(mean_values.std(ddof=1) / mean_values.mean())
        if len(mean_values) > 1 and mean_values.mean()
        else None,
        "year_class_rule": "Dry: complete-WY annual mean Q <= Q25; normal: Q25 < annual mean Q < Q75; wet: annual mean Q >= Q75. Thresholds are computed from complete Water Years only.",
        "dry_threshold_annual_mean_discharge_cfs": dry_threshold,
        "wet_threshold_annual_mean_discharge_cfs": wet_threshold,
        "high_flow_threshold_quantile": HIGH_FLOW_QUANTILE,
        "high_flow_threshold_cfs": threshold,
        "high_flow_event_definition": "Consecutive observed daily values above the full-record 95th percentile; missing dates break an event.",
        "flood_frequency_method": "Systematic-only B17C-style LP3/EMA-equivalent using at-site skew; 95% seeded parametric bootstrap limits",
        "flood_frequency_source": frequency_source,
        "flood_frequency_complete_years": int(len(annual_peaks)),
        "flood_frequency_systematic_years": int(
            fitted_frequency["n_systematic_years"].iloc[0]
        ) if not fitted_frequency.empty else int(
            empirical_frequency["n_systematic_years"].iloc[0]
        ) if not empirical_frequency.empty else 0,
        "flood_frequency_excluded_historic_peaks": int(len(historical_exclusions)),
        "flood_frequency_confidence_level": float(
            fitted_frequency["confidence_level"].iloc[0]
        ) if not fitted_frequency.empty else None,
        "flood_frequency_warning": (
            str(fitted_frequency["warning"].iloc[0])
            if not fitted_frequency.empty
            else "No B17C-style frequency fit was produced."
        ),
        "flood_frequency_max_observed_empirical_return_period_years": (
            float(empirical_frequency.loc[
                empirical_frequency["estimate_discharge_cfs"].idxmax(),
                "return_period_years",
            ])
            if not empirical_frequency.empty
            else None
        ),
        "flood_frequency_max_observed_fitted_return_period_years": (
            float(empirical_frequency.loc[
                empirical_frequency["estimate_discharge_cfs"].idxmax(),
                "fitted_return_period_years",
            ])
            if not empirical_frequency.empty
            and pd.notna(empirical_frequency["fitted_return_period_years"].max())
            else None
        ),
        "high_flow_event_count": int(len(events)),
        "high_flow_total_days": int(events["duration_days"].sum()) if not events.empty else 0,
        "high_flow_largest_event_peak_cfs": (
            float(largest_event["peak_discharge_cfs"]) if largest_event is not None else None
        ),
        "high_flow_largest_event_date": (
            largest_event["peak_date"] if largest_event is not None else None
        ),
        "high_flow_interpretation": "Events are consecutive observed daily values above the full-record 95th percentile; they are screening indicators, not regulatory flood peaks.",
        "change_point_method": "Top five annual mean-shift candidates per metric, requiring ten complete Water Years on each side.",
        "method_evidence_source": "USGS OGC field-measurements collection retained locally.",
        "method_evidence_interpretation": "Field-method, control, approval, and agency fields are observed evidence; missing history or a candidate shift does not establish causation.",
        "correlation_interpretation": "Pearson and Spearman values describe association only; they do not prove causation or replace a rating-curve analysis.",
        "correlation_result_count": int(len(correlations)),
        "correlation_selection": [
            {
                "comparison": "lag_1_calendar_days, lag_7_calendar_days, lag_30_calendar_days",
                "purpose": "Measure short-, weekly-, and monthly-scale persistence in the daily discharge series.",
                "selection_rule": "Fixed hydrologic lags chosen to distinguish persistence from seasonal smoothing; missing dates are not imputed.",
            },
            {
                "comparison": "stage_discharge_daily_pairs, linear_scale and log10_scale",
                "purpose": "Check whether paired daily stage and discharge co-vary and whether the relationship is multiplicative.",
                "selection_rule": "Use all positive same-date daily discharge and daily mean stage pairs available locally; field pairs remain a separate ground-truth layer.",
            },
            {
                "comparison": "field_published_discharge_error, relative_error_vs_time",
                "purpose": "Screen whether the difference between field-measured and provider-published discharge changes over time.",
                "selection_rule": "Use only field measurements with a published relative-error value and a valid measurement date.",
            },
        ],
        "baseflow": baseflow_summary,
        "baseflow_annual_count": int(len(baseflow_annual)),
        "flow_duration_curve": {
            "definition": "Percentage of observed daily values equaled or exceeded; date order is ignored.",
            "quantiles": fdc_quantiles.to_dict(orient="records"),
        },
        "water_management_baselines": management_baselines.to_dict(orient="records"),
        "annual_runoff_exceedance_count": int(len(annual_runoff_exceedance)),
        "annual_runoff_exceedance_method": (
            "Complete Water-Year annual runoff sorted descending; exceedance probability is rank/(n+1)."
        ),
        "low_flow_management": low_flow_management.to_dict(orient="records"),
        "low_flow_management_durations_days": sorted(
            low_flow_management["duration_days"].dropna().astype(int).unique().tolist()
        ) if not low_flow_management.empty else [],
        "water_management_interpretation": "P50/P75/P90/P95 annual-runoff values are empirical planning baselines from complete Water Years. They are not guaranteed future supply, ecological-flow standards, or fitted low-flow recurrence statistics.",
    }
    if peak is not None and not peak.empty:
        payload["annual_peak_observed_start"] = str(
            pd.to_datetime(peak["observed_date_local"], errors="coerce").min().date()
        )
        payload["annual_peak_observed_end"] = str(
            pd.to_datetime(peak["observed_date_local"], errors="coerce").max().date()
        )
        payload["annual_peak_quality_code_counts"] = (
            peak.get("quality_code", pd.Series(dtype="string"))
            .fillna("<none>")
            .astype(str)
            .value_counts()
            .to_dict()
        )
    return {
        "annual": annual,
        "monthly": monthly,
        "high_flow_events": events,
        "high_flow_annual": high_flow_annual,
        "annual_peaks": annual_peaks,
        "flood_frequency": frequency,
        "correlations": correlations,
        "method_evidence": method_evidence,
        "change_points": change_points,
        "baseflow_daily": baseflow_daily,
        "baseflow_annual": baseflow_annual,
        "baseflow_summary": baseflow_summary,
        "flow_duration_curve": fdc,
        "flow_duration_quantiles": fdc_quantiles,
        "management_baselines": management_baselines,
        "low_flow_management": low_flow_management,
        "annual_runoff_exceedance": annual_runoff_exceedance,
        "summary": payload,
    }


def write_station_hydrology(station_root: Path) -> Path:
    """Persist canonical hydrologic tables under the station data package."""

    result = analyze_station_hydrology(station_root)
    output = station_root / "hydrology"
    output.mkdir(parents=True, exist_ok=True)
    names = {
        "annual": "annual_hydrology.csv",
        "monthly": "monthly_hydrology.csv",
        "high_flow_events": "high_flow_events.csv",
        "high_flow_annual": "high_flow_annual.csv",
        "annual_peaks": "annual_peak_summary.csv",
        "flood_frequency": "flood_frequency.csv",
        "correlations": "correlation_summary.csv",
        "method_evidence": "method_evidence_summary.csv",
        "change_points": "change_point_candidates.csv",
        "baseflow_daily": "baseflow_daily.parquet",
        "baseflow_annual": "baseflow_annual.csv",
        "flow_duration_curve": "flow_duration_curve.csv",
        "flow_duration_quantiles": "flow_duration_quantiles.csv",
        "management_baselines": "water_management_baselines.csv",
        "low_flow_management": "low_flow_management.csv",
        "annual_runoff_exceedance": "annual_runoff_exceedance.csv",
    }
    for key, filename in names.items():
        if filename.endswith(".parquet"):
            result[key].to_parquet(output / filename, index=False)
        else:
            result[key].to_csv(output / filename, index=False)
    write_json(output / "baseflow_summary.json", result["baseflow_summary"])
    write_json(output / "hydrology_summary.json", result["summary"])
    from .narrative import write_hydrology_interpretation

    write_hydrology_interpretation(station_root, result)
    return output
