"""Build a validated, ranked directory from a seed list of USGS stations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, fields
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

import pandas as pd

from .discovery import DiscoveryResult, USGSProvider
from .models import AvailableDataRecord, StationMetadata, StationRequest
from .paths import station_root as resolve_station_root
from .storage import write_json


SEED_SERIES_MAP = {
    ("discharge", "daily"): {("dv", "00060")},
    ("discharge", "15min"): {("uv", "00060"), ("iv", "00060")},
    ("stage", "daily"): {("dv", "00065")},
    ("stage", "15min"): {("uv", "00065"), ("iv", "00065")},
}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _local_record(payload: dict[str, Any]) -> AvailableDataRecord:
    """Rehydrate one locally cached availability record without network access."""

    values: dict[str, Any] = {}
    date_fields = {
        "declared_start",
        "declared_end",
        "observed_start",
        "observed_end",
    }
    for item in fields(AvailableDataRecord):
        if item.name not in payload:
            continue
        value = payload[item.name]
        values[item.name] = _date(value) if item.name in date_fields else value
    return AvailableDataRecord(**values)


def load_local_discovery_result(data_root: Path, station_id: str) -> DiscoveryResult:
    """Load a previously downloaded station package as a discovery result.

    This is intentionally separate from online discovery: a local catalog run
    can update ranking and directory files while preserving the provider
    metadata snapshot that was already downloaded.
    """

    request = StationRequest(station_id)
    station_root = resolve_station_root(data_root, request)
    metadata_path = station_root / "metadata" / "station_metadata.json"
    availability_path = station_root / "metadata" / "available_data.json"
    manifest_path = station_root / "metadata" / "discovery_manifest.json"
    if not metadata_path.exists() or not availability_path.exists():
        raise FileNotFoundError(
            f"Local station package is missing metadata: {station_root}"
        )

    metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    station_values: dict[str, Any] = {}
    date_fields = {"construction_date", "inventory_date"}
    datetime_fields = {"retrieved_at"}
    for item in fields(StationMetadata):
        if item.name == "available_data" or item.name not in metadata_payload:
            continue
        value = metadata_payload[item.name]
        if item.name in date_fields:
            value = _date(value)
        elif item.name in datetime_fields and value:
            value = datetime.fromisoformat(str(value))
        station_values[item.name] = value

    availability_payload = json.loads(availability_path.read_text(encoding="utf-8"))
    records = [_local_record(item) for item in availability_payload.get("series", [])]
    station = StationMetadata(**station_values, available_data=records)
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    retrieved_at = station.retrieved_at or datetime.now(timezone.utc)
    return DiscoveryResult(
        station=station,
        available_data=records,
        source_artifacts=manifest.get("source_artifacts", []),
        retrieved_at=retrieved_at,
    )


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _clean_name(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).strip()


def load_seed_catalog(path: Path) -> list[dict[str, Any]]:
    """Load and validate the colleague-provided station list."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Seed catalog must be a non-empty JSON list")
    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Every seed catalog item must be an object")
        station_id = str(item.get("gage_id", "")).strip()
        request = StationRequest(station_id)
        if request.station_id in seen:
            raise ValueError(f"Duplicate station ID in seed catalog: {station_id}")
        seen.add(request.station_id)
        if not isinstance(item.get("variables"), dict):
            raise ValueError(f"Missing variables object for {station_id}")
        stations.append(item)
    return stations


def _records_by_key(records: list[AvailableDataRecord]) -> dict[tuple[str, str], list[AvailableDataRecord]]:
    grouped: dict[tuple[str, str], list[AvailableDataRecord]] = {}
    for record in records:
        key = (record.provider_data_type or "", record.parameter_code or "")
        grouped.setdefault(key, []).append(record)
    return grouped


def _core_record(records: list[AvailableDataRecord], data_type: str, parameter_code: str) -> AvailableDataRecord | None:
    candidates = [
        record
        for record in records
        if record.provider_data_type == data_type
        and (
            record.parameter_code == parameter_code
            or (parameter_code == "" and record.parameter_code is None)
        )
    ]
    return max(candidates, key=lambda record: record.declared_end or date.min, default=None)


def _record_summary(records: list[AvailableDataRecord]) -> dict[str, Any]:
    by_type = Counter(record.provider_data_type or "unspecified" for record in records)
    by_frequency = Counter(record.frequency or "unspecified" for record in records)
    return {
        "total_series": len(records),
        "by_provider_data_type": dict(sorted(by_type.items())),
        "by_frequency": dict(sorted(by_frequency.items())),
        "core": {
            "daily_discharge": _series_dict(_core_record(records, "dv", "00060")),
            "daily_gage_height": _series_dict(_core_record(records, "dv", "00065")),
            "continuous_discharge": _series_dict(_continuous_record(records, "00060")),
            "continuous_gage_height": _series_dict(_continuous_record(records, "00065")),
            "annual_peak_discharge": _series_dict(_core_record(records, "pk", "")),
        },
        "quality_code_status": (
            "not_declared_in_series_catalog; inspect observation records after download"
        ),
    }


def _continuous_record(records: list[AvailableDataRecord], parameter_code: str) -> AvailableDataRecord | None:
    candidates = [
        record
        for record in records
        if record.provider_data_type in {"uv", "iv"}
        and record.parameter_code == parameter_code
    ]
    return max(candidates, key=lambda record: record.declared_end or date.min, default=None)


def _series_dict(record: AvailableDataRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "series_id": record.series_id,
        "provider_data_type": record.provider_data_type,
        "data_type": record.data_type,
        "frequency": record.frequency,
        "parameter_code": record.parameter_code,
        "parameter_name": record.parameter_name,
        "unit": record.unit,
        "statistic_code": record.statistic_code,
        "declared_start": _iso(record.declared_start),
        "declared_end": _iso(record.declared_end),
        "provider_record_count": record.provider_count,
        "analysis_role": record.analysis_role,
        "quality_code_status": "not_declared_in_series_catalog",
        "source_url": record.source_url,
        "notes": record.notes,
    }


def _seed_series(seed: dict[str, Any]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for variable, frequencies in seed.get("variables", {}).items():
        for frequency, record in frequencies.items():
            key = f"{variable}/{frequency}"
            result[key] = record is not None
    return result


def _provider_series_presence(records: list[AvailableDataRecord]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for (variable, frequency), candidates in SEED_SERIES_MAP.items():
        result[f"{variable}/{frequency}"] = any(
            (record.provider_data_type, record.parameter_code) in candidates
            for record in records
        )
    return result


def _validate_seed(seed: dict[str, Any], result: DiscoveryResult) -> dict[str, Any]:
    station = result.station
    seed_lat = float(seed["latitude"])
    seed_lon = float(seed["longitude"])
    name_match = _clean_name(seed.get("name")) == _clean_name(station.name)
    latitude_delta = abs(seed_lat - (station.latitude or seed_lat))
    longitude_delta = abs(seed_lon - (station.longitude or seed_lon))
    seed_presence = _seed_series(seed)
    provider_presence = _provider_series_presence(result.available_data)
    series_mismatches = [
        key
        for key, expected in seed_presence.items()
        if expected != provider_presence.get(key, False)
    ]
    return {
        "seed_name_match": name_match,
        "seed_latitude_delta_deg": latitude_delta,
        "seed_longitude_delta_deg": longitude_delta,
        "seed_coordinate_match": latitude_delta <= 0.001 and longitude_delta <= 0.001,
        "seed_series_presence": seed_presence,
        "provider_series_presence": provider_presence,
        "series_mismatches": series_mismatches,
        "status": "match" if name_match and not series_mismatches and latitude_delta <= 0.001 and longitude_delta <= 0.001 else "review",
    }


def _recent_status(records: list[AvailableDataRecord], as_of: date) -> tuple[str, str, date | None]:
    hydrology_records = [
        record
        for record in records
        if (
            (record.provider_data_type == "dv" and record.parameter_code in {"00060", "00065"})
            or (
                record.provider_data_type in {"uv", "iv"}
                and record.parameter_code in {"00060", "00065"}
            )
            or record.provider_data_type == "pk"
        )
    ]
    records_to_check = hydrology_records or records
    ends = [record.declared_end for record in records_to_check if record.declared_end]
    latest_end = max(ends, default=None)
    if latest_end is None:
        return "unknown", "No bounded provider end date", None
    age_days = (as_of - latest_end).days
    if age_days <= 120:
        return "recent_data", f"Latest declared record is {age_days} days old", latest_end
    return "not_recent", f"Latest declared record is {age_days} days old; confirm inactive status", latest_end


def _importance_profile(station: Any, records: list[AvailableDataRecord], as_of: date) -> dict[str, Any]:
    name = _clean_name(station.name)
    if any(
        token in name
        for token in (
            "EAST FORK SAN JUAN",
            "EF SAN JUAN",
            "WEST FORK SAN JUAN",
            "WF SAN JUAN",
            "W FK SAN JUAN",
        )
    ):
        basin_role = "East/West Fork San Juan tributary"
        role_score = 37
    elif "WOLF CREEK" in name:
        basin_role = "Wolf Creek upper-basin tributary"
        role_score = 32
    elif "TURKEY CREEK" in name:
        basin_role = "Turkey Creek upper-basin tributary"
        role_score = 30
    elif "PIEDRA" in name:
        basin_role = "Piedra River major tributary"
        role_score = 36
    elif "SAN JUAN" in name:
        basin_role = "San Juan mainstem"
        role_score = 45
    elif "ANIMAS" in name:
        basin_role = "major Animas tributary"
        role_score = 39
    elif "LA PLATA" in name:
        basin_role = "major La Plata tributary"
        role_score = 34
    else:
        basin_role = "other basin station"
        role_score = 25

    daily = _core_record(records, "dv", "00060")
    daily_years = 0.0
    if daily and daily.declared_start and daily.declared_end:
        daily_years = max(0.0, (daily.declared_end - daily.declared_start).days / 365.2425)
    status, status_reason, latest_end = _recent_status(records, as_of)
    record_score = min(25.0, daily_years / 4.0)
    current_score = 15.0 if status == "recent_data" else 3.0
    core = _record_summary(records)["core"]
    core_count = sum(value is not None for value in core.values())
    data_score = min(15.0, core_count * 3.0)
    drainage_score = min(10.0, max(0.0, float(station.drainage_area_sq_mi or 0.0)) / 800.0 * 10.0)
    score = round(role_score + record_score + current_score + data_score + drainage_score, 2)

    if score >= 85:
        tier = "A"
    elif score >= 65:
        tier = "B"
    else:
        tier = "C"
    reasons = [basin_role, f"{daily_years:.1f} years of declared daily discharge"]
    if status == "recent_data":
        reasons.append("recent provider data available")
    else:
        reasons.append("historical or not-recent data; verify operational status")
    if core.get("continuous_discharge") and core.get("continuous_gage_height"):
        reasons.append("continuous discharge and gage-height series support hydrograph work")
    if core.get("annual_peak_discharge"):
        reasons.append("annual peak series supports flood-frequency screening")
    if station.drainage_area_sq_mi:
        reasons.append(f"drainage area {station.drainage_area_sq_mi:.1f} square miles")
    return {
        "screening_priority_score": score,
        "importance_tier": tier,
        "basin_role": basin_role,
        "daily_discharge_record_years": round(daily_years, 2),
        "latest_declared_end": _iso(latest_end),
        "operational_status_screen": status,
        "operational_status_basis": status_reason,
        "importance_reasons": reasons,
        "scoring_note": "Screening order for staged project work, not an official USGS importance rating.",
    }


def _station_entry(seed: dict[str, Any], result: DiscoveryResult, as_of: date) -> dict[str, Any]:
    station = result.station
    required_metadata = [
        "name",
        "latitude",
        "longitude",
        "drainage_area_sq_mi",
        "elevation_ft",
        "elevation_datum",
        "coordinate_datum",
        "huc_code",
        "state_code",
        "county_code",
        "timezone",
    ]
    optional_metadata = [
        "reliability_code",
        "construction_date",
        "inventory_date",
        "contributing_drainage_area_sq_mi",
    ]
    metadata_missing = [field for field in required_metadata if getattr(station, field) in (None, "")]
    optional_missing = [field for field in optional_metadata if getattr(station, field) in (None, "")]
    return {
        "station_id": station.station_id,
        "provider": station.provider,
        "name": station.name,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "agency_code": station.agency_code,
        "drainage_area_sq_mi": station.drainage_area_sq_mi,
        "elevation_ft": station.elevation_ft,
        "elevation_accuracy_ft": station.elevation_accuracy_ft,
        "elevation_method_code": station.elevation_method_code,
        "elevation_datum": station.elevation_datum,
        "coordinate_method_code": station.coordinate_method_code,
        "coordinate_accuracy_code": station.coordinate_accuracy_code,
        "coordinate_datum": station.coordinate_datum,
        "huc_code": station.huc_code,
        "basin_code": station.basin_code,
        "state_code": station.state_code,
        "county_code": station.county_code,
        "country_code": station.country_code,
        "site_type": station.site_type,
        "timezone": station.timezone,
        "local_time_flag": station.local_time_flag,
        "reliability_code": station.reliability_code,
        "construction_date": _iso(station.construction_date),
        "inventory_date": _iso(station.inventory_date),
        "contributing_drainage_area_sq_mi": station.contributing_drainage_area_sq_mi,
        "source_url": station.source_url,
        "retrieved_at": _iso(result.retrieved_at),
        "seed_metadata": {
            "name": seed.get("name"),
            "latitude": seed.get("latitude"),
            "longitude": seed.get("longitude"),
            "variables": seed.get("variables"),
        },
        "validation": _validate_seed(seed, result),
        "metadata_quality": {
            "analysis_metadata_complete": not metadata_missing,
            "required_metadata_missing": metadata_missing,
            "optional_metadata_missing": optional_missing,
            "series_catalog_retrieved": True,
            "series_catalog_scope": "NWIS series-catalog rows; observation-level quality codes require downloaded records",
        },
        "importance": _importance_profile(station, result.available_data, as_of),
        "analysis_readiness": {
            "daily_discharge": _core_record(result.available_data, "dv", "00060") is not None,
            "annual_peak_discharge": _core_record(result.available_data, "pk", "") is not None,
            "continuous_discharge": _continuous_record(result.available_data, "00060") is not None,
            "continuous_gage_height": _continuous_record(result.available_data, "00065") is not None,
            "field_measurements": any(record.provider_data_type == "sv" for record in result.available_data),
            "watershed_boundary": "available through NLDI when the station has a resolvable navigation link",
        },
        "available_data_summary": _record_summary(result.available_data),
        "available_data": [asdict(record) for record in result.available_data],
        "source_artifacts": result.source_artifacts,
    }


def _flat_station(entry: dict[str, Any]) -> dict[str, Any]:
    importance = entry["importance"]
    validation = entry["validation"]
    readiness = entry["analysis_readiness"]
    metadata_quality = entry["metadata_quality"]
    summary = entry["available_data_summary"]
    core = summary["core"]
    return {
        "rank": entry.get("rank"),
        "station_id": entry["station_id"],
        "name": entry["name"],
        "importance_tier": importance["importance_tier"],
        "screening_priority_score": importance["screening_priority_score"],
        "basin_role": importance["basin_role"],
        "importance_reasons": " | ".join(importance["importance_reasons"]),
        "operational_status_screen": importance["operational_status_screen"],
        "operational_status_basis": importance["operational_status_basis"],
        "latitude": entry["latitude"],
        "longitude": entry["longitude"],
        "drainage_area_sq_mi": entry["drainage_area_sq_mi"],
        "elevation_ft": entry["elevation_ft"],
        "elevation_accuracy_ft": entry["elevation_accuracy_ft"],
        "elevation_method_code": entry["elevation_method_code"],
        "elevation_datum": entry["elevation_datum"],
        "coordinate_method_code": entry["coordinate_method_code"],
        "coordinate_accuracy_code": entry["coordinate_accuracy_code"],
        "coordinate_datum": entry["coordinate_datum"],
        "huc_code": entry["huc_code"],
        "basin_code": entry["basin_code"],
        "state_code": entry["state_code"],
        "county_code": entry["county_code"],
        "country_code": entry["country_code"],
        "site_type": entry["site_type"],
        "timezone": entry["timezone"],
        "local_time_flag": entry["local_time_flag"],
        "reliability_code": entry["reliability_code"],
        "construction_date": entry["construction_date"],
        "inventory_date": entry["inventory_date"],
        "contributing_drainage_area_sq_mi": entry["contributing_drainage_area_sq_mi"],
        "daily_discharge_start": (core["daily_discharge"] or {}).get("declared_start"),
        "daily_discharge_end": (core["daily_discharge"] or {}).get("declared_end"),
        "annual_peak_start": (core["annual_peak_discharge"] or {}).get("declared_start"),
        "annual_peak_end": (core["annual_peak_discharge"] or {}).get("declared_end"),
        "continuous_discharge_start": (core["continuous_discharge"] or {}).get("declared_start"),
        "continuous_discharge_end": (core["continuous_discharge"] or {}).get("declared_end"),
        "continuous_stage_start": (core["continuous_gage_height"] or {}).get("declared_start"),
        "continuous_stage_end": (core["continuous_gage_height"] or {}).get("declared_end"),
        "total_available_series": summary["total_series"],
        "available_data_types": json.dumps(summary["by_provider_data_type"], sort_keys=True),
        "daily_discharge": readiness["daily_discharge"],
        "annual_peak_discharge": readiness["annual_peak_discharge"],
        "continuous_discharge": readiness["continuous_discharge"],
        "continuous_gage_height": readiness["continuous_gage_height"],
        "field_measurements": readiness["field_measurements"],
        "quality_code_status": summary["quality_code_status"],
        "seed_validation_status": validation["status"],
        "analysis_metadata_complete": metadata_quality["analysis_metadata_complete"],
        "required_metadata_missing": " | ".join(metadata_quality["required_metadata_missing"]),
        "optional_metadata_missing": " | ".join(metadata_quality["optional_metadata_missing"]),
        "source_url": entry["source_url"],
        "retrieved_at": entry["retrieved_at"],
    }


def _flat_series(entry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series in entry["available_data"]:
        rows.append(
            {
                "rank": entry.get("rank"),
                "station_id": entry["station_id"],
                "station_name": entry["name"],
                "importance_tier": entry["importance"]["importance_tier"],
                "screening_priority_score": entry["importance"]["screening_priority_score"],
                **series,
            }
        )
    return rows


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# SJ Basin USGS Station Directory",
        "",
        f"Generated at: `{payload['generated_at']}`",
        f"Source seed: `{payload['source_seed']}`",
        f"Source mode: `{payload['source_mode']}`",
        "",
        "This is a validated working directory for staged station analysis. The ranking is a transparent project-work screening order, not an official USGS rating.",
        "",
        "## Ranked stations",
        "",
        "| Rank | Station | Tier | Score | Daily Q start | Latest declared end | Validation |",
        "| ---: | --- | :---: | ---: | --- | --- | --- |",
    ]
    for entry in payload["stations"]:
        core = entry["available_data_summary"]["core"]
        lines.append(
            "| {rank} | {station_id} — {name} | {tier} | {score} | {start} | {end} | {validation} |".format(
                rank=entry["rank"],
                station_id=entry["station_id"],
                name=entry["name"],
                tier=entry["importance"]["importance_tier"],
                score=entry["importance"]["screening_priority_score"],
                start=(core["daily_discharge"] or {}).get("declared_start", ""),
                end=entry["importance"]["latest_declared_end"] or "",
                validation=entry["validation"]["status"],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation rules",
            "",
            "- `recent_data` means at least one provider-declared series ends within 120 days of the catalog audit date; it is not a formal USGS active-status field.",
            "- Quality-code availability is not declared by the series catalog. Observation-level quality codes must be retained and assessed after download.",
            "- The standard analysis inputs are daily discharge, annual peak discharge, optional continuous discharge/gage height, field measurements, and the NLDI watershed boundary.",
            "- The original seed JSON is preserved as `seed_metadata` for every station and is never overwritten.",
            "- `local_cached_discovery` means the directory was rebuilt from saved station metadata; rerun online discovery when a fresh provider audit is required.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_station_directory(
    entries: list[dict[str, Any]],
    source_seed: Path,
    output_path: Path,
    generated_at: datetime,
    errors: list[dict[str, str]] | None = None,
    source_mode: str = "online_usgs_discovery",
) -> dict[str, Path]:
    """Write comprehensive JSON plus review-friendly flat tables."""

    ranked = sorted(
        entries,
        key=lambda item: (-item["importance"]["screening_priority_score"], item["station_id"]),
    )
    for rank, entry in enumerate(ranked, start=1):
        entry["rank"] = rank
    payload = {
        "schema_version": "0.2",
        "catalog_name": "SJ Basin USGS station directory",
        "generated_at": generated_at,
        "audit_date": generated_at.date(),
        "source_seed": str(source_seed),
        "source_provider": "USGS Water Data for the Nation / NWIS",
        "source_mode": source_mode,
        "station_count": len(ranked),
        "complete": not errors,
        "completeness_definition": (
            "All seed stations were successfully queried from USGS. Blank optional fields reflect blank provider metadata and are documented per station."
            if source_mode == "online_usgs_discovery"
            else "All seed stations had a locally cached discovery package. The directory reflects those saved provider snapshots; it is not an online freshness audit."
        ),
        "required_analysis_metadata": [
            "name",
            "latitude",
            "longitude",
            "drainage_area_sq_mi",
            "elevation_ft",
            "elevation_datum",
            "coordinate_datum",
            "huc_code",
            "state_code",
            "county_code",
            "timezone",
        ],
        "errors": errors or [],
        "ranking_method": {
            "role": "mainstem > major Animas/La Plata tributary > other",
            "record_length": "longer declared daily-discharge record receives more weight",
            "recency": "recent provider-declared data receives more weight",
            "analysis_readiness": "annual peak, continuous discharge/gage height, and drainage area add weight",
        },
        "stations": ranked,
    }
    write_json(output_path, payload)

    flat_path = output_path.with_suffix(".csv")
    pd.DataFrame([_flat_station(entry) for entry in ranked]).to_csv(flat_path, index=False)
    series_path = output_path.with_name(f"{output_path.stem}_series.csv")
    pd.DataFrame([row for entry in ranked for row in _flat_series(entry)]).to_csv(series_path, index=False)
    validation_path = output_path.with_name(f"{output_path.stem}_validation.csv")
    pd.DataFrame(
        [
            {
                "rank": entry["rank"],
                "station_id": entry["station_id"],
                "name": entry["name"],
                **entry["validation"],
            }
            for entry in ranked
        ]
    ).to_csv(validation_path, index=False)
    audit_path = output_path.with_name(f"{output_path.stem}_audit.json")
    write_json(
        audit_path,
        {
            "generated_at": generated_at,
            "source_seed": source_seed,
            "station_count": len(ranked),
            "complete": not errors,
            "errors": errors or [],
            "validation_counts": dict(Counter(entry["validation"]["status"] for entry in ranked)),
            "series_mismatch_count": sum(bool(entry["validation"]["series_mismatches"]) for entry in ranked),
            "quality_code_status": "observation-level check required after download",
            "source_mode": source_mode,
        },
    )
    markdown_path = output_path.with_suffix(".md")
    _write_markdown(markdown_path, payload)
    return {
        "json": output_path,
        "csv": flat_path,
        "series_csv": series_path,
        "validation_csv": validation_path,
        "audit_json": audit_path,
        "markdown": markdown_path,
    }


def build_station_directory(
    seed_path: Path,
    data_root: Path,
    output_path: Path,
    timeout_seconds: int = 60,
) -> dict[str, Path]:
    """Discover every seed station and build a ranked directory."""

    seeds = load_seed_catalog(seed_path)
    provider = USGSProvider(data_root=data_root, timeout_seconds=timeout_seconds)
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    generated_at = datetime.now(timezone.utc)
    for seed in seeds:
        station_id = str(seed["gage_id"])
        try:
            result = provider.discover(StationRequest(station_id))
            entries.append(_station_entry(seed, result, generated_at.date()))
        except Exception as exc:
            errors.append({"station_id": station_id, "error": f"{type(exc).__name__}: {exc}"})
    paths = write_station_directory(entries, seed_path, output_path, generated_at, errors)
    if errors:
        raise RuntimeError(
            f"Station directory is incomplete: {len(errors)} of {len(seeds)} stations failed; see {paths['audit_json']}"
        )
    return paths


def build_station_directory_from_local(
    seed_path: Path,
    data_root: Path,
    output_path: Path,
) -> dict[str, Path]:
    """Build a ranked directory from cached station packages only."""

    seeds = load_seed_catalog(seed_path)
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    generated_at = datetime.now(timezone.utc)
    for seed in seeds:
        station_id = str(seed["gage_id"])
        try:
            result = load_local_discovery_result(data_root, station_id)
            entries.append(_station_entry(seed, result, generated_at.date()))
        except Exception as exc:
            errors.append({"station_id": station_id, "error": f"{type(exc).__name__}: {exc}"})
    paths = write_station_directory(
        entries,
        seed_path,
        output_path,
        generated_at,
        errors,
        source_mode="local_cached_discovery",
    )
    if errors:
        raise RuntimeError(
            f"Local station directory is incomplete: {len(errors)} of {len(seeds)} stations failed; see {paths['audit_json']}"
        )
    return paths
