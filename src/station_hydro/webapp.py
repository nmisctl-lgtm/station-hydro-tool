"""Small FastAPI application for the standalone station tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import StationRequest
from .paths import output_station_root, station_root as resolve_station_root
from .service import (
    StationRunOptions,
    load_station_snapshot,
    run_station,
)


_SOURCE_WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
_PACKAGED_WEB_ROOT = Path(__file__).resolve().parent / "web"
WEB_ROOT = _SOURCE_WEB_ROOT if _SOURCE_WEB_ROOT.is_dir() else _PACKAGED_WEB_ROOT


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
        return FileResponse(WEB_ROOT / "index.html")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "station-hydro-tool"}

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

    @app.get("/api/v1/stations/{station_id}/figures/{file_name}")
    def station_figure(station_id: str, file_name: str) -> FileResponse:
        try:
            request = StationRequest(station_id)
            root = resolve_station_root(data_dir, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if Path(file_name).name != file_name or Path(file_name).suffix not in {".png", ".svg"}:
            raise HTTPException(status_code=400, detail="Invalid figure file name")
        path = output_station_root(output_dir, root) / "figures" / file_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Figure is not available")
        media_type = "image/svg+xml" if path.suffix == ".svg" else "image/png"
        return FileResponse(path, media_type=media_type)

    return app
