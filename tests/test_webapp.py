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
                "available_data": [],
            }
        ),
        encoding="utf-8",
    )
    (metadata_root / "availability_summary.json").write_text(
        json.dumps({"series": []}), encoding="utf-8"
    )
    figure_root = tmp_path / "outputs" / "USGS_09342500" / "figures"
    figure_root.mkdir(parents=True)
    (figure_root / "eda_coverage.png").write_bytes(b"png")
    (figure_root.parent / "figure_manifest.json").write_text(
        json.dumps(
            {"figures": {"coverage": {"png": "eda_coverage.png"}}}
        ),
        encoding="utf-8",
    )
    return tmp_path / "data" / "stations", tmp_path / "outputs"


def test_webapp_exposes_station_contract_and_artifacts(tmp_path) -> None:
    data_dir, output_dir = _write_minimal_package(tmp_path)
    client = TestClient(create_app(data_dir=data_dir, output_dir=output_dir))

    assert client.get("/api/v1/health").status_code == 200
    summary = client.get("/api/v1/stations/USGS-09342500")
    assert summary.status_code == 200
    assert summary.json()["station"]["name"] == "Test station"
    assert client.get("/station/USGS/09342500").status_code == 200
    assert client.get("/api/v1/stations/09342500/figures/eda_coverage.png").content == b"png"


def test_webapp_returns_a_clear_missing_package_error(tmp_path) -> None:
    client = TestClient(
        create_app(data_dir=tmp_path / "data", output_dir=tmp_path / "outputs")
    )
    response = client.get("/api/v1/stations/09342500")
    assert response.status_code == 404
    assert "09342500" in response.json()["detail"]
