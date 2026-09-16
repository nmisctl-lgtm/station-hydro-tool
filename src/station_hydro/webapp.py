"""Small FastAPI application for the standalone station tool."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import StationRequest
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
    app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")
    package_reader = StationPackageReader(data_dir)

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

    @app.get("/api/v1/stations")
    def stations(limit: int = Query(500, ge=1, le=5000)) -> dict[str, list[dict[str, Any]]]:
        """List local packages; a fresh clone intentionally returns an empty list."""

        return {"data": list_cached_stations(data_dir)[:limit]}

    @app.get("/api/v1/map/stations")
    def station_map() -> dict[str, Any]:
        return {"data": cached_station_feature_collection(data_dir)}

    @app.get("/api/v1/overview")
    def overview() -> dict[str, Any]:
        return {"data": cached_overview(data_dir)}

    @app.get("/api/v1/stations/{location_key}/analysis")
    def station_analysis(location_key: str) -> dict[str, Any]:
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
