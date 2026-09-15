"""Controlled observation retrieval for the Phase 1 USGS station package."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .discovery import USGS_SITE_URL, USGSProvider, parse_rdb_text
from .models import AvailableDataRecord, DateWindow, StationRequest
from .paths import station_root as resolve_station_root
from .storage import write_json
from .time_utils import recent_water_year_window


USGS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"
USGS_PEAK_URL = (
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/peaks/items"
)
USGS_PEAK_RDB_URL = "https://nwis.waterdata.usgs.gov/nwis/peak"


@dataclass(frozen=True)
class DownloadPlanItem:
    series_id: str
    variable: str
    parameter_code: str | None
    provider_data_type: str | None
    action: str
    start: date | None
    end: date | None
    reason: str


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def available_data_from_json(path: Path) -> list[AvailableDataRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = []
    for item in payload.get("series", []):
        fields = dict(item)
        for key in ("declared_start", "declared_end", "observed_start", "observed_end"):
            fields[key] = _parse_date(fields.get(key))
        records.append(AvailableDataRecord(**fields))
    return records


def build_download_plan(
    records: list[AvailableDataRecord],
    latest_date: date | None = None,
    recent_unit_years: int = 10,
    include_continuous: bool = False,
) -> list[DownloadPlanItem]:
    """Build a metadata-first plan from the discovered inventory.

    Continuous/unit series remain visible in the plan even when they are not
    downloaded.  They are opt-in because the provider-declared record count is
    sufficient for the default availability view and full IV/UV retrieval can
    be large.
    """

    if not records:
        return []
    latest = latest_date or max(
        (record.declared_end for record in records if record.declared_end),
        default=date.today(),
    )
    unit_window = recent_water_year_window(latest, recent_unit_years)
    plan: list[DownloadPlanItem] = []
    for record in records:
        data_type = record.provider_data_type or ""
        parameter = record.parameter_code
        if data_type == "dv" and parameter == "00060":
            action = "download"
            start, end = record.declared_start, record.declared_end
            reason = "full declared daily discharge history"
        elif data_type in {"uv", "iv"} and parameter == "00065":
            if include_continuous:
                action = "download"
                start = record.declared_start or unit_window.start
                end = record.declared_end or unit_window.end
                reason = (
                    "full declared continuous stage history; plots select the "
                    "recent Water Year window"
                )
            else:
                action = "defer"
                start, end = record.declared_start, record.declared_end
                reason = "metadata only by default; continuous stage retrieval is opt-in"
        elif data_type in {"uv", "iv"} and parameter == "00060":
            if include_continuous:
                action = "download"
                start, end = record.declared_start, record.declared_end
                reason = "full declared continuous discharge history for completeness and comparison"
            else:
                action = "defer"
                start, end = record.declared_start, record.declared_end
                reason = "metadata only by default; continuous discharge retrieval is opt-in"
        elif data_type == "pk":
            action = "download"
            start, end = record.declared_start, record.declared_end
            reason = "provider annual peak observations for flood-frequency analysis"
        else:
            action = "skip"
            start, end = record.declared_start, record.declared_end
            reason = "inventory only or not required by the core profile"
        plan.append(
            DownloadPlanItem(
                series_id=record.series_id,
                variable=record.variable,
                parameter_code=parameter,
                provider_data_type=data_type or None,
                action=action,
                start=start,
                end=end,
                reason=reason,
            )
        )
    return plan


def _station_timezone(name: str | None) -> ZoneInfo:
    return ZoneInfo({"MST": "America/Denver", "MDT": "America/Denver"}.get(name or "", "UTC"))


def _parse_daily_discharge(
    text: str,
    record: AvailableDataRecord,
    source_url: str,
    station_timezone: str | None,
    retrieved_at: datetime,
) -> pd.DataFrame:
    frame = parse_rdb_text(text)
    if frame.empty:
        return pd.DataFrame()
    value_columns = [
        column
        for column in frame.columns
        if column not in {"agency_cd", "site_no", "datetime"}
        and not column.endswith("_cd")
    ]
    if not value_columns:
        raise ValueError("Daily RDB response does not contain a value column")
    value_column = value_columns[0]
    qualifier_column = f"{value_column}_cd"
    local_zone = _station_timezone(station_timezone)
    observed_date = pd.to_datetime(frame["datetime"], errors="coerce").dt.date
    observed_at = pd.to_datetime(
        [datetime.combine(day, time.min).replace(tzinfo=local_zone) for day in observed_date],
        utc=True,
    )
    result = pd.DataFrame(
        {
            "provider": "USGS",
            "station_id": frame["site_no"].astype(str),
            "series_id": record.series_id,
            "variable": record.variable,
            "parameter_code": record.parameter_code,
            "frequency": "daily",
            "observed_at_utc": observed_at,
            "observed_date_local": observed_date,
            "value": pd.to_numeric(frame[value_column], errors="coerce"),
            "unit": record.unit or "ft3/s",
            "quality_code": frame[qualifier_column].astype("string")
            if qualifier_column in frame
            else pd.Series(pd.NA, index=frame.index, dtype="string"),
            "source_url": source_url,
            "retrieved_at": retrieved_at,
        }
    )
    return result.dropna(subset=["observed_date_local"]).reset_index(drop=True)


def _json_time_series(payload: dict[str, Any]) -> list[dict[str, Any]]:
    root = payload.get("value", payload)
    return root.get("timeSeries", []) if isinstance(root, dict) else []


def _parse_instantaneous_values(
    payload: dict[str, Any],
    record: AvailableDataRecord,
    source_url: str,
    station_timezone: str | None,
    retrieved_at: datetime,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    local_zone = _station_timezone(station_timezone)
    for series in _json_time_series(payload):
        variable = series.get("variable", {})
        variable_code = next(
            (
                item.get("value")
                for item in variable.get("variableCode", [])
                if item.get("value")
            ),
            record.parameter_code,
        )
        unit = (variable.get("unit") or {}).get("unitCode") or record.unit or "unknown"
        for value_block in series.get("values", []):
            for item in value_block.get("value", []):
                qualifiers = item.get("qualifiers") or []
                site_codes = series.get("sourceInfo", {}).get("siteCode", [{}])
                rows.append(
                    {
                        "provider": "USGS",
                        "station_id": str(site_codes[0].get("value", "")),
                        "series_id": record.series_id,
                        "variable": record.variable,
                        "parameter_code": str(variable_code) if variable_code else record.parameter_code,
                        "frequency": "unit",
                        "timestamp_raw": item.get("dateTime"),
                        "value": pd.to_numeric(item.get("value"), errors="coerce"),
                        "unit": unit,
                        "quality_code": "|".join(str(code) for code in qualifiers) or pd.NA,
                        "source_url": source_url,
                        "retrieved_at": retrieved_at,
                    }
                )
    if not rows:
        return pd.DataFrame()
    result = pd.DataFrame(rows)
    timestamps = pd.to_datetime(result.pop("timestamp_raw"), errors="coerce", utc=True)
    valid = timestamps.notna()
    result = result.loc[valid].copy()
    timestamps = timestamps.loc[valid]
    result["observed_at_utc"] = timestamps.to_numpy()
    result["observed_date_local"] = timestamps.dt.tz_convert(local_zone).dt.date.to_numpy()
    columns = [
        "provider",
        "station_id",
        "series_id",
        "variable",
        "parameter_code",
        "frequency",
        "observed_at_utc",
        "observed_date_local",
        "value",
        "unit",
        "quality_code",
        "source_url",
        "retrieved_at",
    ]
    return result[columns].reset_index(drop=True)


def _write_observations(frame: pd.DataFrame, path: Path) -> None:
    if frame.empty:
        raise ValueError(f"No observations returned for {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False, compression="zstd")


def fetch_daily_discharge(
    request: StationRequest,
    record: AvailableDataRecord,
    data_root: Path,
    station_timezone: str | None = None,
    timeout_seconds: int = 120,
) -> Path:
    if not record.declared_start or not record.declared_end:
        raise ValueError("Daily series has no declared date range")
    provider = USGSProvider(data_root, timeout_seconds=timeout_seconds)
    station_root = resolve_station_root(data_root, request)
    source_url = USGS_SITE_URL.replace("/site/", "/dv/")
    params = {
        "format": "rdb",
        "sites": request.station_id,
        "parameterCd": record.parameter_code or "00060",
        "statCd": record.statistic_code or "00003",
        "siteStatus": "all",
        "startDT": record.declared_start.isoformat(),
        "endDT": record.declared_end.isoformat(),
    }
    text, artifact = provider._get_raw(
        source_url,
        params,
        station_root / "raw" / "dv_00060_full.rdb",
        reuse_existing=not request.refresh,
    )
    frame = _parse_daily_discharge(
        text,
        record,
        source_url,
        station_timezone,
        datetime.now(timezone.utc),
    )
    output = station_root / "observations" / "daily_discharge.parquet"
    _write_observations(frame, output)
    write_json(
        station_root / "observations" / "daily_discharge_manifest.json",
        {"artifact": artifact, "rows": len(frame), "output": str(output)},
    )
    return output


def _parse_annual_peak(
    text: str,
    record: AvailableDataRecord,
    source_url: str,
    station_timezone: str | None,
    retrieved_at: datetime,
) -> pd.DataFrame:
    if text.lstrip().startswith("{"):
        payload = json.loads(text)
        rows = [dict(feature.get("properties", {})) for feature in payload.get("features", [])]
        frame = pd.DataFrame(rows)
        if frame.empty or "time" not in frame or "value" not in frame:
            return pd.DataFrame()
        observed_date = pd.to_datetime(frame["time"], errors="coerce").dt.date
        station_ids = frame.get(
            "monitoring_location_id", pd.Series(requested_station_id(record), index=frame.index)
        ).astype(str).str.removeprefix("USGS-")
        qualifiers = frame.get(
            "qualifier", pd.Series(pd.NA, index=frame.index, dtype="string")
        ).map(
            lambda value: "|".join(str(item) for item in value)
            if isinstance(value, list)
            else value
        )
        return pd.DataFrame(
            {
                "provider": "USGS",
                "station_id": station_ids,
                "series_id": record.series_id,
                "variable": record.variable,
                # The catalog pk row may omit parm_cd; the modern peaks API
                # identifies this observation as parameter code 00060.
                "parameter_code": frame.get(
                    "parameter_code", pd.Series("00060", index=frame.index)
                ).astype("string"),
                "frequency": "annual_peak",
                "observed_at_utc": pd.to_datetime(frame["time"], errors="coerce", utc=True),
                "observed_date_local": observed_date,
                "value": pd.to_numeric(frame["value"], errors="coerce"),
                "unit": frame.get(
                    "unit_of_measure", pd.Series(record.unit or "ft3/s", index=frame.index)
                ),
                "quality_code": qualifiers.astype("string"),
                "source_url": source_url,
                "retrieved_at": retrieved_at,
            }
        ).dropna(subset=["observed_date_local"]).sort_values(
            "observed_date_local"
        ).reset_index(drop=True)
    frame = parse_rdb_text(text)
    if frame.empty or "peak_dt" not in frame or "peak_va" not in frame:
        return pd.DataFrame()
    local_zone = _station_timezone(station_timezone)
    observed_date = pd.to_datetime(frame["peak_dt"], errors="coerce").dt.date
    observed_at = pd.to_datetime(
        [datetime.combine(day, time.min).replace(tzinfo=local_zone) for day in observed_date],
        utc=True,
    )
    return pd.DataFrame(
        {
            "provider": "USGS",
            "station_id": frame["site_no"].astype(str),
            "series_id": record.series_id,
            "variable": record.variable,
            "parameter_code": record.parameter_code,
            "frequency": "annual_peak",
            "observed_at_utc": observed_at,
            "observed_date_local": observed_date,
            "value": pd.to_numeric(frame["peak_va"], errors="coerce"),
            "unit": record.unit or "ft3/s",
            "quality_code": frame["peak_cd"].astype("string")
            if "peak_cd" in frame
            else pd.Series(pd.NA, index=frame.index, dtype="string"),
            "source_url": source_url,
            "retrieved_at": retrieved_at,
        }
    ).dropna(subset=["observed_date_local"]).sort_values(
        "observed_date_local"
    ).reset_index(drop=True)


def requested_station_id(record: AvailableDataRecord) -> str:
    """Return a fallback station id from a canonical series id."""

    parts = record.series_id.split(":")
    return parts[1] if len(parts) > 1 else ""


def fetch_annual_peak(
    request: StationRequest,
    record: AvailableDataRecord,
    data_root: Path,
    station_timezone: str | None = None,
    timeout_seconds: int = 120,
) -> Path:
    """Download the provider annual-peak series used by flood-frequency analysis."""

    if not record.declared_start or not record.declared_end:
        raise ValueError("Annual peak series has no declared date range")
    provider = USGSProvider(data_root, timeout_seconds=timeout_seconds)
    station_root = resolve_station_root(data_root, request)
    params = {
        "f": "json",
        "monitoring_location_id": f"USGS-{request.station_id}",
        "parameter_code": "00060",
        "limit": "10000",
    }
    raw_path = station_root / "raw" / "peak_annual_full.rdb"
    source_url = USGS_PEAK_URL
    try:
        text, artifact = provider._get_raw(
            USGS_PEAK_URL, params, raw_path, reuse_existing=not request.refresh
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status != 429:
            raise
        legacy_params = {"format": "rdb", "site_no": request.station_id}
        legacy_path = station_root / "raw" / "peak_annual_full_legacy.rdb"
        text, artifact = provider._get_raw(
            USGS_PEAK_RDB_URL,
            legacy_params,
            legacy_path,
            reuse_existing=not request.refresh,
        )
        artifact["rate_limit_fallback"] = True
        artifact["fallback_from"] = USGS_PEAK_URL
        source_url = USGS_PEAK_RDB_URL
    frame = _parse_annual_peak(
        text,
        record,
        source_url,
        station_timezone,
        datetime.now(timezone.utc),
    )
    # The peaks endpoint is already scoped to this station and parameter.  The
    # NWIS catalog dates are an availability hint, not a hard observation
    # filter: local-date versus UTC semantics can put a valid returned peak one
    # day beyond the catalog end date.  Keep every parsed provider observation
    # and retain the declared span separately in the inventory/coverage tables.
    output = station_root / "observations" / "annual_peak_discharge.parquet"
    _write_observations(frame, output)
    write_json(
        station_root / "observations" / "annual_peak_discharge_manifest.json",
        {"artifact": artifact, "rows": len(frame), "output": str(output)},
    )
    return output


def fetch_unit_values(
    request: StationRequest,
    record: AvailableDataRecord,
    window: DateWindow,
    data_root: Path,
    station_timezone: str | None = None,
    chunk_days: int = 31,
    timeout_seconds: int = 120,
    output_name: str | None = None,
) -> Path:
    if not record.parameter_code:
        raise ValueError("Unit series has no parameter code")
    provider = USGSProvider(data_root, timeout_seconds=timeout_seconds)
    station_root = resolve_station_root(data_root, request)
    raw_root = station_root / "raw" / f"iv_{record.parameter_code}"
    frames: list[pd.DataFrame] = []
    artifacts: list[dict[str, Any]] = []
    start = window.start
    retrieved_at = datetime.now(timezone.utc)
    while start <= window.end:
        end = min(start + timedelta(days=chunk_days - 1), window.end)
        params = {
            "format": "json",
            "sites": request.station_id,
            "parameterCd": record.parameter_code,
            "siteStatus": "all",
            "startDT": f"{start.isoformat()}T00:00:00Z",
            "endDT": f"{end.isoformat()}T23:59:59Z",
        }
        raw_path = raw_root / f"{start.isoformat()}_{end.isoformat()}.json"
        text, artifact = provider._get_raw(
            USGS_IV_URL,
            params,
            raw_path,
            reuse_existing=not request.refresh,
        )
        frame = _parse_instantaneous_values(
            json.loads(text),
            record,
            USGS_IV_URL,
            station_timezone,
            retrieved_at,
        )
        if not frame.empty:
            frames.append(frame)
        artifacts.append(artifact)
        start = end + timedelta(days=1)
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not combined.empty:
        combined = combined.drop_duplicates(
            subset=["station_id", "parameter_code", "observed_at_utc"]
        ).sort_values("observed_at_utc")
    output = station_root / "observations" / (
        output_name or f"unit_{record.parameter_code}.parquet"
    )
    _write_observations(combined, output)
    write_json(
        station_root / "observations" / f"{output.stem}_manifest.json",
        {
            "artifacts": artifacts,
            "rows": len(combined),
            "window": {"start": window.start, "end": window.end},
            "output": str(output),
        },
    )
    return output
