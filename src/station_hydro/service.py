"""Application-facing station workflow and local artifact reader.

The service layer is intentionally small.  It keeps the CLI, the local web
application, and a future basin-platform plugin on the same station contract
without making any of them depend on the other's presentation code.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .models import StationRequest
from .paths import output_station_root, station_root as resolve_station_root


@dataclass(frozen=True)
class StationRunOptions:
    """Options exposed by the station web/API boundary."""

    station_id: str
    data_dir: Path = Path("data/stations")
    output_dir: Path = Path("outputs")
    refresh: bool = False
    with_continuous: bool = True
    profile: str = "core"

    @property
    def request(self) -> StationRequest:
        return StationRequest(
            station_id=self.station_id,
            refresh=self.refresh,
            profile=self.profile,
        )


def run_station(options: StationRunOptions) -> dict[str, Path | None]:
    """Run the existing station pipeline through a stable application seam.

    The CLI remains the owner of the detailed provider workflow for now.  This
    wrapper is deliberately the only place the web layer reaches into it, so
    the retrieval implementation can be replaced by a cleaner pipeline module
    later without changing the API contract.
    """

    from .cli import _run_pipeline

    request = options.request
    args = SimpleNamespace(
        data_dir=options.data_dir,
        output_dir=options.output_dir,
        with_continuous=options.with_continuous,
    )
    return _run_pipeline(args, request)


def refresh_station_daily(options: StationRunOptions) -> dict[str, Path | None]:
    """Refresh only the USGS daily-discharge path for an existing package.

    Metadata discovery is refreshed so the provider-declared end date moves
    forward, but continuous observations, ratings, field measurements, and
    watershed downloads are intentionally outside this view-triggered seam.
    """

    from .cli import _read_station_timezone, _station_root, _write_download_plan
    from .discovery import USGSProvider, write_availability_summary
    from .quality import write_station_quality
    from .hydrology import write_station_hydrology
    from .retrieval import available_data_from_json, build_download_plan, fetch_daily_discharge

    request = options.request
    station_root = _station_root(options.data_dir, request)
    inventory_path = station_root / "metadata" / "available_data.json"
    if request.refresh or not inventory_path.exists():
        USGSProvider(options.data_dir, output_root=options.output_dir).discover(request)
    records = available_data_from_json(inventory_path)
    daily_record = next(
        (
            record
            for record in records
            if record.provider_data_type == "dv" and record.parameter_code == "00060"
        ),
        None,
    )
    if daily_record is None:
        raise ValueError(f"USGS station {request.station_id} has no daily discharge series")
    plan = build_download_plan(records, include_continuous=False)
    plan_path = station_root / "metadata" / "download_plan.json"
    _write_download_plan(plan_path, request, plan)
    write_availability_summary(station_root, records, options.output_dir, plan)
    daily_path = fetch_daily_discharge(
        request,
        daily_record,
        options.data_dir,
        station_timezone=_read_station_timezone(station_root),
    )
    return {
        "station_root": station_root,
        "plan": plan_path,
        "daily": daily_path,
        "quality": write_station_quality(station_root, options.output_dir),
        "hydrology": write_station_hydrology(station_root),
    }


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_station_snapshot(
    station_id: str,
    *,
    data_dir: Path = Path("data/stations"),
    output_dir: Path = Path("outputs"),
) -> dict[str, Any]:
    """Read a station package into the JSON-safe API response contract.

    This function never contacts a provider.  A missing package is explicit so
    callers can decide whether to run a live refresh or show an offline error.
    """

    request = StationRequest(station_id)
    root = resolve_station_root(data_dir, request)
    metadata_path = root / "metadata" / "station_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(
            f"No local package for USGS station {request.station_id}. "
            "Run the station workflow first."
        )

    metadata = _read_json(metadata_path, {})
    availability = _read_json(root / "metadata" / "availability_summary.json", {})
    if not availability:
        availability = {
            "station_id": request.station_id,
            "series": metadata.get("available_data", []),
        }
    quality = _read_json(root / "quality" / "quality_summary.json", {})
    hydrology = _read_json(root / "hydrology" / "hydrology_summary.json", {})
    basin_manifest = _read_json(
        root / "spatial" / "contributing_watershed_manifest.json", {}
    )
    coverage = _read_csv(root / "metadata" / "coverage.csv")
    observation_files = []
    observation_root = root / "observations"
    for path in sorted(observation_root.glob("*.parquet")):
        observation_files.append(
            {
                "name": path.name,
                "relative_path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
            }
        )

    return {
        "station": {
            key: value for key, value in metadata.items() if key != "available_data"
        },
        "available_data": availability.get("series", []),
        "coverage": coverage,
        "quality": quality,
        "hydrology": hydrology,
        "spatial": {
            "contributing_watershed": {
                "available": (root / "spatial" / "contributing_watershed.geojson").is_file(),
                "url": f"/api/v1/stations/{request.station_id}/basin",
                "manifest": basin_manifest,
            },
            "flow_network_available": (root / "spatial" / "flow_network.geojson").is_file(),
        },
        "observations": observation_files,
        "provenance": {
            "data_root": str(root),
            "output_root": str(output_station_root(output_dir, root)),
            "discovery_manifest": _read_json(
                root / "metadata" / "discovery_manifest.json", {}
            ),
        },
    }
