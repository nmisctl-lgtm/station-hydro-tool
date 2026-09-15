from datetime import date, datetime, timezone
import json

import pandas as pd

from station_hydro.discovery import build_available_data, parse_rdb_text
from station_hydro.retrieval import (
    _parse_daily_discharge,
    _parse_annual_peak,
    _parse_instantaneous_values,
    build_download_plan,
    fetch_annual_peak,
)
from station_hydro.models import AvailableDataRecord, StationRequest


DAILY_RDB = """# comment
agency_cd\tsite_no\tdatetime\t19544_00060_00003\t19544_00060_00003_cd
5s\t15s\t20d\t14n\t10s
USGS\t09342500\t2026-08-25\t23.6\tP
USGS\t09342500\t2026-08-26\t15.3\t
"""


IV_JSON = {
    "value": {
        "timeSeries": [
            {
                "sourceInfo": {"siteCode": [{"value": "09342500"}]},
                "variable": {
                    "variableCode": [{"value": "00065"}],
                    "unit": {"unitCode": "ft"},
                    "variableDescription": "Gage height, feet",
                },
                "values": [
                    {
                        "value": [
                            {
                                "value": "3.66",
                                "qualifiers": ["P"],
                                "dateTime": "2026-08-26T18:00:00.000-06:00",
                            }
                        ]
                    }
                ],
            }
        ]
    }
}

PEAK_RDB = """# comment
agency_cd\tsite_no\tpeak_dt\tpeak_tm\tpeak_va\tpeak_cd\tgage_ht
5s\t15s\t20d\t4s\t12n\t4s\t8n
USGS\t09342500\t2020-05-15\t1200\t2100\t1\t6.2
USGS\t09342500\t2021-05-11\t0830\t1800\t\t5.8
"""


def record_from_rdb(text: str):
    return build_available_data(parse_rdb_text(text))[0]


def test_parse_daily_preserves_quality_code() -> None:
    record = record_from_rdb(
        """agency_cd\tsite_no\tdata_type_cd\tparm_cd\tstat_cd\tbegin_date\tend_date
5s\t15s\t2s\t5s\t5s\t20d\t20d
USGS\t09342500\tdv\t00060\t00003\t2026-08-25\t2026-08-26
"""
    )
    frame = _parse_daily_discharge(
        DAILY_RDB,
        record,
        "https://example.test/dv",
        "MST",
        datetime.now(timezone.utc),
    )
    assert frame["value"].tolist() == [23.6, 15.3]
    assert frame["quality_code"].iloc[0] == "P"
    assert frame["quality_code"].iloc[1] is None or str(frame["quality_code"].iloc[1]) == "<NA>"


def test_parse_unit_values_preserves_qualifiers() -> None:
    record = record_from_rdb(
        """agency_cd\tsite_no\tdata_type_cd\tparm_cd\tbegin_date\tend_date
5s\t15s\t2s\t5s\t20d\t20d
USGS\t09342500\tuv\t00065\t2026-08-26\t2026-08-26
"""
    )
    frame = _parse_instantaneous_values(
        IV_JSON,
        record,
        "https://example.test/iv",
        "MST",
        datetime.now(timezone.utc),
    )
    assert len(frame) == 1
    assert frame["value"].iloc[0] == 3.66
    assert frame["quality_code"].iloc[0] == "P"


def test_parse_annual_peak_preserves_peak_quality_code() -> None:
    record = record_from_rdb(
        """agency_cd\tsite_no\tdata_type_cd\tbegin_date\tend_date
5s\t15s\t2s\t20d\t20d
USGS\t09342500\tpk\t2020-05-15\t2021-05-11
"""
    )
    frame = _parse_annual_peak(
        PEAK_RDB,
        record,
        "https://example.test/peak",
        "MST",
        datetime.now(timezone.utc),
    )
    assert frame["value"].tolist() == [2100.0, 1800.0]
    assert frame["frequency"].tolist() == ["annual_peak", "annual_peak"]
    assert frame["quality_code"].iloc[0] == "1"


def test_fetch_annual_peak_keeps_provider_record_beyond_catalog_end(monkeypatch, tmp_path) -> None:
    payload = {
        "features": [
            {
                "properties": {
                    "monitoring_location_id": "USGS-09342500",
                    "parameter_code": "00060",
                    "unit_of_measure": "ft^3/s",
                    "value": "4840",
                    "time": "2023-05-20",
                    "water_year": 2023,
                    "qualifier": ["UNKNOWNREGULATION"],
                }
            }
        ]
    }

    def fake_get_raw(self, url, params, path, reuse_existing):
        return json.dumps(payload), {"path": str(path), "status_code": 200}

    monkeypatch.setattr("station_hydro.retrieval.USGSProvider._get_raw", fake_get_raw)
    data_root = tmp_path / "stations"
    station_root = data_root / "USGS_09342500"
    station_root.mkdir(parents=True)
    record = AvailableDataRecord(
        series_id="USGS:09342500:pk:none:none:0",
        variable="Annual peak discharge",
        data_type="Annual peak measurements",
        provider_data_type="pk",
        frequency="annual_peak",
        declared_start=date(2020, 6, 7),
        declared_end=date(2023, 5, 19),
    )

    output = fetch_annual_peak(
        StationRequest("09342500"),
        record,
        data_root,
        station_timezone="MST",
    )

    frame = pd.read_parquet(output)
    assert frame["observed_date_local"].tolist() == [date(2023, 5, 20)]
    assert frame["quality_code"].iloc[0] == "UNKNOWNREGULATION"


def test_download_plan_keeps_continuous_data_metadata_only_by_default() -> None:
    records = [
        record_from_rdb(
            """agency_cd\tsite_no\tdata_type_cd\tparm_cd\tstat_cd\tts_id\tbegin_date\tend_date\tcount_nu
5s\t15s\t2s\t5s\t5s\t5n\t20d\t20d\t5n
USGS\t09342500\tdv\t00060\t00003\t1\t1935-10-01\t2026-08-27\t33204
"""
        ),
        record_from_rdb(
            """agency_cd\tsite_no\tdata_type_cd\tparm_cd\tstat_cd\tts_id\tbegin_date\tend_date\tcount_nu
5s\t15s\t2s\t5s\t5s\t5n\t20d\t20d\t5n
USGS\t09342500\tuv\t00065\t\t2\t2020-09-17\t2026-08-28\t2171
"""
        ),
        record_from_rdb(
            """agency_cd\tsite_no\tdata_type_cd\tparm_cd\tstat_cd\tts_id\tbegin_date\tend_date\tcount_nu
5s\t15s\t2s\t5s\t5s\t5n\t20d\t20d\t5n
USGS\t09342500\tuv\t00060\t\t3\t1987-05-22\t2026-08-28\t14343
"""
        ),
    ]
    plan = build_download_plan(records, latest_date=date(2026, 8, 28))
    unit_stage = next(item for item in plan if item.parameter_code == "00065")
    unit_discharge = next(
        item
        for item in plan
        if item.parameter_code == "00060" and item.provider_data_type == "uv"
    )
    assert unit_stage.action == "defer"
    assert unit_stage.start == date(2020, 9, 17)
    assert unit_discharge.action == "defer"
    assert unit_discharge.start == date(1987, 5, 22)
    assert unit_discharge.end == date(2026, 8, 28)
    opt_in_plan = build_download_plan(
        records,
        latest_date=date(2026, 8, 28),
        include_continuous=True,
    )
    opt_in_discharge = next(
        item
        for item in opt_in_plan
        if item.parameter_code == "00060" and item.provider_data_type == "uv"
    )
    assert opt_in_discharge.action == "download"
    opt_in_stage = next(item for item in opt_in_plan if item.parameter_code == "00065")
    assert opt_in_stage.action == "download"
    assert opt_in_stage.start == date(2020, 9, 17)
    assert opt_in_stage.end == date(2026, 8, 28)


def test_download_plan_selects_annual_peak_for_flood_frequency() -> None:
    record = record_from_rdb(
        """agency_cd\tsite_no\tdata_type_cd\tbegin_date\tend_date
5s\t15s\t2s\t20d\t20d
USGS\t09342500\tpk\t1911-07-02\t2025-05-14
"""
    )
    item = next(item for item in build_download_plan([record]) if item.provider_data_type == "pk")
    assert item.action == "download"
    assert item.reason.startswith("provider annual peak")
