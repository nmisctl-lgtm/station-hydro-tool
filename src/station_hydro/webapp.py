"""Small FastAPI application for the standalone station tool."""

from __future__ import annotations

from pathlib import Path
import re
import json
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import StationRequest
from .basin_overview import BasinOverviewError, BasinOverviewReader
from .package_reader import (
    StationPackageError,
    StationPackageNotFound,
    StationPackageReader,
)
from .paths import station_root as resolve_station_root
from .presentation import (
    cached_overview,
    cached_station_feature_collection,
    list_cached_stations,
    local_station_analysis,
)
from .service import (
    StationRunOptions,
    load_station_snapshot,
    refresh_station_daily,
    run_station,
)


_SOURCE_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
_PACKAGED_WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ROOT = _SOURCE_WEB_ROOT if _SOURCE_WEB_ROOT.is_dir() else _PACKAGED_WEB_ROOT


def _series_station_and_parameter(series_key: str) -> tuple[str, str]:
    parts = str(series_key).split(":")
    if len(parts) < 3 or parts[0].upper() != "USGS":
        raise ValueError("Series keys must identify a USGS station")
    parameter = next((part for part in parts[2:] if re.fullmatch(r"\d{5}", part)), None)
    if parameter not in {"00060", "00065"}:
        raise ValueError("Only local discharge and gage-height daily adapters are available")
    return f"USGS:{parts[1]}", parameter


def create_app(
    *,
    data_dir: Path = Path("data/stations"),
    output_dir: Path = Path("outputs"),
    overview_dir: Path | None = None,
) -> FastAPI:
    """Create the standalone station API and browser application."""

    app = FastAPI(
        title="Station Hydro Tool",
        version="0.1.0",
        description=(
            "Station-ID-first USGS metadata, data, watershed, quality, and "
            "visualization service."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def no_cache_local_assets(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith(("/station/", "/static/")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
    package_reader = StationPackageReader(data_dir)
    if overview_dir is None:
        overview_root = data_dir.parent / "overview"
        candidates = sorted(
            path
            for path in overview_root.glob("*")
            if path.is_dir() and (path / "console" / "overview.json").is_file()
        ) if overview_root.is_dir() else []
        overview_dir = candidates[-1] if candidates else None
    try:
        overview_reader = (
            BasinOverviewReader.open(overview_dir, data_dir)
            if overview_dir is not None
            else None
        )
    except BasinOverviewError as exc:
        raise ValueError(str(exc)) from exc
    update_locks: dict[str, threading.Lock] = {}
    update_locks_guard = threading.Lock()

    def package_data(callback: Any) -> dict[str, Any]:
        try:
            return {"data": callback()}
        except StationPackageNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except StationPackageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    def overview_parameters(request: Request) -> dict[str, list[str]]:
        return parse_qs(request.url.query, keep_blank_values=True)

    def station_package_state(location_key: str) -> tuple[Path | None, dict[str, Any] | None]:
        try:
            request = StationRequest(location_key.split(":", maxsplit=1)[-1])
            root = resolve_station_root(data_dir, request)
        except (ValueError, RuntimeError):
            return None, None
        metadata_path = root / "metadata" / "station_metadata.json"
        if not metadata_path.is_file():
            return root, None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return root, None
        return root, metadata if isinstance(metadata, dict) else None

    def cached_station_has_inventory(location_key: str) -> bool:
        _, metadata = station_package_state(location_key)
        return bool(metadata and metadata.get("available_data"))

    def ensure_daily_update(location_key: str) -> None:
        """Check the daily source once per day when a station is viewed.

        The page remains usable with its last local snapshot if a provider is
        unavailable.  A small marker makes the refresh auditable and prevents
        the station page's several API calls from starting duplicate downloads.
        """

        if not location_key.startswith("USGS:"):
            return
        root, metadata = station_package_state(location_key)
        if root is None:
            return
        if metadata is not None and not metadata.get("available_data"):
            return
        station_id = location_key.split(":", maxsplit=1)[1]
        marker_path = root / "metadata" / "daily_update_state.json"
        now = datetime.now(timezone.utc)
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
            checked_at = datetime.fromisoformat(str(marker.get("checked_at_utc", "")).replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if marker.get("status") == "updated" and (now - checked_at).total_seconds() < 24 * 60 * 60:
                return
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        with update_locks_guard:
            lock = update_locks.setdefault(location_key, threading.Lock())
        with lock:
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
                checked_at = datetime.fromisoformat(str(marker.get("checked_at_utc", "")).replace("Z", "+00:00"))
                if checked_at.tzinfo is None:
                    checked_at = checked_at.replace(tzinfo=timezone.utc)
                if marker.get("status") == "updated" and (now - checked_at).total_seconds() < 24 * 60 * 60:
                    return
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
            state: dict[str, Any] = {
                "station_id": station_id,
                "checked_at_utc": now.isoformat().replace("+00:00", "Z"),
                "source": "USGS daily values",
                "status": "updated",
            }
            try:
                options = StationRunOptions(
                    station_id=station_id,
                    data_dir=data_dir,
                    output_dir=output_dir,
                    refresh=metadata is not None,
                    with_continuous=False,
                )
                result = (
                    refresh_station_daily(options)
                    if metadata is not None
                    else run_station(options)
                )
                daily_path = result.get("daily")
                state["daily_path"] = str(daily_path) if daily_path else None
            except Exception as exc:  # pragma: no cover - provider-specific failure
                state["status"] = "error"
                state["error"] = str(exc)
            try:
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass

    @app.get("/station/{provider}/{station_id}", include_in_schema=False)
    def station_page(provider: str, station_id: str) -> FileResponse:
        if provider.upper() != "USGS":
            raise HTTPException(status_code=404, detail="Only USGS is supported in Phase 1")
        try:
            StationRequest(station_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return FileResponse(WEB_ROOT / "station.html")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "station-hydro-tool"}

    @app.get("/api/v1/meta/release")
    def release_metadata() -> dict[str, Any]:
        if overview_reader is None:
            return {"data": {"release_id": "local-dynamic-cache", "display_only": True}}
        return overview_reader.envelope(overview_reader.release_metadata())

    @app.get("/api/v1/context/overview")
    def context_overview() -> dict[str, Any]:
        if overview_reader is None:
            return {"data": {"available": False, "manifest": {}, "paths": {}}}
        return overview_reader.envelope(overview_reader.context_overview())

    @app.get("/api/v1/stations")
    def stations(request: Request, limit: int = Query(500, ge=1, le=5000)) -> dict[str, Any]:
        """List release stations when present, otherwise local station packages."""

        if overview_reader is None:
            return {"data": list_cached_stations(data_dir)[:limit]}
        parameters = overview_parameters(request)
        parameters["limit"] = [str(limit)]
        return overview_reader.envelope(overview_reader.stations(parameters))

    @app.get("/api/v1/map/stations")
    def station_map(request: Request) -> dict[str, Any]:
        if overview_reader is None:
            return {"data": cached_station_feature_collection(data_dir)}
        return overview_reader.envelope(overview_reader.map_stations(overview_parameters(request)))

    @app.get("/api/v1/overview")
    def overview(request: Request) -> dict[str, Any]:
        if overview_reader is None:
            return {"data": cached_overview(data_dir)}
        return overview_reader.envelope(overview_reader.overview_data(overview_parameters(request)))

    @app.get("/api/v1/operations/runs")
    def operation_runs() -> dict[str, Any]:
        if overview_reader is None:
            return {"data": []}
        return overview_reader.envelope(overview_reader.update_runs_data())

    @app.get("/api/v1/operations/source-status")
    def source_status() -> dict[str, Any]:
        if overview_reader is None:
            return {"data": []}
        return overview_reader.envelope(overview_reader.source_archive_status())

    @app.get("/api/v1/qa/matrix")
    def quality_matrix(request: Request) -> dict[str, Any]:
        if overview_reader is None:
            return {"data": {"months": [], "rows": [], "interpretation": "No Overview release is configured."}}
        return overview_reader.envelope(overview_reader.quality_matrix(overview_parameters(request)))

    @app.get("/api/v1/qa/series")
    def quality_series(request: Request) -> dict[str, Any]:
        if overview_reader is None:
            return {"data": []}
        return overview_reader.envelope(overview_reader.qa_series(overview_parameters(request)))

    @app.get("/api/v1/provenance/artifacts/{artifact_id}")
    def source_artifact(artifact_id: str) -> dict[str, Any]:
        if overview_reader is None:
            raise HTTPException(status_code=404, detail="No Overview release is configured")
        try:
            return overview_reader.envelope(overview_reader.source_artifact(artifact_id))
        except BasinOverviewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/gis/{requested_path:path}", include_in_schema=False)
    def gis_file(requested_path: str) -> FileResponse:
        if overview_reader is None:
            raise HTTPException(status_code=404, detail="No Overview release is configured")
        root = (overview_reader.release_path / "gis").resolve()
        candidate = (root / requested_path).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=404, detail="GIS file is not part of the local Overview release")
        return FileResponse(candidate)

    @app.get("/context/{requested_path:path}", include_in_schema=False)
    def context_file(requested_path: str) -> FileResponse:
        root = (data_dir / "_context").resolve()
        candidate = (root / requested_path).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Context file is not part of the local station data")
        return FileResponse(candidate)

    @app.get("/api/v1/stations/{location_key}/analysis")
    def station_analysis(location_key: str) -> dict[str, Any]:
        ensure_daily_update(location_key)
        try:
            return {
                "data": local_station_analysis(
                    location_key, data_dir=data_dir, output_dir=output_dir
                )
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/stations/{location_key}/hydrology")
    def station_hydrology(location_key: str) -> dict[str, Any]:
        return package_data(lambda: package_reader.hydrology(location_key))

    @app.get("/api/v1/stations/{location_key}/hydrology/fdc")
    def station_fdc(location_key: str) -> dict[str, Any]:
        return package_data(lambda: package_reader.flow_duration_curve(location_key))

    @app.get("/api/v1/stations/{location_key}/watershed")
    def station_watershed(location_key: str) -> dict[str, Any]:
        return package_data(lambda: package_reader.watershed(location_key))

    @app.get("/api/v1/stations/{location_key}/flow-network")
    def station_flow_network(location_key: str) -> dict[str, Any]:
        return package_data(lambda: package_reader.flow_network(location_key))

    @app.get("/api/v1/stations/{location_key}/local-daily")
    def station_local_daily(
        location_key: str,
        source_variable: str = Query(...),
        start: str = Query(...),
        end: str = Query(...),
    ) -> dict[str, Any]:
        return package_data(
            lambda: package_reader.local_daily(location_key, source_variable, start, end)
        )

    @app.get("/api/v1/stations/{location_key}/baseflow-daily")
    def station_baseflow_daily(
        location_key: str, start: str = Query(...), end: str = Query(...)
    ) -> dict[str, Any]:
        return package_data(lambda: package_reader.baseflow_daily(location_key, start, end))

    @app.get("/api/v1/stations/{location_key}/field-stage-discharge")
    def station_field_stage_discharge(
        location_key: str, start: str = Query(...), end: str = Query(...)
    ) -> dict[str, Any]:
        return package_data(
            lambda: package_reader.field_stage_discharge(location_key, start, end)
        )

    @app.get("/api/v1/stations/{location_key}/stage-discharge")
    def station_stage_discharge(
        location_key: str, start: str = Query(...), end: str = Query(...)
    ) -> dict[str, Any]:
        return package_data(lambda: package_reader.stage_discharge(location_key, start, end))

    @app.get("/api/v1/series/{series_key}/daily")
    def series_daily(
        series_key: str,
        start: str = Query(...),
        end: str = Query(...),
        statistic: str = Query("mean"),
    ) -> dict[str, Any]:
        if statistic != "mean":
            raise HTTPException(status_code=400, detail="Only mean daily values are available")
        try:
            location_key, parameter_code = _series_station_and_parameter(series_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return package_data(
            lambda: package_reader.local_daily(location_key, parameter_code, start, end)
        )

    @app.get("/api/v1/stations/{station_id}")
    def station_summary(station_id: str) -> dict[str, Any]:
        try:
            return load_station_snapshot(
                station_id, data_dir=data_dir, output_dir=output_dir
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/stations/{station_id}/run")
    def station_run(
        station_id: str,
        refresh: bool = Query(False),
        with_continuous: bool = Query(True),
    ) -> dict[str, Any]:
        try:
            options = StationRunOptions(
                station_id=station_id,
                data_dir=data_dir,
                output_dir=output_dir,
                refresh=refresh,
                with_continuous=with_continuous,
            )
            run_station(options)
            return load_station_snapshot(
                station_id, data_dir=data_dir, output_dir=output_dir
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - provider-specific failure
            raise HTTPException(
                status_code=502,
                detail=f"Station workflow failed: {exc}",
            ) from exc

    @app.get("/api/v1/stations/{station_id}/basin")
    def station_basin(station_id: str) -> Any:
        try:
            request = StationRequest(station_id)
            root = resolve_station_root(data_dir, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        basin_path = root / "spatial" / "contributing_watershed.geojson"
        if not basin_path.exists():
            raise HTTPException(status_code=404, detail="Watershed boundary is not available")
        import json

        return json.loads(basin_path.read_text(encoding="utf-8"))

    return app
