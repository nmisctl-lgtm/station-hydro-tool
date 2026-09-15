from pathlib import Path

import pytest

from station_hydro.models import StationRequest
from station_hydro.paths import output_station_root, station_group_name, station_root


def test_station_root_resolves_grouped_package(tmp_path: Path) -> None:
    data_dir = tmp_path / "stations"
    grouped = data_dir / "Piedra_River_5" / "USGS_09347200"
    grouped.mkdir(parents=True)

    resolved = station_root(data_dir, StationRequest("09347200"))

    assert resolved == grouped
    assert station_group_name(resolved) == "Piedra_River_5"
    assert output_station_root(tmp_path / "outputs", resolved) == (
        tmp_path / "outputs" / "Piedra_River_5" / "USGS_09347200"
    )


def test_station_root_keeps_legacy_flat_fallback(tmp_path: Path) -> None:
    data_dir = tmp_path / "stations"
    legacy = data_dir / "USGS_09342000"
    legacy.mkdir(parents=True)

    resolved = station_root(data_dir, StationRequest("09342000"))

    assert resolved == legacy
    assert station_group_name(resolved) is None
    assert output_station_root(tmp_path / "outputs", resolved) == (
        tmp_path / "outputs" / "USGS_09342000"
    )


def test_station_root_resolves_nested_grouped_package(tmp_path: Path) -> None:
    data_dir = tmp_path / "stations"
    nested = data_dir / "Animas_River_9" / "Hermosa_Creek_1" / "USGS_09361000"
    nested.mkdir(parents=True)

    resolved = station_root(data_dir, StationRequest("09361000"))

    assert resolved == nested
    assert station_group_name(resolved) == "Hermosa_Creek_1"
    assert output_station_root(tmp_path / "outputs", resolved) == (
        tmp_path
        / "outputs"
        / "Animas_River_9"
        / "Hermosa_Creek_1"
        / "USGS_09361000"
    )


def test_station_root_rejects_duplicate_grouped_packages(tmp_path: Path) -> None:
    data_dir = tmp_path / "stations"
    for group in ("Piedra_River_5", "San_Juan_River_11"):
        (data_dir / group / "USGS_09347200").mkdir(parents=True)

    with pytest.raises(RuntimeError, match="multiple grouped locations"):
        station_root(data_dir, StationRequest("09347200"))
