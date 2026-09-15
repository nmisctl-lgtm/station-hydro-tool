import json
from datetime import date

from station_hydro.catalog import (
    build_station_directory_from_local,
    load_local_discovery_result,
    load_seed_catalog,
)
from station_hydro.models import AvailableDataRecord, StationMetadata
from station_hydro.catalog import _importance_profile, _validate_seed
from station_hydro.discovery import DiscoveryResult


def _record(data_type: str, parameter_code: str | None, start: date, end: date) -> AvailableDataRecord:
    return AvailableDataRecord(
        series_id=f"USGS:09342500:{data_type}:{parameter_code or 'none'}",
        variable="Discharge",
        data_type=data_type,
        provider_data_type=data_type,
        frequency="daily" if data_type == "dv" else "unit",
        parameter_code=parameter_code,
        declared_start=start,
        declared_end=end,
        source_url="https://waterdata.usgs.gov/monitoring-location/USGS-09342500",
    )


def test_load_seed_catalog_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps(
            [
                {"gage_id": "09342500", "variables": {}},
                {"gage_id": "09342500", "variables": {}},
            ]
        ),
        encoding="utf-8",
    )
    try:
        load_seed_catalog(path)
    except ValueError as exc:
        assert "Duplicate station ID" in str(exc)
    else:
        raise AssertionError("duplicate seed IDs should fail")


def test_importance_profile_explains_recent_mainstem() -> None:
    station = StationMetadata(
        station_id="09342500",
        name="SAN JUAN RIVER AT PAGOSA SPRINGS, CO",
        drainage_area_sq_mi=281,
    )
    records = [
        _record("dv", "00060", date(1935, 10, 1), date(2026, 8, 30)),
        _record("uv", "00060", date(1987, 5, 22), date(2026, 8, 30)),
        _record("uv", "00065", date(2020, 9, 17), date(2026, 8, 30)),
        _record("pk", None, date(1911, 7, 2), date(2025, 5, 14)),
    ]
    profile = _importance_profile(station, records, date(2026, 9, 1))
    assert profile["basin_role"] == "San Juan mainstem"
    assert profile["operational_status_screen"] == "recent_data"
    assert profile["importance_tier"] == "A"
    assert profile["importance_reasons"]


def test_importance_profile_separates_east_fork_from_mainstem() -> None:
    station = StationMetadata(
        station_id="09340000",
        name="EAST FORK SAN JUAN RIVER NR PAGOSA SPRINGS, CO",
        drainage_area_sq_mi=86.9,
    )
    records = [
        _record("dv", "00060", date(1935, 10, 1), date(1980, 9, 29)),
        _record("pk", None, date(1935, 6, 14), date(1980, 6, 9)),
    ]
    profile = _importance_profile(station, records, date(2026, 9, 1))
    assert profile["basin_role"] == "East/West Fork San Juan tributary"


def test_importance_profile_identifies_wolf_creek() -> None:
    station = StationMetadata(
        station_id="09341200",
        name="WOLF CREEK NEAR PAGOSA SPRINGS, CO.",
        drainage_area_sq_mi=64.0,
    )
    records = [_record("dv", "00060", date(1968, 10, 1), date(1975, 9, 29))]
    profile = _importance_profile(station, records, date(2026, 9, 1))
    assert profile["basin_role"] == "Wolf Creek upper-basin tributary"


def test_importance_profile_identifies_turkey_creek() -> None:
    station = StationMetadata(
        station_id="09342000",
        name="TURKEY CREEK NEAR PAGOSA SPRINGS, CO.",
        drainage_area_sq_mi=23.0,
    )
    records = [_record("dv", "00060", date(1937, 5, 1), date(1949, 9, 29))]
    profile = _importance_profile(station, records, date(2026, 9, 1))
    assert profile["basin_role"] == "Turkey Creek upper-basin tributary"


def test_importance_profile_identifies_piedra_river() -> None:
    station = StationMetadata(
        station_id="09347200",
        name="MIDDLE FORK PIEDRA RIVER NR PAGOSA SPRINGS, CO.",
        drainage_area_sq_mi=32.2,
    )
    records = [_record("dv", "00060", date(1969, 10, 1), date(1975, 9, 29))]
    profile = _importance_profile(station, records, date(2026, 9, 1))
    assert profile["basin_role"] == "Piedra River major tributary"


def test_validation_detects_seed_series_mismatch() -> None:
    station = StationMetadata(
        station_id="09342500",
        name="SAN JUAN RIVER AT PAGOSA SPRINGS, CO",
        latitude=37.2655,
        longitude=-107.011,
    )
    result = DiscoveryResult(
        station=station,
        available_data=[_record("dv", "00060", date(1935, 10, 1), date(2026, 8, 30))],
        source_artifacts=[],
        retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    seed = {
        "name": "SAN JUAN RIVER AT PAGOSA SPRINGS, CO",
        "latitude": 37.2655,
        "longitude": -107.011,
        "variables": {
            "discharge": {"daily": {"time_series_id": "x"}, "15min": {"time_series_id": "y"}},
            "stage": {"daily": None, "15min": None},
        },
    }
    validation = _validate_seed(seed, result)
    assert validation["status"] == "review"
    assert "discharge/15min" in validation["series_mismatches"]


def test_local_catalog_reads_cached_package_without_network(tmp_path) -> None:
    data_root = tmp_path / "stations"
    package = data_root / "San_Juan_River_1" / "USGS_09342500"
    metadata = package / "metadata"
    metadata.mkdir(parents=True)
    station_payload = {
        "station_id": "09342500",
        "provider": "USGS",
        "name": "SAN JUAN RIVER AT PAGOSA SPRINGS, CO",
        "latitude": 37.2655,
        "longitude": -107.011,
        "drainage_area_sq_mi": 281.0,
        "retrieved_at": "2026-09-08T00:00:00+00:00",
    }
    record = {
        "series_id": "USGS:09342500:dv:00060:00003:19544",
        "variable": "Discharge",
        "data_type": "Daily values",
        "provider_data_type": "dv",
        "frequency": "daily",
        "parameter_code": "00060",
        "declared_start": "1935-10-01",
        "declared_end": "2026-09-07",
        "provider_count": 33215,
        "analysis_role": "core",
    }
    (metadata / "station_metadata.json").write_text(json.dumps(station_payload), encoding="utf-8")
    (metadata / "available_data.json").write_text(
        json.dumps({"station_id": "09342500", "series": [record]}),
        encoding="utf-8",
    )
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            [{
                "gage_id": "09342500",
                "name": station_payload["name"],
                "latitude": station_payload["latitude"],
                "longitude": station_payload["longitude"],
                "variables": {"discharge": {"daily": {}}},
            }]
        ),
        encoding="utf-8",
    )

    result = load_local_discovery_result(data_root, "09342500")
    assert result.station.name == station_payload["name"]
    assert result.available_data[0].declared_start == date(1935, 10, 1)

    output = tmp_path / "catalog.json"
    paths = build_station_directory_from_local(seed, data_root, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["station_count"] == 1
    assert payload["source_mode"] == "local_cached_discovery"
    assert paths["audit_json"].exists()
