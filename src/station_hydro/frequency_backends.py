"""Optional flood-frequency backends.

The core station workflow must remain usable without optional packages.  This
module therefore keeps AquaScope behind a lazy adapter: importing
``station_hydro`` never imports AquaScope, and a missing optional dependency
produces an actionable error instead of an empty or silently substituted
result.

The adapter is deliberately not the default analysis path yet.  AquaScope's
LP3 result and the station package's systematic-only B17C-style result need a
cross-check against a fixed test fixture and an official PeakFQ reference
before they can be treated as interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


DEFAULT_RETURN_PERIODS = (2, 5, 10, 25, 50, 100)


class AquaScopeUnavailable(RuntimeError):
    """Raised when an AquaScope-backed calculation was requested but unavailable."""


class AquaScopeResultError(RuntimeError):
    """Raised when an installed AquaScope version returns an unknown result shape."""


@dataclass(frozen=True)
class BackendStatus:
    """Availability and provenance information for an optional backend."""

    name: str
    available: bool
    version: str | None
    message: str


def aquascope_status() -> BackendStatus:
    """Return AquaScope availability without importing it into the core path."""

    try:
        package_version = version("aquascope")
    except PackageNotFoundError:
        return BackendStatus(
            name="AquaScope",
            available=False,
            version=None,
            message="Optional package is not installed; install it separately to enable the adapter.",
        )
    try:
        import_module("aquascope.api")
    except Exception as exc:  # pragma: no cover - depends on optional environment
        return BackendStatus(
            name="AquaScope",
            available=False,
            version=package_version,
            message=f"Package {package_version} is installed but aquascope.api could not load: {exc}",
        )
    return BackendStatus(
        name="AquaScope",
        available=True,
        version=package_version,
        message="AquaScope API is importable; cross-validation is still required before default use.",
    )


def _result_field(result: Any, *names: str) -> Any:
    if isinstance(result, Mapping):
        for name in names:
            if name in result:
                return result[name]
        return None
    for name in names:
        if hasattr(result, name):
            return getattr(result, name)
    return None


def _as_period_map(value: Any, periods: list[int]) -> dict[int, float]:
    """Normalize AquaScope's dict/list/Series estimate forms."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        result: dict[int, float] = {}
        for key, item in value.items():
            try:
                result[int(key)] = float(item)
            except (TypeError, ValueError):
                continue
        return result
    if isinstance(value, pd.Series):
        return _as_period_map(value.to_dict(), periods)
    try:
        values = list(value)
    except TypeError:
        return {}
    result = {}
    for period, item in zip(periods, values):
        try:
            result[int(period)] = float(item)
        except (TypeError, ValueError):
            continue
    return result


def _as_interval_map(value: Any, periods: list[int]) -> dict[int, tuple[float, float]]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        result: dict[int, tuple[float, float]] = {}
        for key, item in value.items():
            try:
                lower, upper = item
                result[int(key)] = (float(lower), float(upper))
            except (TypeError, ValueError):
                continue
        return result
    try:
        values = list(value)
    except TypeError:
        return {}
    result = {}
    for period, item in zip(periods, values):
        try:
            lower, upper = item
            result[int(period)] = (float(lower), float(upper))
        except (TypeError, ValueError):
            continue
    return result


def annual_peak_series(
    annual: pd.DataFrame,
    *,
    include_historical: bool = False,
) -> pd.Series:
    """Convert annual-peak rows to AquaScope's required DatetimeIndex Series."""

    value_column = (
        "annual_peak_discharge_cfs"
        if "annual_peak_discharge_cfs" in annual
        else "max_daily_discharge_cfs"
    )
    if annual.empty or value_column not in annual or "annual_peak_date" not in annual:
        return pd.Series(dtype="float64", name="discharge_cfs")
    frame = annual.copy()
    dates = pd.to_datetime(frame.get("annual_peak_date"), errors="coerce")
    frame["_date"] = dates
    frame["_value"] = pd.to_numeric(frame[value_column], errors="coerce")
    valid = frame["_date"].notna() & frame["_value"].notna() & (frame["_value"] > 0)
    if "is_complete" in frame:
        valid &= frame["is_complete"].fillna(False).astype(bool)
    if not include_historical:
        historical = frame.get(
            "is_historical_peak",
            frame.get("quality_code", pd.Series("", index=frame.index))
            .fillna("")
            .astype(str)
            .str.contains("HISTORIC", case=False, na=False),
        )
        valid &= ~pd.Series(historical, index=frame.index).fillna(False).astype(bool)
    frame = frame.loc[valid, ["_date", "_value"]].drop_duplicates("_date")
    return pd.Series(
        frame["_value"].to_numpy(dtype=float),
        index=pd.DatetimeIndex(frame["_date"]),
        name="discharge_cfs",
    ).sort_index()


def run_aquascope_lp3(
    annual: pd.DataFrame,
    return_periods: Iterable[int] = DEFAULT_RETURN_PERIODS,
    *,
    confidence_level: float = 0.95,
    regional_skew: float | None = None,
    include_historical: bool = False,
) -> pd.DataFrame:
    """Run AquaScope LP3 and normalize its result to station-tool columns.

    AquaScope currently documents confidence intervals for its GEV convenience
    path, not necessarily for LP3.  Missing LP3 intervals are therefore kept
    as nulls rather than being invented by this adapter.
    """

    status = aquascope_status()
    if not status.available:
        raise AquaScopeUnavailable(status.message)
    discharge = annual_peak_series(annual, include_historical=include_historical)
    if len(discharge) < 3:
        raise ValueError("AquaScope LP3 requires at least three valid annual peaks")
    periods = sorted({int(period) for period in return_periods if int(period) > 1})
    if not periods:
        raise ValueError("return_periods must contain at least one period greater than one")
    api = import_module("aquascope.api")
    kwargs: dict[str, Any] = {
        "method": "lp3",
        "return_periods": periods,
    }
    if regional_skew is not None:
        kwargs["regional_skew"] = regional_skew
    try:
        result = api.flood_analysis(discharge, **kwargs)
    except Exception as exc:
        raise AquaScopeResultError(f"AquaScope LP3 calculation failed: {exc}") from exc

    estimates = _as_period_map(
        _result_field(result, "return_periods", "estimates", "quantiles"), periods
    )
    if not estimates:
        raise AquaScopeResultError(
            "AquaScope LP3 returned no recognizable return-period estimates; "
            "inspect the installed FloodFreqResult schema before updating the adapter."
        )
    intervals = _as_interval_map(
        _result_field(result, "confidence_intervals", "confidence_intervals_cfs"), periods
    )
    rows = []
    for period in periods:
        if period not in estimates:
            continue
        lower, upper = intervals.get(period, (None, None))
        rows.append(
            {
                "backend": "AquaScope",
                "method": "aquascope_lp3",
                "return_period_years": period,
                "estimate_discharge_cfs": estimates[period],
                "lower_confidence_cfs": lower,
                "upper_confidence_cfs": upper,
                "confidence_level": confidence_level if lower is not None else None,
                "n_complete_water_years": len(discharge),
                "source": "AquaScope flood_analysis(method='lp3')",
                "warning": (
                    "Optional backend output; compare with station-tool screening fit "
                    "and official PeakFQ before design use."
                ),
            }
        )
    return pd.DataFrame(rows)


__all__ = [
    "AquaScopeResultError",
    "AquaScopeUnavailable",
    "BackendStatus",
    "annual_peak_series",
    "aquascope_status",
    "run_aquascope_lp3",
]
