"""Small, provider-independent domain models for the station tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal


Provider = Literal["USGS"]
AnalysisRole = Literal["core", "supporting", "catalog_only", "inventory_only"]


def normalize_station_id(value: str, provider: Provider = "USGS") -> str:
    """Return the canonical bare station identifier.

    The public input accepts common forms such as ``09342500``,
    ``USGS-09342500``, and ``USGS:09342500``. USGS site identifiers in the
    basin catalog may be eight or longer numeric strings, so the validator
    preserves the provider identifier instead of truncating it.
    """

    if provider != "USGS":
        raise ValueError(f"Unsupported provider: {provider}")

    station_id = value.strip().upper()
    for prefix in ("USGS-", "USGS:"):
        if station_id.startswith(prefix):
            station_id = station_id[len(prefix) :]
            break

    if not 8 <= len(station_id) <= 15 or not station_id.isdigit():
        raise ValueError(
            "USGS station ID must be 8 to 15 digits, for example 09342500"
        )
    return station_id


@dataclass(frozen=True)
class StationRequest:
    """The stable external request contract for a single station run."""

    station_id: str
    provider: Provider = "USGS"
    refresh: bool = False
    profile: str = "core"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "station_id", normalize_station_id(self.station_id, self.provider)
        )


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date


@dataclass
class AvailableDataRecord:
    """One provider-advertised data series or category."""

    series_id: str
    variable: str
    data_type: str
    provider_data_type: str | None = None
    # Canonical frequency code used by analysis (for example: daily, unit,
    # annual_peak). ``data_type`` remains the provider-facing display label.
    frequency: str | None = None
    parameter_code: str | None = None
    parameter_name: str | None = None
    unit: str | None = None
    statistic_code: str | None = None
    time_series_id: int | None = None
    declared_start: date | None = None
    declared_end: date | None = None
    observed_start: date | None = None
    observed_end: date | None = None
    provider_count: int | None = None
    quality_code_available: bool | None = None
    analysis_role: AnalysisRole = "inventory_only"
    downloaded: bool = False
    source_url: str | None = None
    notes: str | None = None


@dataclass
class StationMetadata:
    station_id: str
    provider: Provider = "USGS"
    agency_code: str | None = None
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    drainage_area_sq_mi: float | None = None
    elevation_ft: float | None = None
    elevation_accuracy_ft: float | None = None
    elevation_method_code: str | None = None
    elevation_datum: str | None = None
    coordinate_method_code: str | None = None
    coordinate_accuracy_code: str | None = None
    coordinate_datum: str | None = None
    huc_code: str | None = None
    basin_code: str | None = None
    state_code: str | None = None
    county_code: str | None = None
    country_code: str | None = None
    site_type: str | None = None
    site_type_code: str | None = None
    status: str | None = None
    timezone: str | None = None
    local_time_flag: str | None = None
    reliability_code: str | None = None
    construction_date: date | None = None
    inventory_date: date | None = None
    contributing_drainage_area_sq_mi: float | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    available_data: list[AvailableDataRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    kind: str
    sha256: str | None = None
