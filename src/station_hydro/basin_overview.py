"""Read-only adapter for the display portion of a basin Overview release.

The standalone station project deliberately consumes the small, browser-facing
``console`` and ``gis`` products.  The release's analytical SQLite database is
not required for the Overview map and register, so it stays out of the normal
station-tool runtime and transfer bundle.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote


class BasinOverviewError(RuntimeError):
    """The selected display release cannot answer an Overview request."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BasinOverviewError(f"Could not read Overview JSON: {path}") from error
    if not isinstance(value, dict):
        raise BasinOverviewError(f"Overview JSON is not an object: {path}")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
    except OSError as error:
        raise BasinOverviewError(f"Could not read Overview CSV: {path}") from error


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _one(parameters: dict[str, list[str]], name: str) -> str | None:
    values = parameters.get(name)
    return values[0] if values else None


def _truthy(parameters: dict[str, list[str]], name: str) -> bool:
    return _one(parameters, name) == "true"


@dataclass
class BasinOverviewReader:
    """Small presentation read model over a basin Overview display release."""

    release_path: Path
    station_data_root: Path
    manifest: dict[str, Any]
    overview: dict[str, Any]
    station_status: list[dict[str, str]]
    qa_series_summary: list[dict[str, str]]
    update_runs: list[dict[str, str]]
    stations_geojson: dict[str, Any]
    matrix_cache: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)

    @classmethod
    def open(cls, release_path: Path, station_data_root: Path) -> "BasinOverviewReader":
        root = release_path.expanduser().resolve()
        required = [
            root / "release_manifest.json",
            root / "console" / "overview.json",
            root / "console" / "station_status.csv",
            root / "console" / "stations.geojson",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise BasinOverviewError(
                "Overview display release is incomplete; missing " + ", ".join(missing)
            )
        return cls(
            release_path=root,
            station_data_root=station_data_root.expanduser().resolve(),
            manifest=_read_json(root / "release_manifest.json"),
            overview=_read_json(root / "console" / "overview.json"),
            station_status=_read_csv(root / "console" / "station_status.csv"),
            qa_series_summary=_read_csv(root / "console" / "qa_series_summary.csv"),
            update_runs=_read_csv(root / "console" / "update_runs.csv"),
            stations_geojson=_read_json(root / "console" / "stations.geojson"),
        )

    @property
    def release_id(self) -> str:
        return str(self.manifest.get("release_id") or self.overview.get("release_id") or "unknown")

    def envelope(self, data: Any, *, warnings: list[str] | None = None) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "generated_at_utc": self.manifest.get("generated_at_utc")
            or self.overview.get("generated_at_utc"),
            "data": data,
            "warnings": warnings or [],
        }

    def _filtered_rows(self, parameters: dict[str, list[str]]) -> list[dict[str, str]]:
        exact = {
            "source_name": _one(parameters, "source"),
            "coordinate_status": _one(parameters, "coordinate_status"),
            "state_name": _one(parameters, "state"),
            "station_type": _one(parameters, "station_type"),
            "huc": _one(parameters, "huc"),
            "archive_presence": _one(parameters, "archive_presence"),
            "activity_status": _one(parameters, "activity_status"),
            "station_kind": _one(parameters, "station_kind"),
            "variable_profile": _one(parameters, "variable_profile"),
        }
        coordinate_status = exact["coordinate_status"]
        if coordinate_status == "review":
            exact["coordinate_status"] = None
        rows = [
            row
            for row in self.station_status
            if all(value is None or row.get(field) == value for field, value in exact.items())
        ]
        if coordinate_status == "review":
            rows = [row for row in rows if row.get("coordinate_status") != "valid"]
        if not _truthy(parameters, "include_inactive"):
            rows = [row for row in rows if row.get("activity_status") == "active"]
        if not _truthy(parameters, "include_non_hydro"):
            rows = [
                row
                for row in rows
                if (_number(row.get("hydro_numeric_observation_count")) or 0) > 0
            ]
        search = (_one(parameters, "search") or "").casefold().strip()
        if search:
            rows = [
                row
                for row in rows
                if any(
                    search in str(row.get(field) or "").casefold()
                    for field in ("location_key", "provider_station_id", "display_name")
                )
            ]
        quality_state = _one(parameters, "quality_state")
        if quality_state == "candidate":
            rows = [
                row
                for row in rows
                if (_number(row.get("qa_candidate_series_count")) or 0) > 0
            ]
        return rows

    def overview_data(self, parameters: dict[str, list[str]]) -> dict[str, Any]:
        rows = self._filtered_rows(parameters)
        mapped = sum(row.get("coordinate_status") == "valid" for row in rows)
        return {
            **self.overview,
            "registered_location_count": len(rows),
            "mapped_location_count": mapped,
            "coordinate_review_count": len(rows) - mapped,
            "archived_location_count": sum(
                row.get("archive_presence") == "present" for row in rows
            ),
            "display_only": True,
            "database_loaded": False,
        }

    def stations(self, parameters: dict[str, list[str]]) -> list[dict[str, str]]:
        try:
            limit = max(1, min(5000, int(_one(parameters, "limit") or 500)))
            offset = max(0, int(_one(parameters, "offset") or 0))
        except ValueError as error:
            raise BasinOverviewError("limit and offset must be integers") from error
        return self._filtered_rows(parameters)[offset : offset + limit]

    def map_stations(self, parameters: dict[str, list[str]]) -> dict[str, Any]:
        wanted = {
            row.get("location_key") for row in self._filtered_rows(parameters)
        }
        features = self.stations_geojson.get("features")
        if not isinstance(features, list):
            raise BasinOverviewError("Overview stations GeoJSON has no features array")
        return {
            "type": "FeatureCollection",
            "features": [
                feature
                for feature in features
                if isinstance(feature, dict)
                and isinstance(feature.get("properties"), dict)
                and feature["properties"].get("location_key") in wanted
            ],
        }

    def context_overview(self) -> dict[str, Any]:
        context_base = (self.station_data_root / "_context").resolve()
        candidates = sorted(
            path
            for path in context_base.glob("sj_basin_huc4_*/basin_overview_*")
            if path.is_dir() and (path / "layer_manifest.json").is_file()
        ) if context_base.is_dir() else []
        if not candidates:
            return {"available": False, "manifest": {}, "paths": {}}
        root = candidates[-1]
        root_relative = root.relative_to(context_base).as_posix()
        basin_relative = root.parent.relative_to(context_base).as_posix()
        return {
            "available": True,
            "manifest": _read_json(root / "layer_manifest.json"),
            "paths": {
                "basin_boundary": f"/context/{basin_relative}/basin_boundary.geojson",
                "state_boundaries": f"/context/{root_relative}/state_boundaries.geojson",
                "major_rivers": f"/context/{root_relative}/major_rivers.geojson",
                "major_towns": f"/context/{root_relative}/major_towns.geojson",
                "major_nid_facilities": f"/context/{root_relative}/major_nid_facilities.geojson",
            },
        }

    def qa_series(self, parameters: dict[str, list[str]]) -> list[dict[str, str]]:
        rows = self.qa_series_summary
        source = _one(parameters, "source")
        location_key = _one(parameters, "location_key")
        if source:
            rows = [row for row in rows if row.get("source_name") == source]
        if location_key:
            provider_id = location_key.split(":", maxsplit=1)[-1]
            rows = [row for row in rows if row.get("provider_station_id") == provider_id]
        return rows

    def quality_matrix(self, parameters: dict[str, list[str]]) -> dict[str, Any]:
        """Build a presence matrix from local daily packages, if available.

        The full basin release matrix was computed from its SQLite database.
        For this standalone project, local package records are the intentional
        source of truth, so the matrix describes the stations actually copied
        into ``data/stations``.
        """

        try:
            months_requested = max(1, min(36, int(_one(parameters, "months") or 12)))
        except ValueError as error:
            raise BasinOverviewError("months must be an integer") from error
        if months_requested not in self.matrix_cache:
            from .presentation import local_quality_matrix

            self.matrix_cache[months_requested] = local_quality_matrix(
                self.station_data_root, months_requested
            )
        return self.matrix_cache[months_requested]

    def source_archive_status(self) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in self.station_status:
            source = row.get("source_name") or "Unknown source"
            item = grouped.setdefault(
                source,
                {"source_name": source, "artifact_count": 0, "failed_request_evidence_count": 0},
            )
            item["artifact_count"] += 1
            retrieved = row.get("latest_retrieved_at") or ""
            if retrieved >= str(item.get("latest_retrieved_at_utc") or ""):
                item["latest_retrieved_at_utc"] = retrieved or None
                item["latest_artifact_id"] = None
                item["latest_artifact_sha256"] = None
            item["failed_request_evidence_count"] = 0
            item["failure_count_scope"] = "display release has no normalized request-failure table"
        now = datetime.now(timezone.utc)
        for item in grouped.values():
            retrieved = item.get("latest_retrieved_at_utc")
            age = None
            if retrieved:
                try:
                    parsed = datetime.fromisoformat(str(retrieved).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    age = max(0, int((now - parsed).total_seconds()))
                except ValueError:
                    pass
            item["response_age_seconds"] = age
        return sorted(grouped.values(), key=lambda item: str(item["source_name"]))

    def update_runs_data(self) -> list[dict[str, str]]:
        return self.update_runs

    def source_artifact(self, artifact_id: str) -> dict[str, Any]:
        raise BasinOverviewError(
            f"Artifact {unquote(artifact_id)} is not exposed by the display-only release adapter"
        )

    def release_metadata(self) -> dict[str, Any]:
        manifest = {**self.manifest}
        path = self.release_path / "release_manifest.json"
        manifest["manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return manifest
