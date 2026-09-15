from datetime import datetime, timezone

import pandas as pd

from station_hydro.field_measurements import (
    _normalize_field_measurements,
    build_field_stage_discharge_pairs,
    validate_field_pairs_against_daily,
)


def test_pair_field_measurements_by_visit_id() -> None:
    frame = _normalize_field_measurements(
        [
            {
                "id": "d1",
                "field_visit_id": "visit-1",
                "monitoring_location_id": "USGS-09342500",
                "parameter_code": "00060",
                "reading_type": "Discharge",
                "value": "76.8",
                "unit_of_measure": "ft^3/s",
                "time": "2019-09-03T22:38:30+00:00",
                "approval_status": "Approved",
            },
            {
                "id": "h1",
                "field_visit_id": "visit-1",
                "monitoring_location_id": "USGS-09342500",
                "parameter_code": "00065",
                "reading_type": "MeanGageHeight",
                "value": "4.20",
                "unit_of_measure": "ft",
                "time": "2019-09-03T22:38:30+00:00",
                "approval_status": "Approved",
            },
        ]
    )
    pairs = build_field_stage_discharge_pairs(frame)
    assert len(pairs) == 1
    assert pairs["discharge_cfs"].iloc[0] == 76.8
    assert pairs["gage_height_ft"].iloc[0] == 4.2


def test_validation_is_idempotent_for_persisted_pairs(tmp_path) -> None:
    pairs = pd.DataFrame(
        {
            "field_visit_id": ["visit-1"],
            "discharge_cfs": [76.8],
            "gage_height_ft": [4.2],
            "discharge_cfs_published": [75.0],
            "relative_error_pct": [-2.34375],
        }
    )
    daily_path = tmp_path / "daily.parquet"
    pd.DataFrame(
        {
            "observed_date_local": ["2020-01-01"],
            "value": [75.0],
            "quality_code": ["A"],
        }
    ).to_parquet(daily_path)

    result = validate_field_pairs_against_daily(pairs, daily_path)

    pd.testing.assert_frame_equal(result, pairs)
