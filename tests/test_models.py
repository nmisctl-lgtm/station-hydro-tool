from datetime import date

import pytest

from station_hydro.models import StationRequest, normalize_station_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("09342500", "09342500"),
        ("USGS-09342500", "09342500"),
        ("USGS:09342500", "09342500"),
        (" usgs:09342500 ", "09342500"),
        ("093710009", "093710009"),
    ],
)
def test_normalize_station_id(value: str, expected: str) -> None:
    assert normalize_station_id(value) == expected
    assert StationRequest(value).station_id == expected


@pytest.mark.parametrize(
    "value", ["9342500", "0934250A", "CO-09342500", "", "1234567890123456"]
)
def test_reject_invalid_station_id(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_station_id(value)
