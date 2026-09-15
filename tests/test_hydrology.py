import numpy as np
import pandas as pd

from station_hydro.hydrology import (
    flow_duration_curve,
    flood_frequency_analysis,
    identify_high_flow_events,
    separate_baseflow,
    screen_change_points,
    summarize_annual_hydrology,
    _daily_gap_summary,
    summarize_annual_runoff_exceedance,
    summarize_low_flow_management,
    summarize_water_management_baselines,
    summarize_correlations,
    summarize_high_flow_annual,
    summarize_monthly_hydrology,
)


def synthetic_daily(years: int = 12) -> pd.DataFrame:
    dates = pd.date_range("2010-10-01", periods=years * 365 + 3, freq="D")
    values = 100 + 20 * np.sin(np.arange(len(dates)) / 30)
    return pd.DataFrame(
        {
            "observed_date_local": dates.date,
            "value": values,
        }
    )


def test_daily_gap_summary_reports_missing_calendar_runs_without_imputation() -> None:
    daily = pd.DataFrame(
        {
            "observed_date_local": pd.to_datetime(
                ["2020-01-01", "2020-01-02", "2020-01-10"]
            ),
            "value": [1.0, 2.0, 3.0],
        }
    )

    result = _daily_gap_summary(daily)

    assert result["observed_day_count"] == 3
    assert result["calendar_span_days"] == 10
    assert result["missing_day_count"] == 7
    assert result["long_gaps"] == [
        {"start_date": "2020-01-03", "end_date": "2020-01-09", "missing_days": 7}
    ]


def test_annual_and_monthly_statistics_use_water_years() -> None:
    daily = synthetic_daily()
    annual = summarize_annual_hydrology(daily)
    monthly = summarize_monthly_hydrology(daily, annual)

    assert annual.iloc[0]["water_year"] == 2011
    assert annual.iloc[0]["is_complete"]
    assert len(monthly) == 12
    assert set(monthly["month_label"]) == {
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
    }
    assert monthly["normalized_mean_discharge"].notna().all()


def test_high_flow_events_and_frequency_outputs_are_explicit() -> None:
    daily = synthetic_daily()
    daily.loc[100:102, "value"] = 500
    events, threshold = identify_high_flow_events(daily, threshold_quantile=0.95)
    assert threshold is not None
    assert len(events) >= 1
    injected = events[events["peak_discharge_cfs"] == 500]
    assert len(injected) == 1
    assert injected.iloc[0]["duration_days"] == 3
    annual_events = summarize_high_flow_annual(events)
    assert annual_events["high_flow_event_count"].sum() == len(events)

    annual = summarize_annual_hydrology(daily)
    frequency = flood_frequency_analysis(annual)
    assert "empirical_ams_weibull_plotting_position" in set(frequency["method"])
    assert "b17c_systematic_ema_station_skew" in set(frequency["method"])
    fitted = frequency[frequency["method"] == "b17c_systematic_ema_station_skew"]
    assert fitted["lower_confidence_cfs"].notna().all()
    assert fitted["upper_confidence_cfs"].notna().all()
    assert (fitted["upper_confidence_cfs"] >= fitted["estimate_discharge_cfs"]).all()


def test_empirical_maximum_return_period_uses_systematic_sample_size() -> None:
    annual = pd.DataFrame(
        {
            "water_year": range(1912, 2007),
            "annual_peak_discharge_cfs": [25_000.0] + [1_000.0 + i for i in range(94)],
            "annual_peak_date": [pd.Timestamp("1911-10-05")] + list(
                pd.date_range("1912-05-01", periods=94, freq="YE")
            ),
            "is_complete": [True] * 95,
            "is_historical_peak": [False] * 95,
        }
    )
    result = flood_frequency_analysis(annual, bootstrap_replicates=20)
    maximum = result[
        result["method"] == "empirical_ams_weibull_plotting_position"
    ].iloc[0]
    assert maximum["return_period_years"] == 96.0
    assert maximum["annual_peak_date"] == pd.Timestamp("1911-10-05")
    assert maximum["fitted_return_period_years"] > maximum["return_period_years"]


def test_correlation_and_change_point_results_are_screening_only() -> None:
    daily = synthetic_daily()
    correlations = summarize_correlations(daily)
    assert set(correlations["comparison"]) >= {
        "lag_1_calendar_days",
        "lag_7_calendar_days",
        "lag_30_calendar_days",
    }
    annual = summarize_annual_hydrology(daily)
    candidates = screen_change_points(annual)
    assert candidates.empty  # only twelve years: fewer than two 10-year segments by default data rule


def test_baseflow_fdc_and_management_baselines_are_explicit() -> None:
    daily = synthetic_daily(years=12)
    baseflow = separate_baseflow(daily)
    assert len(baseflow) == len(daily)
    valid_baseflow = baseflow.dropna(subset=["discharge_cfs", "baseflow_cfs"])
    assert (valid_baseflow["baseflow_cfs"] >= 0).all()
    assert (valid_baseflow["baseflow_cfs"] <= valid_baseflow["discharge_cfs"]).all()

    curve, quantiles = flow_duration_curve(daily, exceedance_levels=(50, 90, 95))
    assert len(curve) == len(valid_baseflow)
    assert quantiles["exceedance_probability_pct"].tolist() == [50, 90, 95]
    assert quantiles["discharge_cfs"].notna().all()

    annual = summarize_annual_hydrology(daily)
    baselines = summarize_water_management_baselines(annual)
    assert baselines["exceedance_probability_pct"].tolist() == [50, 75, 90, 95]
    runoff = summarize_annual_runoff_exceedance(annual)
    assert len(runoff) == int(annual["is_complete"].sum())
    assert runoff["exceedance_probability_pct"].is_monotonic_increasing
    assert runoff["water_year"].notna().all()
    low_flow = summarize_low_flow_management(annual)
    assert set(low_flow["duration_days"]) == {7, 14, 30}
    assert all(
        low_flow.loc[low_flow["duration_days"] == duration, "nonexceedance_probability_pct"].tolist()
        == [5, 10, 25, 50]
        for duration in (7, 14, 30)
    )
