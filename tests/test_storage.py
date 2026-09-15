import json

from station_hydro.storage import write_json


def test_write_json_converts_nonfinite_values_to_null(tmp_path):
    path = tmp_path / "summary.json"

    write_json(path, {"missing": float("nan"), "nested": [float("inf"), 1.0]})

    assert json.loads(path.read_text()) == {"missing": None, "nested": [None, 1.0]}
