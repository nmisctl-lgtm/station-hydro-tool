"""Read-only adapter for independent station analysis packages.

The basin console remains a release reader.  This adapter is the small seam
between that console and the local-first ``station_hydro_tool`` package: it
reads already-produced station artifacts and never downloads, recalculates,
or mutates them.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


class StationPackageError(RuntimeError):
    """The requested station package is missing or malformed."""


class StationPackageNotFound(StationPackageError):
    """No independent station package exists for the requested station."""


def _read_json(path: Path, *, required: bool = False) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise StationPackageError(f"Station artifact is missing: {path.name}")
        return {}
    try:
        # A few historical station-tool summaries contain non-finite numeric
        # values emitted by Python's JSON encoder.  Treat those missing
        # statistics as null at the presentation boundary rather than making
        # the whole station page unavailable.
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _token: None)
    except (OSError, json.JSONDecodeError) as error:
        raise StationPackageError(f"Could not read station JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise StationPackageError(f"Station JSON is not an object: {path.name}")
    return value


def _read_csv(path: Path, *, required: bool = False) -> list[dict[str, str]]:
    if not path.is_file():
        if required:
            raise StationPackageError(f"Station artifact is missing: {path.name}")
        return []
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
    except OSError as error:
        raise StationPackageError(f"Could not read station CSV: {path.name}") from error


def _station_parts(location_key: str) -> tuple[str, str]:
    provider, separator, station_id = str(location_key).partition(":")
    if not separator:
        raise StationPackageNotFound(f"Station key must use PROVIDER:ID: {location_key}")
    provider = provider.strip().upper()
    station_id = station_id.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]+", provider) or not re.fullmatch(r"[A-Z0-9_-]+", station_id.upper()):
        raise StationPackageNotFound(f"No station package is registered for: {location_key}")
    return provider, station_id


class StationPackageReader:
    """Read one station package through a stable presentation contract."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.expanduser().resolve()

    def package_root(self, location_key: str) -> Path:
        provider, station_id = _station_parts(location_key)
        package_name = f"{provider}_{station_id}"
        legacy = self.data_root / package_name
        matches = sorted(
            path
            for path in self.data_root.rglob(package_name)
            if path.is_dir() and path.name == package_name
        ) if self.data_root.exists() else []
        if len(matches) > 1:
            locations = ", ".join(str(path) for path in matches)
            raise StationPackageError(f"Station package has multiple grouped locations: {locations}")
        root = (matches[0] if matches else legacy).resolve()
        if self.data_root not in root.parents or not root.is_dir():
            raise StationPackageNotFound(
                f"No independent station package is available for {provider}:{station_id}. "
                "Run the station_hydro_tool workflow first."
            )
        return root

    def _artifact(self, root: Path, relative: str) -> Path:
        artifact = (root / relative).resolve()
        if root not in artifact.parents:
            raise StationPackageError(f"Station artifact escaped package root: {relative}")
        return artifact

    def hydrology(self, location_key: str) -> dict[str, Any]:
        root = self.package_root(location_key)
        provider, station_id = _station_parts(location_key)
        hydrology = root / "hydrology"
        metadata = root / "metadata"
        observations = root / "observations"
        return {
            "provider": provider,
            "station_id": station_id,
            "summary": _read_json(self._artifact(root, "hydrology/hydrology_summary.json"), required=True),
            "baseflow_summary": _read_json(self._artifact(root, "hydrology/baseflow_summary.json")),
            "flood_frequency": _read_csv(self._artifact(root, "hydrology/flood_frequency.csv")),
            "flow_duration_quantiles": _read_csv(
                self._artifact(root, "hydrology/flow_duration_quantiles.csv")
            ),
            "annual_hydrology": _read_csv(self._artifact(root, "hydrology/annual_hydrology.csv")),
            "annual_runoff_exceedance": _read_csv(
                self._artifact(root, "hydrology/annual_runoff_exceedance.csv")
            ),
            "baseflow_annual": _read_csv(self._artifact(root, "hydrology/baseflow_annual.csv")),
            "monthly_hydrology": _read_csv(self._artifact(root, "hydrology/monthly_hydrology.csv")),
            "annual_peaks": _read_csv(self._artifact(root, "hydrology/annual_peak_summary.csv")),
            "availability": _read_csv(self._artifact(root, "metadata/availability_summary.csv")),
            "quality_summary": _read_csv(self._artifact(root, "quality/series_summary.csv")),
            "station_metadata": _read_json(self._artifact(root, "metadata/station_metadata.json")),
            "discovery_manifest": _read_json(self._artifact(root, "metadata/discovery_manifest.json")),
            "artifact_names": {
                "station_root": root.name,
                "daily_discharge": str(self._artifact(root, "observations/daily_discharge.parquet").relative_to(root)),
                "annual_peaks": str(self._artifact(root, "observations/annual_peak_discharge.parquet").relative_to(root)),
                "hydrology": str(hydrology.relative_to(root)),
                "watershed": str(self._artifact(root, "spatial/contributing_watershed.geojson").relative_to(root)),
                "baseflow_daily": str(self._artifact(root, "hydrology/baseflow_daily.parquet").relative_to(root)),
                "annual_runoff_exceedance": str(
                    self._artifact(root, "hydrology/annual_runoff_exceedance.csv").relative_to(root)
                ),
            },
        }

    def flow_duration_curve(self, location_key: str) -> list[dict[str, str]]:
        root = self.package_root(location_key)
        return _read_csv(self._artifact(root, "hydrology/flow_duration_curve.csv"), required=True)

    def watershed(self, location_key: str) -> dict[str, Any]:
        root = self.package_root(location_key)
        path = self._artifact(root, "spatial/contributing_watershed.geojson")
        if not path.is_file():
            raise StationPackageError("Contributing watershed GeoJSON is not available.")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StationPackageError("Could not read contributing watershed GeoJSON.") from error
        if not isinstance(value, dict):
            raise StationPackageError("Contributing watershed GeoJSON is not an object.")
        return value

    def flow_network(self, location_key: str) -> dict[str, Any]:
        """Return the station-specific local flow-network GeoJSON when present."""

        root = self.package_root(location_key)
        path = self._artifact(root, "spatial/flow_network.geojson")
        if not path.is_file():
            raise StationPackageError("Station flow-network GeoJSON is not available.")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StationPackageError("Could not read station flow-network GeoJSON.") from error
        if not isinstance(value, dict):
            raise StationPackageError("Station flow-network GeoJSON is not an object.")
        return value

    def local_daily(self, location_key: str, source_variable: str, start: str, end: str) -> dict[str, Any]:
        """Return daily values from the immutable station package.

        Daily discharge is read from the retained NWIS RDB download.  Gage
        height is reduced from the retained unit-value JSON chunks.  This is
        intentionally a small presentation adapter: it never writes or
        recalculates station artifacts, and the returned rows retain the
        source-variable and aggregation description.
        """

        root = self.package_root(location_key)
        code = str(source_variable).strip()
        if code == "00060":
            path = self._artifact(root, "raw/dv_00060_full.rdb")
            values: dict[str, list[float]] = defaultdict(list)
            if path.is_file():
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    parts = line.split("\t")
                    if len(parts) < 4 or parts[0] != "USGS":
                        continue
                    observed_date, raw_value = parts[2][:10], parts[3]
                    if start <= observed_date <= end:
                        try:
                            parsed_value = float(raw_value)
                        except ValueError:
                            continue
                        values[observed_date].append(parsed_value)
            rows = [
                {"observed_date": day, "value": sum(day_values) / len(day_values), "numeric_observation_count": len(day_values)}
                for day, day_values in sorted(values.items())
            ]
            return {"source_variable": code, "unit_canonical": "ft³/s", "statistic": "provider daily value", "points": rows}

        if code == "00065":
            values = defaultdict(list)
            raw_root = self._artifact(root, "raw/iv_00065")
            for path in sorted(raw_root.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for series in payload.get("value", {}).get("timeSeries", []):
                    for point in series.get("values", [{}])[0].get("value", []):
                        observed_date = str(point.get("dateTime", ""))[:10]
                        if not (start <= observed_date <= end):
                            continue
                        try:
                            parsed_value = float(point["value"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        values[observed_date].append(parsed_value)
            rows = [
                {"observed_date": day, "value": sum(day_values) / len(day_values), "numeric_observation_count": len(day_values)}
                for day, day_values in sorted(values.items())
            ]
            return {"source_variable": code, "unit_canonical": "ft", "statistic": "daily mean from unit values", "points": rows}

        raise StationPackageError(f"No local daily adapter is available for source variable {code}.")

    def field_stage_discharge(self, location_key: str, start: str, end: str) -> dict[str, Any]:
        """Return same-visit USGS field stage/discharge measurements."""

        root = self.package_root(location_key)
        by_visit: dict[str, dict[str, Any]] = {}
        for code, reading_types, output_key in (("00060", {"discharge"}, "discharge_value"), ("00065", {"meangageheight", "referenceprimary"}, "stage_value")):
            path = self._artifact(root, f"raw/field_measurements_{code}.json")
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for feature in payload.get("features", []):
                properties = feature.get("properties", {})
                visit = str(properties.get("field_visit_id") or "")
                timestamp = str(properties.get("time") or "")
                observed_date = timestamp[:10]
                if not visit or not (start <= observed_date <= end) or str(properties.get("reading_type", "")).casefold() not in reading_types:
                    continue
                try:
                    value = float(properties["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                row = by_visit.setdefault(visit, {"field_visit_id": visit, "observed_at_utc": timestamp, "observed_date": observed_date})
                row[output_key] = value
                row[output_key.replace("_value", "_unit")] = properties.get("unit_of_measure")
                row[output_key.replace("_value", "_quality")] = properties.get("measurement_rated")
        rows = [row for row in by_visit.values() if row.get("stage_value") is not None and row.get("discharge_value") is not None]
        rows.sort(key=lambda row: (row.get("observed_at_utc", ""), row.get("field_visit_id", "")))
        return {"source": "USGS field measurements", "start": start, "end": end, "points": rows}

    def baseflow_daily(self, location_key: str, start: str, end: str) -> dict[str, Any]:
        """Return the retained daily baseflow artifact for display."""

        root = self.package_root(location_key)
        path = self._artifact(root, "hydrology/baseflow_daily.parquet")
        if not path.is_file():
            raise StationPackageError("Station baseflow daily artifact is not available.")
        try:
            import pyarrow.parquet as parquet
        except ImportError as error:
            raise StationPackageError("The local runtime cannot read the retained baseflow Parquet artifact.") from error
        try:
            table = parquet.read_table(path, columns=["observed_date_local", "discharge_cfs", "baseflow_cfs", "baseflow_method"])
        except Exception as error:
            raise StationPackageError("Could not read the retained baseflow daily artifact.") from error
        columns = table.to_pydict()
        rows: list[dict[str, Any]] = []
        for date, discharge, baseflow, method in zip(
            columns.get("observed_date_local", []),
            columns.get("discharge_cfs", []),
            columns.get("baseflow_cfs", []),
            columns.get("baseflow_method", []),
        ):
            observed_date = date.isoformat() if hasattr(date, "isoformat") else str(date)[:10]
            if not (start <= observed_date <= end) or baseflow is None:
                continue
            rows.append({
                "observed_date": observed_date,
                "value": float(baseflow),
                "discharge_cfs": float(discharge) if discharge is not None else None,
                "method": method,
            })
        return {
            "source": "retained station-tool baseflow_daily.parquet",
            "start": start,
            "end": end,
            "unit_canonical": "ft³/s",
            "statistic": "Lyne-Hollick recursive digital filter output",
            "points": rows,
        }

    def stage_discharge(self, location_key: str, start: str, end: str) -> dict[str, Any]:
        """Return timestamp-aligned continuous stage and discharge pairs."""

        root = self.package_root(location_key)
        paths = {
            "stage": self._artifact(root, "observations/unit_00065.parquet"),
            "discharge": self._artifact(root, "observations/unit_00060.parquet"),
        }
        if not all(path.is_file() for path in paths.values()):
            raise StationPackageError(
                "Continuous stage and discharge observations are not both available."
            )
        try:
            import pyarrow.parquet as parquet
        except ImportError as error:
            raise StationPackageError(
                "The local runtime cannot read continuous observation Parquet files."
            ) from error

        values: dict[str, dict[str, tuple[str, float]]] = {"stage": {}, "discharge": {}}
        for name, path in paths.items():
            try:
                columns = parquet.read_table(
                    path, columns=["observed_at_utc", "observed_date_local", "value"]
                ).to_pydict()
            except Exception as error:
                raise StationPackageError(
                    f"Could not read {name} continuous observations."
                ) from error
            for observed_at, observed_date, value in zip(
                columns.get("observed_at_utc", []),
                columns.get("observed_date_local", []),
                columns.get("value", []),
            ):
                date_text = (
                    observed_date.isoformat()
                    if hasattr(observed_date, "isoformat")
                    else str(observed_date)[:10]
                )
                if not (start <= date_text <= end) or value is None:
                    continue
                timestamp = (
                    observed_at.isoformat()
                    if hasattr(observed_at, "isoformat")
                    else str(observed_at)
                )
                values[name][timestamp] = (date_text, float(value))

        points = [
            {
                "observed_at_utc": timestamp,
                "observed_date": values["stage"][timestamp][0],
                "stage_value": values["stage"][timestamp][1],
                "stage_unit": "ft",
                "discharge_value": values["discharge"][timestamp][1],
                "discharge_unit": "ft³/s",
            }
            for timestamp in sorted(set(values["stage"]) & set(values["discharge"]))
        ]
        return {"start": start, "end": end, "points": points}


__all__ = ["StationPackageError", "StationPackageNotFound", "StationPackageReader"]
