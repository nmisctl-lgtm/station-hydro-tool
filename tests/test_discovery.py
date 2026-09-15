from datetime import date

from station_hydro.discovery import build_available_data, parse_rdb_text


RDB = """# comment
agency_cd\tsite_no\tstation_nm\tdata_type_cd\tparm_cd\tstat_cd\tts_id\tbegin_date\tend_date\tcount_nu
5s\t15s\t50s\t2s\t5s\t5s\t5n\t20d\t20d\t5n
USGS\t09342500\tSAN JUAN RIVER\tdv\t00060\t00003\t19544\t1935-10-01\t2026-08-27\t33204
USGS\t09342500\tSAN JUAN RIVER\tuv\t00065\t\t279672\t2020-09-17\t2026-08-28\t2171
"""


def test_parse_rdb_text_skips_comments_and_type_row() -> None:
    frame = parse_rdb_text(RDB)
    assert frame.shape == (2, 10)
    assert frame.iloc[0]["parm_cd"] == "00060"


def test_build_available_data_preserves_coverage_and_roles() -> None:
    records = build_available_data(
        parse_rdb_text(RDB),
        {
            "00060": {"parameter_name": "Discharge", "unit_of_measure": "ft3/s"},
            "00065": {"parameter_name": "Gage height", "unit_of_measure": "ft"},
        },
    )
    assert records[0].variable == "Discharge"
    assert records[0].unit == "ft3/s"
    assert records[0].analysis_role == "core"
    assert records[0].declared_start == date(1935, 10, 1)
    assert records[1].analysis_role == "core"
    assert records[1].provider_count == 2171
    assert records[0].frequency == "daily"
    assert records[1].frequency == "unit"


def test_peak_catalog_row_is_normalized_as_annual_peak_discharge() -> None:
    records = build_available_data(
        parse_rdb_text(
            """agency_cd\tsite_no\tdata_type_cd\tparm_cd\tbegin_date\tend_date\n
5s\t15s\t2s\t5s\t20d\t20d\n
USGS\t09342500\tpk\t\t1911-07-02\t2025-05-14\n"""
        )
    )
    assert records[0].variable == "Annual peak discharge"
    assert records[0].data_type == "Annual peak measurements"
    assert records[0].frequency == "annual_peak"
    assert records[0].parameter_code is None
    assert records[0].notes is not None
