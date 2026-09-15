"""USGS stage-discharge rating snapshots and provenance."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .discovery import USGSProvider
from .models import StationRequest
from .paths import station_root as resolve_station_root
from .storage import write_json


USGS_RATINGS_STAC_URL = "https://api.waterdata.usgs.gov/stac/v0/search"


def build_rating_inventory(payload: dict[str, Any]) -> pd.DataFrame:
    """Flatten STAC rating items to one row per rating asset."""

    rows: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        properties = feature.get("properties", {})
        asset = (feature.get("assets") or {}).get("data", {})
        if not asset.get("href"):
            continue
        rows.append(
            {
                "item_id": feature.get("id"),
                "monitoring_location_id": properties.get("monitoring_location_id"),
                "file_type": properties.get("file_type"),
                "item_datetime": properties.get("datetime"),
                "asset_title": asset.get("title"),
                "asset_description": asset.get("description"),
                "asset_url": asset.get("href"),
            }
        )
    return pd.DataFrame(rows)


def _safe_filename(item_id: str | None, fallback: str) -> str:
    value = item_id or fallback
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def parse_rating_header(text: str) -> dict[str, str | None]:
    """Extract current rating metadata from the USGS RDB header."""

    def first(pattern: str) -> str | None:
        match = re.search(pattern, text, flags=re.MULTILINE)
        return match.group(1) if match else None

    raw_begin = first(r"^# //RATING_DATETIME BEGIN=([^\s]+)")
    formatted_begin = raw_begin
    if raw_begin and re.fullmatch(r"\d{14}", raw_begin):
        formatted_begin = datetime.strptime(raw_begin, "%Y%m%d%H%M%S").isoformat()
    return {
        "rating_id": first(r'^# //RATING ID="([^"]+)"'),
        "rating_type": first(r'^# //RATING ID="[^"]+" TYPE="([^"]+)"'),
        "rating_name": first(r'^# //RATING ID="[^"]+" TYPE="[^"]+" NAME="([^"]+)"'),
        "rating_expansion": first(r"^# //RATING EXPANSION=\"([^\"]+)\""),
        "rating_offset1": first(r"^# //RATING OFFSET1=([^\s]+)"),
        "rating_shifted": first(r'^# //RATING SHIFTED="([^"]+)"'),
        "rating_datetime_begin": formatted_begin,
        "rating_datetime_begin_raw": raw_begin,
    }


def fetch_rating_curves(
    request: StationRequest,
    data_root: Path,
    timeout_seconds: int = 120,
) -> Path:
    """Retrieve current USGS rating assets and persist checksummed snapshots."""

    provider = USGSProvider(data_root, timeout_seconds=timeout_seconds)
    station_root = resolve_station_root(data_root, request)
    raw_root = station_root / "raw" / "ratings"
    stac_path = raw_root / "ratings_stac.json"
    params = {
        "collection": "ratings",
        "filter": f"monitoring_location_id='USGS-{request.station_id}'",
        "limit": "10000",
    }
    text, stac_artifact = provider._get_raw(
        USGS_RATINGS_STAC_URL,
        params,
        stac_path,
        reuse_existing=not request.refresh,
        fallback_on_rate_limit=True,
    )
    payload = json.loads(text)
    inventory = build_rating_inventory(payload)
    artifacts: list[dict[str, Any]] = [stac_artifact]
    local_paths: list[str | None] = []
    version_rows: list[dict[str, Any]] = []
    for row in inventory.to_dict(orient="records"):
        local_path = raw_root / _safe_filename(row.get("item_id"), "rating.rdb")
        rating_text, artifact = provider._get_raw(
            str(row["asset_url"]),
            {},
            local_path,
            reuse_existing=not request.refresh,
            fallback_on_rate_limit=True,
        )
        artifacts.append(artifact)
        local_paths.append(str(local_path))
        header = parse_rating_header(rating_text)
        # The raw 14-digit timestamp is preserved in the RDB file. Keep the
        # normalized ISO value in CSV so spreadsheet readers do not coerce it
        # into scientific notation.
        header.pop("rating_datetime_begin_raw", None)
        version_rows.append(
            {
                **row,
                **header,
                "local_path": str(local_path),
                "sha256": artifact.get("sha256"),
                "retrieved_at": datetime.now(timezone.utc),
            }
        )
    if not inventory.empty:
        inventory["local_path"] = local_paths
        inventory["retrieved_at"] = datetime.now(timezone.utc)
        inventory["sha256"] = [artifact.get("sha256") for artifact in artifacts[1:]]
    metadata_root = station_root / "metadata"
    metadata_root.mkdir(parents=True, exist_ok=True)
    inventory.to_csv(metadata_root / "rating_curves.csv", index=False)
    pd.DataFrame(version_rows).to_csv(
        metadata_root / "rating_version_summary.csv", index=False
    )
    write_json(
        metadata_root / "rating_curves_manifest.json",
        {
            "station_id": request.station_id,
            "retrieved_at": datetime.now(timezone.utc),
            "source_url": USGS_RATINGS_STAC_URL,
            "rating_count": len(inventory),
            "artifacts": artifacts,
            "output": str(metadata_root / "rating_curves.csv"),
            "version_output": str(metadata_root / "rating_version_summary.csv"),
        },
    )
    return metadata_root / "rating_curves.csv"
