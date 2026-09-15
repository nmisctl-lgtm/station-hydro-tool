from pathlib import Path

from station_hydro.narrative import _load_catalog_context


def test_load_catalog_context_finds_project_directory() -> None:
    station_root = Path(
        "/Users/tliu4/Documents/ChatGPT/San Juan Digital Twin/"
        "station_hydro_tool/data/stations/San_Juan_River_11/USGS_09379500"
    )
    context = _load_catalog_context(station_root)
    assert context is not None
    _, payload, entry = context
    assert payload["station_count"] == len(payload["stations"])
    assert payload["station_count"] >= 23
    assert entry["station_id"] == "09379500"
    assert entry["rank"] == 1
