import json

from station_hydro.service import load_station_snapshot


def test_load_station_snapshot_reads_metadata_and_artifact_contract(tmp_path) -> None:
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
        json.dumps(
            {
                "station_id": "09342500",
                "series": [
                    {
                        "variable": "Discharge",
                        "frequency": "daily",
                        "local_status": "downloaded",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "outputs" / "USGS_09342500" / "figures"
    output_root.mkdir(parents=True)
    (output_root / "eda_coverage.png").write_bytes(b"png")
    (output_root.parent / "figure_manifest.json").write_text(
        json.dumps(
            {
                "figures": {
                    "coverage": {
                        "png": "outputs/USGS_09342500/figures/eda_coverage.png",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_station_snapshot(
        "USGS-09342500",
        data_dir=tmp_path / "data" / "stations",
        output_dir=tmp_path / "outputs",
    )

    assert snapshot["station"]["name"] == "Test station"
    assert snapshot["available_data"][0]["local_status"] == "downloaded"
    assert snapshot["figures"]["coverage"]["png"].endswith("/figures/eda_coverage.png")


def test_load_station_snapshot_requires_a_local_package(tmp_path) -> None:
    try:
        load_station_snapshot("09342500", data_dir=tmp_path / "data")
    except FileNotFoundError as exc:
        assert "09342500" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("missing station package should be explicit")
