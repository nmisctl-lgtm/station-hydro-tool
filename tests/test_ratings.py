from station_hydro.ratings import build_rating_inventory, parse_rating_header


def test_build_rating_inventory_flattens_rating_assets() -> None:
    table = build_rating_inventory(
        {
            "features": [
                {
                    "id": "USGS-09342500.exsa.rdb",
                    "properties": {
                        "monitoring_location_id": "USGS-09342500",
                        "file_type": "exsa",
                        "datetime": "2026-08-27T23:05:02Z",
                    },
                    "assets": {
                        "data": {
                            "href": "https://example.test/exsa.rdb",
                            "title": "Rating File",
                        }
                    },
                }
            ]
        }
    )
    assert len(table) == 1
    assert table.iloc[0]["file_type"] == "exsa"
    assert table.iloc[0]["asset_url"].endswith("exsa.rdb")


def test_parse_rating_header_extracts_current_rating_version() -> None:
    header = (
        '# //RATING ID="31.0" TYPE="STGQ" NAME="stage-discharge" AGING=Working\n'
        '# //RATING EXPANSION="logarithmic"\n'
        '# //RATING OFFSET1=3.170000E+00\n'
        '# //RATING SHIFTED="20260827230502 MST"\n'
        '# //RATING_DATETIME BEGIN=20251108103000 BZONE=-07:00 END=--------------\n'
    )
    result = parse_rating_header(header)
    assert result["rating_id"] == "31.0"
    assert result["rating_type"] == "STGQ"
    assert result["rating_expansion"] == "logarithmic"
    assert result["rating_datetime_begin"] == "2025-11-08T10:30:00"
    assert result["rating_datetime_begin_raw"] == "20251108103000"
