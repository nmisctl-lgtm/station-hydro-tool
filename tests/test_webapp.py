import json

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
