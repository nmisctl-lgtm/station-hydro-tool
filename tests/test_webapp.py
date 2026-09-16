import json
import csv
from pathlib import Path

from fastapi.testclient import TestClient

from station_hydro.webapp import create_app


def _write_minimal_package(tmp_path):
    data_root = tmp_path / "data" / "stations" / "USGS_09342500"
    metadata_root = data_root / "metadata"
    metadata_root.mkdir(parents=True)
    (metadata_root / "station_metadata.json").write_text(
        json.dumps(
            {
                "station_id": "09342500",
                "name": "Test station",
                "latitude": 37.2,
                "longitude": -107.0,
                "retrieved_at": "2026-09-15T12:34:56Z",
                "available_data": [],
            }
        ),
        encoding="utf-8",
    )
    (metadata_root / "availability_summary.json").write_text(
        json.dumps({"series": []}), encoding="utf-8"
    )
    return tmp_path / "data" / "stations", tmp_path / "outputs"


def test_webapp_serves_dynamic_overview_and_station_shell(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    assert client.get("/api/v1/health").status_code == 200
    summary = client.get("/api/v1/stations/USGS-09342500")
    assert summary.status_code == 200
    assert summary.json()["station"]["name"] == "Test station"
    overview = client.get("/")
    assert overview.status_code == 200
    assert "Basin Overview" in overview.text
    assert "app.js?v=20260916-2" in overview.text
    assert overview.headers["cache-control"] == "no-store, max-age=0"
    assert client.get("/static/app.js?v=20260916-2").headers["cache-control"] == "no-store, max-age=0"
    station = client.get("/station/USGS/09342500")
    assert station.status_code == 200
    assert "Hydrograph" in station.text
    assert "station.js" in station.text


def test_webapp_lists_only_locally_cached_stations(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    response = client.get("/api/v1/stations?limit=50")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "location_key": "USGS:09342500",
            "provider_station_id": "09342500",
            "source_name": "USGS",
            "display_name": "Test station",
            "latitude": 37.2,
            "longitude": -107.0,
            "coordinate_status": "valid",
            "activity_status": "local_cached",
        }
    ]


def test_webapp_exposes_cached_stations_as_dynamic_map_features(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    response = client.get("/api/v1/map/stations")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-107.0, 37.2]},
                "properties": {
                    "location_key": "USGS:09342500",
                    "provider_station_id": "09342500",
                    "source_name": "USGS",
                    "display_name": "Test station",
                    "coordinate_status": "valid",
                    "activity_status": "local_cached",
                },
            }
        ],
    }


def test_webapp_summarizes_the_local_dynamic_overview(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    response = client.get("/api/v1/overview")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "mode": "local_dynamic_cache",
        "registered_location_count": 1,
        "mapped_location_count": 1,
        "coordinate_review_count": 0,
        "daily_observation_count": 0,
    }


def test_webapp_adapts_a_local_package_for_dynamic_station_analysis(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    response = client.get("/api/v1/stations/USGS:09342500/analysis")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "station": {
            "location_key": "USGS:09342500",
            "provider_station_id": "09342500",
            "source_name": "USGS",
            "display_name": "Test station",
            "latitude": 37.2,
            "longitude": -107.0,
            "coordinate_status": "valid",
            "activity_status": "local_cached",
            "station_type": None,
            "state_name": None,
            "huc": None,
            "timezone": None,
            "drainage_area_sq_mi": None,
            "latest_observed_at": None,
            "latest_retrieved_at": "2026-09-15T12:34:56Z",
            "local_snapshot_id": "USGS:09342500@2026-09-15T12:34:56Z",
        },
        "series": [],
        "monthly": [],
        "month_of_year": [],
        "quality_event_summary": [],
    }


def test_webapp_reads_dynamic_daily_series_from_a_local_package(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    root = data_dir / "USGS_09342500"
    (root / "raw").mkdir()
    (root / "raw" / "dv_00060_full.rdb").write_text(
        "USGS\t09342500\t2025-01-02\t12.5\n", encoding="utf-8"
    )
    (root / "metadata" / "availability_summary.json").write_text(
        json.dumps(
            {
                "series": [
                    {
                        "series_id": "USGS:09342500:dv:00060:00003:1",
                        "variable": "Discharge",
                        "parameter_code": "00060",
                        "provider_data_type": "dv",
                        "frequency": "daily",
                        "unit": "ft3/s",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    response = client.get(
        "/api/v1/series/USGS:09342500:dv:00060:00003:1/daily?"
        "start=2025-01-01&end=2025-01-31&statistic=mean"
    )

    assert response.status_code == 200
    assert response.json()["data"]["points"] == [
        {
            "observed_date": "2025-01-02",
            "value": 12.5,
            "numeric_observation_count": 1,
        }
    ]


def test_webapp_returns_a_clear_missing_package_error(tmp_path) -> None:
    client = TestClient(
        create_app(data_dir=tmp_path / "data", output_dir=tmp_path / "outputs")
    )
    response = client.get("/api/v1/stations/09342500")
    assert response.status_code == 404
    assert "09342500" in response.json()["detail"]


def _write_display_release(tmp_path: Path) -> Path:
    release = tmp_path / "overview" / "basin-overview-test"
    (release / "console").mkdir(parents=True)
    (release / "gis" / "hydrography").mkdir(parents=True)
    (release / "release_manifest.json").write_text(
        json.dumps({"release_id": "basin-overview-test", "generated_at_utc": "2026-09-15T00:00:00Z"}),
        encoding="utf-8",
    )
    (release / "console" / "overview.json").write_text(
        json.dumps({
            "release_id": "basin-overview-test",
            "generated_at_utc": "2026-09-15T00:00:00Z",
            "registered_location_count": 1,
            "mapped_location_count": 1,
            "coordinate_review_count": 0,
            "daily_observation_count": 12,
        }),
        encoding="utf-8",
    )
    row = {
        "location_key": "USGS:09342500",
        "source_name": "USGS",
        "provider_station_id": "09342500",
        "display_name": "TEST RIVER",
        "station_type": "Stream Gage",
        "latitude": "37.2",
        "longitude": "-107.0",
        "coordinate_status": "valid",
        "state_name": "Colorado",
        "huc": "140801",
        "archive_presence": "present",
        "activity_status": "active",
        "hydro_numeric_observation_count": "12",
        "qa_candidate_series_count": "0",
    }
    with (release / "console" / "station_status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (release / "console" / "stations.geojson").write_text(
        json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-107.0, 37.2]}, "properties": row}],
        }),
        encoding="utf-8",
    )
    for name, content in (("qa_series_summary.csv", ""), ("update_runs.csv", "")):
        (release / "console" / name).write_text(content, encoding="utf-8")
    (release / "gis" / "hydrography" / "basin_boundary.geojson").write_text(
        '{"type":"FeatureCollection","features":[]}', encoding="utf-8"
    )
    return release


def test_webapp_adapts_the_display_overview_release(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    release = _write_display_release(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir, overview_dir=release))

    overview = client.get("/api/v1/overview")
    assert overview.status_code == 200
    assert overview.json()["release_id"] == "basin-overview-test"
    assert overview.json()["data"]["registered_location_count"] == 1
    assert client.get("/api/v1/stations?limit=5").json()["data"][0]["location_key"] == "USGS:09342500"
    assert client.get("/gis/hydrography/basin_boundary.geojson").status_code == 200


def test_viewing_a_station_refreshes_daily_once_per_day(tmp_path, monkeypatch) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    metadata_path = data_dir / "USGS_09342500" / "metadata" / "station_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["available_data"] = [{"frequency": "daily", "parameter_code": "00060", "variable": "Discharge"}]
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    calls = []

    def fake_run(options):
        calls.append(options)
        return {"daily": data_dir / "USGS_09342500" / "raw" / "dv_00060_full.rdb"}

    monkeypatch.setattr("station_hydro.webapp.refresh_station_daily", fake_run)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    assert client.get("/api/v1/stations/USGS:09342500/analysis").status_code == 200
    assert client.get("/api/v1/stations/USGS:09342500/analysis").status_code == 200
    assert len(calls) == 1
    marker = json.loads((data_dir / "USGS_09342500" / "metadata" / "daily_update_state.json").read_text())
    assert marker["status"] == "updated"
    assert calls[0].refresh is True
    assert calls[0].with_continuous is False
