"""USGS station discovery using HyRiver plus small raw-response adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any
import time

import pandas as pd
import requests
from pygeohydro import NWIS

from .models import AvailableDataRecord, StationMetadata, StationRequest
from .paths import output_station_root, station_root as resolve_station_root
from .storage import write_bytes, write_json


USGS_SITE_URL = f"{NWIS.url}/site/"
USGS_PARAMETER_CODES_URL = (
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/"
    "parameter-codes/items"
)

DATA_TYPE_LABELS = {
    "ad": "Annual data",
    "dv": "Daily values",
    "iv": "Instantaneous values",
    "pk": "Annual peak measurements",
    "qw": "Water-quality samples",
    "sv": "Site visits",
    "uv": "Unit values (continuous)",
}

FREQUENCY_CODES = {
    "ad": "annual",
    "dv": "daily",
    "iv": "instantaneous",
    "pk": "annual_peak",
    "qw": "sample",
    "sv": "site_visit",
    "uv": "unit",
}

CORE_SERIES = {
    ("dv", "00060"),
    ("dv", "00065"),
    ("iv", "00060"),
    ("iv", "00065"),
    ("uv", "00060"),
    ("uv", "00065"),
}


@dataclass
class DiscoveryResult:
    station: StationMetadata
    available_data: list[AvailableDataRecord]
    source_artifacts: list[dict[str, Any]]
    retrieved_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "station": asdict(self.station),
            "available_data": [asdict(item) for item in self.available_data],
            "source_artifacts": self.source_artifacts,
            "retrieved_at": self.retrieved_at,
        }


def parse_rdb_text(text: str) -> pd.DataFrame:
    """Parse an NWIS tab-delimited RDB response without losing raw text."""

    rows = [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]
    if len(rows) < 2:
        return pd.DataFrame()
    return pd.DataFrame.from_records(
        [dict(zip(rows[0], row)) for row in rows[2:]], columns=rows[0]
    ).replace({"": pd.NA})


def _as_optional_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _as_optional_int(value: Any) -> int | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _parameter_definition_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    for feature in payload.get("features", []):
        properties = feature.get("properties", {})
        code = str(properties.get("id") or feature.get("id") or "").strip()
        if code and code not in definitions:
            definitions[code] = properties
    return definitions


def _parameter_codes(catalog: pd.DataFrame) -> list[str]:
    if "parm_cd" not in catalog:
        return []
    return sorted(
        {
            str(value).strip()
            for value in catalog["parm_cd"].dropna()
            if str(value).strip()
        }
    )


def classify_analysis_role(data_type_code: str, parameter_code: str | None) -> str:
    if (data_type_code, parameter_code or "") in CORE_SERIES:
        return "core"
    if data_type_code in {"pk", "sv"}:
        return "supporting"
    if data_type_code in {"ad", "qw"}:
        return "inventory_only"
    return "inventory_only"


def build_available_data(
    catalog: pd.DataFrame,
    parameter_definitions: dict[str, dict[str, Any]] | None = None,
) -> list[AvailableDataRecord]:
    """Convert one NWIS series-catalog row into one stable inventory record."""

    parameter_definitions = parameter_definitions or {}
    records: list[AvailableDataRecord] = []
    for row in catalog.to_dict(orient="records"):
        data_type_code = _clean_text(row.get("data_type_cd"))
        parameter_code = _clean_text(row.get("parm_cd")) or None
        definition = parameter_definitions.get(parameter_code or "", {})
        variable = definition.get("parameter_name") or {
            "00060": "Discharge",
            "00065": "Gage height",
        }.get(parameter_code or "", f"USGS parameter {parameter_code or 'unspecified'}")
        notes = None
        if data_type_code == "pk" and parameter_code is None:
            # NWIS's pk category is annual peak discharge even though the
            # series-catalog row leaves parm_cd blank. Preserve the blank
            # source field and normalize only the analytical display fields.
            variable = "Annual peak discharge"
            notes = "NWIS pk category; source catalog omits parm_cd."
        elif data_type_code == "sv" and parameter_code is None:
            variable = "Site visits"
        series_id = ":".join(
            [
                _clean_text(row.get("agency_cd")) or "USGS",
                _clean_text(row.get("site_no")),
                data_type_code or "unknown",
                parameter_code or "none",
                _clean_text(row.get("stat_cd")) or "none",
                _clean_text(row.get("ts_id")) or "none",
            ]
        )
        records.append(
            AvailableDataRecord(
                series_id=series_id,
                variable=str(variable),
                data_type=DATA_TYPE_LABELS.get(
                    data_type_code, f"USGS data type {data_type_code or 'unspecified'}"
                ),
                provider_data_type=data_type_code or None,
                frequency=FREQUENCY_CODES.get(data_type_code),
                parameter_code=parameter_code,
                parameter_name=definition.get("parameter_name"),
                unit=definition.get("unit_of_measure"),
                statistic_code=_clean_text(row.get("stat_cd")) or None,
                time_series_id=_as_optional_int(row.get("ts_id")),
                declared_start=_as_optional_date(row.get("begin_date")),
                declared_end=_as_optional_date(row.get("end_date")),
                provider_count=_as_optional_int(row.get("count_nu")),
                quality_code_available=None,
                analysis_role=classify_analysis_role(data_type_code, parameter_code),
                source_url=USGS_SITE_URL,
                notes=notes,
            )
        )
    return records


def write_availability_summary(
    station_root: Path,
    records: list[AvailableDataRecord],
    output_root: Path | None = None,
    plan: list[Any] | None = None,
) -> Path:
    """Write provider availability alongside local-download status.

    This table is deliberately metadata-first: ``provider_count`` describes
    what USGS advertises, while ``local_status`` says whether this workspace
    has actually downloaded the corresponding canonical observation file.
    """

    plan_by_series = {item.series_id: item for item in (plan or [])}
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.provider_data_type == "dv" and record.parameter_code == "00060":
            local_path = station_root / "observations" / "daily_discharge.parquet"
        elif record.provider_data_type == "pk":
            local_path = station_root / "observations" / "annual_peak_discharge.parquet"
        elif record.provider_data_type in {"uv", "iv"} and record.parameter_code:
            local_path = station_root / "observations" / f"unit_{record.parameter_code}.parquet"
        else:
            local_path = None
        plan_item = plan_by_series.get(record.series_id)
        if local_path is not None and local_path.exists():
            local_status = "downloaded"
            manifest = local_path.with_name(f"{local_path.stem}_manifest.json")
            local_rows = None
            if manifest.exists():
                try:
                    local_rows = json.loads(manifest.read_text(encoding="utf-8")).get("rows")
                except (OSError, json.JSONDecodeError):
                    local_rows = None
        elif plan_item is not None and plan_item.action == "defer":
            local_status = "not_downloaded"
            local_rows = None
        else:
            local_status = "catalog_only"
            local_rows = None
        rows.append(
            {
                "series_id": record.series_id,
                "variable": record.variable,
                "data_type": record.data_type,
                "provider_data_type": record.provider_data_type,
                "frequency": record.frequency,
                "parameter_code": record.parameter_code,
                "unit": record.unit,
                "declared_start": record.declared_start,
                "declared_end": record.declared_end,
                "provider_record_count": record.provider_count,
                "download_plan_action": plan_item.action if plan_item else None,
                "local_status": local_status,
                "local_record_count": local_rows,
                "local_path": str(local_path) if local_path is not None else None,
                "source_url": record.source_url,
            }
        )
    table = pd.DataFrame(rows)
    roots = {station_root / "metadata"}
    if output_root is not None:
        roots.add(output_station_root(output_root, station_root) / "metadata")
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        table.to_csv(root / "availability_summary.csv", index=False)
        write_json(
            root / "availability_summary.json",
            {"station_id": station_root.name.removeprefix("USGS_"), "series": rows},
        )
    return station_root / "metadata" / "availability_summary.csv"


class USGSProvider:
    """Discovery adapter for one USGS station."""

    def __init__(
        self,
        data_root: Path,
        output_root: Path | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.data_root = data_root
        self.output_root = output_root
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "station-hydro-tool/0.1 (local hydrology analysis)"}
        )

    def _get_raw(
        self,
        url: str,
        params: dict[str, str],
        path: Path,
        reuse_existing: bool = False,
        fallback_on_rate_limit: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        if reuse_existing and path.exists():
            payload = path.read_bytes()
            prepared_url = requests.Request("GET", url, params=params).prepare().url
            return payload.decode("utf-8"), {
                "path": str(path),
                "url": prepared_url,
                "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                "status_code": 200,
                "sha256": write_bytes(path, payload),
                "from_cache": True,
            }

        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                response.raise_for_status()
                checksum = write_bytes(path, response.content)
                return response.text, {
                    "path": str(path),
                    "url": response.url,
                    "retrieved_at": datetime.now(timezone.utc),
                    "status_code": response.status_code,
                    "sha256": checksum,
                    "from_cache": False,
                }
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if (
                    status == 429
                    and fallback_on_rate_limit
                    and path.exists()
                    and path.stat().st_size > 0
                ):
                    payload = path.read_bytes()
                    prepared_url = requests.Request("GET", url, params=params).prepare().url
                    return payload.decode("utf-8"), {
                        "path": str(path),
                        "url": prepared_url,
                        "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                        "status_code": 200,
                        "sha256": write_bytes(path, payload),
                        "from_cache": True,
                        "rate_limit_fallback": True,
                    }
                if status not in {429, 500, 502, 503, 504} or attempt == 3:
                    raise
                retry_after = exc.response.headers.get("Retry-After") if exc.response else None
                try:
                    delay = float(retry_after) if retry_after else 0.0
                except (TypeError, ValueError):
                    delay = 0.0
                if delay <= 0:
                    delay = (5 * 2**attempt) if status == 429 else 2**attempt
                time.sleep(min(delay, 60.0))
        raise RuntimeError("USGS request retry loop ended unexpectedly")

    def discover(self, request: StationRequest) -> DiscoveryResult:
        station_root = resolve_station_root(self.data_root, request)
        raw_root = station_root / "raw"
        metadata_root = station_root / "metadata"
        retrieved_at = datetime.now(timezone.utc)

        # HyRiver provides typed station metadata and validates the NWIS query.
        site_info = NWIS.get_info(
            {"sites": request.station_id, "siteStatus": "all"},
            expanded=True,
            fix_names=False,
        )
        if site_info.empty:
            raise ValueError(f"USGS station not found: {request.station_id}")

        site_text, site_artifact = self._get_raw(
            USGS_SITE_URL,
            {
                "format": "rdb",
                "sites": request.station_id,
                "siteStatus": "all",
                "siteOutput": "expanded",
            },
            raw_root / "site_info.rdb",
        )
        catalog_text, catalog_artifact = self._get_raw(
            USGS_SITE_URL,
            {
                "format": "rdb",
                "sites": request.station_id,
                "siteStatus": "all",
                "seriesCatalogOutput": "true",
            },
            raw_root / "series_catalog.rdb",
        )
        catalog = parse_rdb_text(catalog_text)
        parameter_codes = _parameter_codes(catalog)
        parameter_payload: dict[str, Any] = {"type": "FeatureCollection", "features": []}
        parameter_artifact: dict[str, Any] | None = None
        if parameter_codes:
            parameter_params = {"f": "json", "id": ",".join(parameter_codes)}
            parameter_path = raw_root / "parameter_codes.json"
            try:
                parameter_text, parameter_artifact = self._get_raw(
                    USGS_PARAMETER_CODES_URL,
                    parameter_params,
                    parameter_path,
                    reuse_existing=not request.refresh,
                )
            except requests.HTTPError as exc:
                # Parameter definitions are shared reference metadata. If the
                # provider rate-limits a refresh, a prior valid snapshot is
                # safer than aborting the station update; observation
                # requests still have to succeed and retain their own
                # retrieval timestamps.
                status = exc.response.status_code if exc.response is not None else None
                if status != 429 or not parameter_path.exists() or parameter_path.stat().st_size == 0:
                    raise
                parameter_text, parameter_artifact = self._get_raw(
                    USGS_PARAMETER_CODES_URL,
                    parameter_params,
                    parameter_path,
                    reuse_existing=True,
                )
                parameter_artifact["rate_limit_fallback"] = True
            parameter_payload = json.loads(parameter_text)

        parameter_definitions = _parameter_definition_map(parameter_payload)
        records = build_available_data(catalog, parameter_definitions)
        row = site_info.iloc[0]
        station = StationMetadata(
            station_id=request.station_id,
            agency_code=str(row.get("agency_cd") or "USGS"),
            name=str(row.get("station_nm") or "").strip() or None,
            latitude=_as_optional_float(row.get("dec_lat_va")),
            longitude=_as_optional_float(row.get("dec_long_va")),
            drainage_area_sq_mi=_as_optional_float(row.get("drain_area_va")),
            elevation_ft=_as_optional_float(row.get("alt_va")),
            elevation_accuracy_ft=_as_optional_float(row.get("alt_acy_va")),
            elevation_method_code=_clean_text(row.get("alt_meth_cd")) or None,
            elevation_datum=str(row.get("alt_datum_cd") or "").strip() or None,
            coordinate_method_code=_clean_text(row.get("coord_meth_cd")) or None,
            coordinate_accuracy_code=_clean_text(row.get("coord_acy_cd")) or None,
            coordinate_datum=str(row.get("dec_coord_datum_cd") or "").strip() or None,
            huc_code=str(row.get("huc_cd") or "").strip() or None,
            basin_code=_clean_text(row.get("basin_cd")) or None,
            state_code=str(row.get("state_cd") or "").strip() or None,
            county_code=str(row.get("county_cd") or "").strip() or None,
            country_code=_clean_text(row.get("country_cd")) or None,
            site_type=str(row.get("site_tp_cd") or "").strip() or None,
            site_type_code=str(row.get("site_tp_cd") or "").strip() or None,
            timezone=str(row.get("tz_cd") or "").strip() or None,
            local_time_flag=_clean_text(row.get("local_time_fg")) or None,
            reliability_code=_clean_text(row.get("reliability_cd")) or None,
            construction_date=_as_optional_date(row.get("construction_dt")),
            inventory_date=_as_optional_date(row.get("inventory_dt")),
            contributing_drainage_area_sq_mi=_as_optional_float(row.get("contrib_drain_area_va")),
            source_url=f"https://waterdata.usgs.gov/monitoring-location/USGS-{request.station_id}",
            retrieved_at=retrieved_at,
            available_data=records,
        )
        artifacts = [site_artifact, catalog_artifact]
        if parameter_artifact:
            artifacts.append(parameter_artifact)
        result = DiscoveryResult(
            station=station,
            available_data=records,
            source_artifacts=artifacts,
            retrieved_at=retrieved_at,
        )
        output_metadata_root = (
            output_station_root(self.output_root, station_root) / "metadata"
            if self.output_root is not None
            else metadata_root
        )
        metadata_roots = {metadata_root, output_metadata_root}
        for root in metadata_roots:
            write_json(root / "station_metadata.json", asdict(station))
            write_json(
                root / "available_data.json",
                {
                    "station_id": request.station_id,
                    "series_count": len(records),
                    "series": [asdict(record) for record in records],
                    "source_artifacts": artifacts,
                    "retrieved_at": retrieved_at,
                },
            )
            coverage = pd.DataFrame(
                [
                    {
                        "series_id": item.series_id,
                        "variable": item.variable,
                        "data_type": item.data_type,
                        "provider_data_type": item.provider_data_type,
                        "frequency": item.frequency,
                        "parameter_code": item.parameter_code,
                        "parameter_name": item.parameter_name,
                        "unit": item.unit,
                        "statistic_code": item.statistic_code,
                        "declared_start": item.declared_start,
                        "declared_end": item.declared_end,
                        "provider_count": item.provider_count,
                        "analysis_role": item.analysis_role,
                    }
                    for item in records
                ]
            )
            coverage.to_csv(root / "coverage.csv", index=False)
            write_json(
                root / "discovery_manifest.json",
                {
                    "station_id": request.station_id,
                    "provider": request.provider,
                    "retrieved_at": retrieved_at,
                    "source_artifacts": artifacts,
                    "raw_site_response": str(Path(site_artifact["path"])),
                    "raw_series_catalog_response": str(Path(catalog_artifact["path"])),
                },
            )
        write_availability_summary(
            station_root,
            records,
            output_root=self.output_root,
        )
        return result
