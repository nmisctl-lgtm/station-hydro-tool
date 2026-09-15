"""Contributing-watershed retrieval for a USGS station."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from pygeohydro import WBD
from pynhd import GeoConnex
from pynhd import NLDI

from .models import StationRequest
from .paths import station_root as resolve_station_root
from .storage import write_json


SQ_M_PER_SQ_MI = 2_589_988.110336
SJ_BASIN_HUC4 = "1408"


def summarize_basin(
    basin: gpd.GeoDataFrame,
    provider_drainage_area_sq_mi: float | None = None,
) -> dict[str, Any]:
    if basin.empty:
        raise ValueError("NLDI returned no contributing watershed geometry")
    if basin.crs is None:
        raise ValueError("NLDI watershed has no CRS")
    geometry = basin.geometry.iloc[0]
    area_sq_mi = float(basin.to_crs("EPSG:5070").geometry.area.iloc[0] / SQ_M_PER_SQ_MI)
    comparison_pct = None
    if provider_drainage_area_sq_mi and provider_drainage_area_sq_mi > 0:
        comparison_pct = 100 * (area_sq_mi - provider_drainage_area_sq_mi) / provider_drainage_area_sq_mi
    return {
        "source": "USGS NLDI",
        "crs": str(basin.crs),
        "geometry_type": geometry.geom_type,
        "geometry_valid": bool(geometry.is_valid),
        "area_sq_mi_albers_5070": area_sq_mi,
        "provider_drainage_area_sq_mi": provider_drainage_area_sq_mi,
        "area_difference_pct": comparison_pct,
    }


def download_contributing_watershed(
    request: StationRequest,
    data_root: Path,
    provider_drainage_area_sq_mi: float | None = None,
) -> Path:
    basin = NLDI().get_basins(f"USGS-{request.station_id}")
    summary = summarize_basin(basin, provider_drainage_area_sq_mi)
    station_root = resolve_station_root(data_root, request)
    spatial_root = station_root / "spatial"
    spatial_root.mkdir(parents=True, exist_ok=True)
    output = spatial_root / "contributing_watershed.geojson"
    basin.to_file(output, driver="GeoJSON")
    write_json(
        spatial_root / "contributing_watershed_manifest.json",
        {
            "station_id": request.station_id,
            "retrieved_at": datetime.now(timezone.utc),
            "source_url": "https://waterdata.usgs.gov/nldi/",
            "output": str(output),
            "summary": summary,
        },
    )
    return output


def download_flow_network(
    request: StationRequest,
    data_root: Path,
    distance_km: int = 1000,
) -> Path:
    """Download upstream mainstem and tributary flowlines from USGS NLDI."""

    nldi = NLDI()
    frames: list[gpd.GeoDataFrame] = []
    for navigation, network_role in (
        ("upstreamMain", "upstream_main"),
        ("upstreamTributaries", "upstream_tributaries"),
    ):
        flowlines = nldi.navigate_byid(
            fsource="nwissite",
            fid=f"USGS-{request.station_id}",
            navigation=navigation,
            source="flowlines",
            distance=distance_km,
        )
        if flowlines.empty:
            continue
        flowlines = flowlines.reset_index()
        flowlines["network_role"] = network_role
        frames.append(flowlines)
    if not frames:
        raise ValueError("NLDI returned no upstream flowlines")

    network = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs=frames[0].crs,
    )
    comid_column = next(
        (column for column in ("nhdplus_comid", "comid") if column in network.columns),
        None,
    )
    if comid_column:
        network = network.drop_duplicates(comid_column, keep="first")
    station_root = resolve_station_root(data_root, request)
    spatial_root = station_root / "spatial"
    spatial_root.mkdir(parents=True, exist_ok=True)
    output = spatial_root / "flow_network.geojson"
    network.to_file(output, driver="GeoJSON")
    write_json(
        spatial_root / "flow_network_manifest.json",
        {
            "station_id": request.station_id,
            "retrieved_at": datetime.now(timezone.utc),
            "source_url": "https://waterdata.usgs.gov/nldi/",
            "distance_km": distance_km,
            "flowline_count": len(network),
            "role_counts": network["network_role"].value_counts().to_dict(),
            "output": str(output),
        },
    )
    return output


def download_basin_context(
    data_root: Path,
    huc4: str = SJ_BASIN_HUC4,
) -> dict[str, Path]:
    """Cache the small regional layers needed by station map labels."""

    context_root = data_root / "_context" / f"sj_basin_huc4_{huc4}"
    boundary_path = context_root / "basin_boundary.geojson"
    rivers_path = context_root / "main_rivers.geojson"
    places_path = context_root / "places.geojson"
    context_root.mkdir(parents=True, exist_ok=True)

    if boundary_path.exists():
        basin = gpd.read_file(boundary_path)
    else:
        basin = WBD("huc4").byids("huc4", huc4)
        basin.to_file(boundary_path, driver="GeoJSON")
    if basin.empty:
        raise ValueError(f"No HUC4 boundary returned for {huc4}")
    geometry = basin.geometry.iloc[0]

    if rivers_path.exists():
        rivers = gpd.read_file(rivers_path)
    else:
        rivers = GeoConnex("mainstems").bygeometry(geometry, predicate="intersects")
        rivers.to_file(rivers_path, driver="GeoJSON")

    if places_path.exists():
        places = gpd.read_file(places_path)
    else:
        places = GeoConnex("places").bygeometry(geometry, predicate="intersects")
        places.to_file(places_path, driver="GeoJSON")

    write_json(
        context_root / "context_manifest.json",
        {
            "huc4": huc4,
            "basin_boundary": str(boundary_path),
            "main_rivers": str(rivers_path),
            "places": str(places_path),
            "basin_boundary_source": "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer",
            "main_rivers_source": "https://geoconnex.us/collections/mainstems",
            "places_source": "https://geoconnex.us/collections/places",
            "river_count": len(rivers),
            "place_count": len(places),
            "retrieved_at": datetime.now(timezone.utc),
        },
    )
    return {
        "basin_boundary": boundary_path,
        "main_rivers": rivers_path,
        "places": places_path,
    }
