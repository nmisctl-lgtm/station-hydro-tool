"""USGS field measurements used as the Stage–Discharge ground-truth layer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .discovery import USGSProvider
from .models import StationRequest
from .paths import station_root as resolve_station_root
from .storage import write_json


USGS_FIELD_MEASUREMENTS_URL = (
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/"
    "field-measurements/items"
)


def _feature_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for feature in payload.get("features", []):
        row = dict(feature.get("properties", {}))
        row["record_id"] = feature.get("id")
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates", [])
        row["longitude"] = coordinates[0] if len(coordinates) > 0 else None
        row["latitude"] = coordinates[1] if len(coordinates) > 1 else None
        rows.append(row)
    return rows


def _normalize_field_measurements(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["measurement_time_utc"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
    frame["parameter_code"] = frame["parameter_code"].astype("string")
    frame["quality_code"] = frame.get("qualifier", pd.Series(pd.NA, index=frame.index)).astype("string")
    frame = frame.rename(columns={"monitoring_location_id": "station_key"})
    columns = [
        "record_id",
        "field_measurements_series_id",
        "field_visit_id",
        "station_key",
        "parameter_code",
        "reading_type",
        "value",
        "unit_of_measure",
        "measurement_time_utc",
        "quality_code",
        "approval_status",
        "measuring_agency",
        "observing_procedure_code",
        "observing_procedure",
        "measurement_rated",
        "control_condition",
        "last_modified",
        "latitude",
        "longitude",
    ]
    return frame[[column for column in columns if column in frame]].sort_values(
        "measurement_time_utc"
    ).reset_index(drop=True)


def build_field_stage_discharge_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    """Pair Discharge and MeanGageHeight readings from the same field visit."""

    if frame.empty:
        return pd.DataFrame()
    discharge = frame[
        (frame["parameter_code"] == "00060") & (frame["reading_type"] == "Discharge")
    ].copy()
    stage = frame[
        (frame["parameter_code"] == "00065")
        & (frame["reading_type"] == "MeanGageHeight")
    ].copy()
    discharge = discharge.drop_duplicates("field_visit_id")
    stage = stage.drop_duplicates("field_visit_id")
    discharge = discharge.rename(
        columns={
            "value": "discharge_cfs",
            "unit_of_measure": "discharge_unit",
            "measurement_time_utc": "discharge_time_utc",
            "quality_code": "discharge_quality_code",
            "approval_status": "discharge_approval_status",
            "measurement_rated": "measurement_rated",
        }
    )
    stage = stage.rename(
        columns={
            "value": "gage_height_ft",
            "unit_of_measure": "gage_height_unit",
            "measurement_time_utc": "gage_height_time_utc",
            "quality_code": "gage_height_quality_code",
            "approval_status": "gage_height_approval_status",
        }
    )
    columns = [
        "field_visit_id",
        "discharge_cfs",
        "discharge_unit",
        "discharge_time_utc",
        "discharge_quality_code",
        "discharge_approval_status",
        "measurement_rated",
        "gage_height_ft",
        "gage_height_unit",
        "gage_height_time_utc",
        "gage_height_quality_code",
        "gage_height_approval_status",
        "observing_procedure",
        "control_condition",
    ]
    pairs = discharge.merge(stage, on="field_visit_id", suffixes=("", "_stage"))
    pairs = pairs[[column for column in columns if column in pairs]]
    return pairs.dropna(subset=["discharge_cfs", "gage_height_ft"]).reset_index(drop=True)


def validate_field_pairs_against_daily(
    field_pairs: pd.DataFrame,
    daily_discharge_path: Path,
    station_timezone: str = "America/Denver",
) -> pd.DataFrame:
    if field_pairs.empty:
        return field_pairs.copy()
    # The fetch step persists the validated pair table so that plotting can
    # use it directly.  Make this validator idempotent when the quality step
    # reads that already-enriched table again.
    if "discharge_cfs_published" in field_pairs.columns:
        return field_pairs.copy()
    daily = pd.read_parquet(daily_discharge_path)
    daily["discharge_cfs_published"] = pd.to_numeric(daily["value"], errors="coerce")
    daily = daily[["observed_date_local", "discharge_cfs_published", "quality_code"]].rename(
        columns={"quality_code": "published_quality_code"}
    )
    result = field_pairs.copy()
    result["field_date_local"] = pd.to_datetime(result["discharge_time_utc"], utc=True).dt.tz_convert(
        station_timezone
    ).dt.date
    result = result.merge(daily, left_on="field_date_local", right_on="observed_date_local", how="left")
    result["absolute_error_cfs"] = result["discharge_cfs_published"] - result["discharge_cfs"]
    result["relative_error_pct"] = (
        100 * result["absolute_error_cfs"] / result["discharge_cfs"].where(result["discharge_cfs"] != 0)
    )
    result["match_method"] = "same_local_calendar_date_to_published_daily_discharge"
    return result


def fetch_field_measurements(
    request: StationRequest,
    data_root: Path,
    daily_discharge_path: Path | None = None,
    timeout_seconds: int = 120,
) -> tuple[Path, Path]:
    provider = USGSProvider(data_root, timeout_seconds=timeout_seconds)
    station_root = resolve_station_root(data_root, request)
    raw_root = station_root / "raw"
    rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for parameter_code in ("00060", "00065"):
        params = {
            "f": "json",
            "monitoring_location_id": f"USGS-{request.station_id}",
            "parameter_code": parameter_code,
            "limit": "10000",
        }
        raw_path = raw_root / f"field_measurements_{parameter_code}.json"
        text, artifact = provider._get_raw(
            USGS_FIELD_MEASUREMENTS_URL,
            params,
            raw_path,
            reuse_existing=not request.refresh,
            fallback_on_rate_limit=True,
        )
        rows.extend(_feature_rows(json.loads(text)))
        artifacts.append(artifact)
    frame = _normalize_field_measurements(rows)
    observations_root = station_root / "observations"
    observations_root.mkdir(parents=True, exist_ok=True)
    all_path = observations_root / "field_measurements.parquet"
    frame.to_parquet(all_path, index=False, compression="zstd")
    pairs = build_field_stage_discharge_pairs(frame)
    if daily_discharge_path and not pairs.empty:
        pairs = validate_field_pairs_against_daily(pairs, daily_discharge_path)
    pairs_path = observations_root / "field_stage_discharge_pairs.parquet"
    pairs.to_parquet(pairs_path, index=False, compression="zstd")
    write_json(
        observations_root / "field_measurements_manifest.json",
        {
            "station_id": request.station_id,
            "retrieved_at": datetime.now(timezone.utc),
            "rows": len(frame),
            "paired_field_visits": len(pairs),
            "artifacts": artifacts,
            "outputs": [str(all_path), str(pairs_path)],
        },
    )
    return all_path, pairs_path
