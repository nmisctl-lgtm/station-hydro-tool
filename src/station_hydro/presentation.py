"""Read-only presentation adapters for locally cached station packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .service import load_station_snapshot


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def list_cached_stations(data_dir: Path) -> list[dict[str, Any]]:
    """Return a compact Overview register from local station metadata only.

    The browser never needs to know package paths.  A missing ``data_dir`` is
    a valid empty cache, not an application error.
    """

    if not data_dir.is_dir():
        return []

    stations: list[dict[str, Any]] = []
    for metadata_path in sorted(data_dir.rglob("metadata/station_metadata.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue

        station_id = str(metadata.get("station_id") or "").strip()
        if not station_id:
            station_id = metadata_path.parent.parent.name.removeprefix("USGS_")
        if not station_id:
            continue
        latitude = _number(metadata.get("latitude"))
        longitude = _number(metadata.get("longitude"))
        stations.append(
            {
                "location_key": f"USGS:{station_id}",
                "provider_station_id": station_id,
                "source_name": "USGS",
                "display_name": metadata.get("name") or f"USGS {station_id}",
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_status": (
                    "valid" if latitude is not None and longitude is not None else "review"
                ),
                "activity_status": "local_cached",
            }
        )
    return sorted(stations, key=lambda row: (str(row["display_name"]), str(row["location_key"])))


def cached_station_feature_collection(data_dir: Path) -> dict[str, Any]:
    """Expose coordinate-valid local packages as browser-ready GeoJSON."""

    features: list[dict[str, Any]] = []
    for station in list_cached_stations(data_dir):
        if station["coordinate_status"] != "valid":
            continue
        properties = {
            key: station[key]
            for key in (
                "location_key",
                "provider_station_id",
                "source_name",
                "display_name",
                "coordinate_status",
                "activity_status",
            )
        }
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [station["longitude"], station["latitude"]],
                },
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def cached_overview(data_dir: Path) -> dict[str, int | str]:
    """Summarize only data already created on this machine.

    This is deliberately not a release manifest: the source repository ships
    no station observations and has no claim to a basin-wide record count.
    """

    stations = list_cached_stations(data_dir)
    mapped = sum(station["coordinate_status"] == "valid" for station in stations)
    return {
        "mode": "local_dynamic_cache",
        "registered_location_count": len(stations),
        "mapped_location_count": mapped,
        "coordinate_review_count": len(stations) - mapped,
        "daily_observation_count": 0,
    }


def _station_id_from_key(location_key: str) -> str:
    provider, separator, station_id = str(location_key).partition(":")
    if provider.upper() != "USGS" or not separator:
        raise ValueError("Station keys must use the form USGS:station_id")
    return station_id


def _analysis_series(available_data: list[dict[str, Any]], location_key: str) -> list[dict[str, Any]]:
    series: list[dict[str, Any]] = []
    for index, record in enumerate(available_data):
        if not isinstance(record, dict):
            continue
        parameter_code = str(record.get("parameter_code") or "")
        provider_data_type = str(record.get("provider_data_type") or "")
        series.append(
            {
                "series_key": str(
                    record.get("series_id")
                    or f"{location_key}:{provider_data_type}:{parameter_code}:{index}"
                ),
                "source_name": "USGS",
                "source_variable": parameter_code,
                "variable_name": record.get("variable"),
                "frequency": record.get("frequency"),
                "unit_canonical": record.get("unit"),
                "declared_start": record.get("declared_start"),
                "declared_end": record.get("declared_end"),
                "observed_date_start": record.get("declared_start"),
                "observed_date_end": record.get("declared_end"),
                "raw_observation_count": record.get("local_record_count"),
                "numeric_observation_count": record.get("local_record_count"),
                "local_status": record.get("local_status"),
                "parameter_code": record.get("parameter_code"),
                "provider_data_type": record.get("provider_data_type"),
            }
        )
    return series


def local_station_analysis(
    location_key: str,
    *,
    data_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Adapt one local package to the browser's dynamic analysis contract."""

    station_id = _station_id_from_key(location_key)
    snapshot = load_station_snapshot(
        station_id, data_dir=data_dir, output_dir=output_dir
    )
    metadata = snapshot["station"]
    latitude = _number(metadata.get("latitude"))
    longitude = _number(metadata.get("longitude"))
    available_data = [
        record for record in snapshot.get("available_data", []) if isinstance(record, dict)
    ]
    latest_observed = max(
        (str(record["declared_end"]) for record in available_data if record.get("declared_end")),
        default=None,
    )
    retrieved_at = metadata.get("retrieved_at")
    station = {
        "location_key": f"USGS:{station_id}",
        "provider_station_id": station_id,
        "source_name": "USGS",
        "display_name": metadata.get("name") or f"USGS {station_id}",
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_status": "valid" if latitude is not None and longitude is not None else "review",
        "activity_status": "local_cached",
        "station_type": metadata.get("site_type"),
        "state_name": metadata.get("state_code"),
        "huc": metadata.get("huc_code"),
        "timezone": metadata.get("timezone"),
        "drainage_area_sq_mi": metadata.get("drainage_area_sq_mi"),
        "latest_observed_at": latest_observed,
        "latest_retrieved_at": retrieved_at,
        "local_snapshot_id": f"USGS:{station_id}@{retrieved_at or 'unknown-retrieval-time'}",
    }
    return {
        "station": station,
        "series": _analysis_series(available_data, station["location_key"]),
        "monthly": [],
        "month_of_year": [],
        "quality_event_summary": [],
    }
