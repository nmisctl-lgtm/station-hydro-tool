import pandas as pd
import pytest

from station_hydro.frequency_backends import (
    AquaScopeUnavailable,
    annual_peak_series,
    aquascope_status,
    run_aquascope_lp3,
)


def test_annual_peak_series_defaults_to_systematic_rows() -> None:
    annual = pd.DataFrame(
        {
            "annual_peak_date": pd.to_datetime(["2020-05-01", "2021-05-01"]),
            "annual_peak_discharge_cfs": [100.0, 200.0],
            "is_complete": [True, True],
            "quality_code": ["", "HISTORIC"],
        }
    )
    series = annual_peak_series(annual)
    assert series.tolist() == [100.0]
    assert series.index[0] == pd.Timestamp("2020-05-01")


def test_aquascope_adapter_reports_optional_dependency_boundary() -> None:
    status = aquascope_status()
    assert status.name == "AquaScope"
    annual = pd.DataFrame(
        {
            "annual_peak_date": pd.date_range("2010-01-01", periods=4, freq="YE"),
            "annual_peak_discharge_cfs": [100.0, 110.0, 120.0, 130.0],
            "is_complete": [True] * 4,
        }
    )
    if not status.available:
        with pytest.raises(AquaScopeUnavailable):
            run_aquascope_lp3(annual)
