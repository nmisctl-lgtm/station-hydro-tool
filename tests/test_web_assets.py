from pathlib import Path


STATION_JS = Path(__file__).resolve().parents[1] / "web" / "station.js"
STATION_HTML = Path(__file__).resolve().parents[1] / "web" / "station.html"


def test_stage_discharge_default_range_excludes_catalog_water_year_series() -> None:
    """The default paired-record window must come from actual observations."""

    source = STATION_JS.read_text(encoding="utf-8")

    assert (
        "const analyticalSeries = series.filter((seriesItem) => !isWaterYearSeries(seriesItem));"
        in source
    )
    assert "const selectedStageKey = $(\"#stage-series\").value;" in source
    assert "const stage = analyticalSeries.find((seriesItem) => seriesItem.series_key === selectedStageKey);" in source
    assert "const dischargePoints = analyticalSeries.find" in source


def test_stage_discharge_scales_without_spreading_a_large_pair_array() -> None:
    """A full continuous archive can exceed the JavaScript argument limit."""

    source = STATION_JS.read_text(encoding="utf-8")

    assert "Math.max(...usable.map((point) => point.discharge), 1)" not in source
    assert "for (const point of usable)" in source


def test_station_shell_uses_explicit_static_assets_without_a_basin_release() -> None:
    """A station route must not make broken relative or retired basin-release requests."""

    html = STATION_HTML.read_text(encoding="utf-8")
    source = STATION_JS.read_text(encoding="utf-8")

    assert 'href="/static/styles.css?v=' in html
    assert 'href="/static/vendor/maplibre-gl.css"' in html
    assert 'href="/static/station.css?v=' in html
    assert 'src="/static/station.js?v=' in html
    assert "/gis/hydrography/basin_boundary.geojson" not in source
    assert "MAP_REFERENCE_URLS" not in source
    assert "REGIONAL_CITIES" not in source
    assert 'String(feature.properties?.activity_status || "").toLowerCase() === "active"' not in source


def test_coverage_inventory_excludes_catalog_water_year_series_for_every_filter() -> None:
    """Water-Year catalog metadata must never re-enter a visual coverage view."""

    source = STATION_JS.read_text(encoding="utf-8")

    assert "const all = (state.hydrology?.availability?.length ? state.hydrology.availability : state.analysis.series || [])" in source
    assert ".filter((series) => !isWaterYearSeries(series));" in source
