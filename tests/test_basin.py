import geopandas as gpd
from shapely.geometry import Polygon

from station_hydro.basin import summarize_basin


def test_summarize_basin_reports_geometry_and_area() -> None:
    basin = gpd.GeoDataFrame(
        {"identifier": ["USGS-09342500"]},
        geometry=[Polygon([(-107.0, 37.0), (-106.99, 37.0), (-106.99, 37.01), (-107.0, 37.0)])],
        crs="EPSG:4326",
    )
    summary = summarize_basin(basin, provider_drainage_area_sq_mi=281.0)
    assert summary["source"] == "USGS NLDI"
    assert summary["geometry_valid"] is True
    assert summary["area_sq_mi_albers_5070"] > 0
    assert summary["area_difference_pct"] is not None
