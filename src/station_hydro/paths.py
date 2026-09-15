"""Stable path resolution for grouped station packages."""

from __future__ import annotations

from pathlib import Path
import re

from .models import StationRequest


_GROUP_NAME = re.compile(r".+_[0-9]+$")


def station_package_name(request: StationRequest) -> str:
    """Return the provider-prefixed station package directory name."""

    return f"{request.provider}_{request.station_id}"


def station_root(data_dir: Path, request: StationRequest) -> Path:
    """Resolve a grouped package, retaining a legacy flat-path fallback.

    Grouped packages live at ``data_dir/<river_group>/.../<provider_station_id>``.
    A unique grouped package wins. A flat package is still accepted so a newly
    discovered station can be created before it is assigned to a river group.
    Multiple grouped matches are rejected rather than guessed.
    """

    package_name = station_package_name(request)
    legacy = data_dir / package_name
    matches = sorted(
        path
        for path in data_dir.rglob(package_name)
        if path.is_dir() and path.name == package_name
    ) if data_dir.exists() else []
    if len(matches) > 1:
        locations = ", ".join(str(path) for path in matches)
        raise RuntimeError(f"Station package has multiple grouped locations: {locations}")
    return matches[0] if matches else legacy


def station_group_parts(station_path: Path) -> tuple[str, ...]:
    """Return all grouped parents from outermost to innermost.

    Nested groups let a tributary package remain visibly under its receiving
    river while retaining the same station-package contract.
    """

    parts: list[str] = []
    current = station_path.parent
    while current is not None and _GROUP_NAME.fullmatch(current.name):
        parts.append(current.name)
        current = current.parent
    return tuple(reversed(parts))


def station_group_name(station_path: Path) -> str | None:
    """Return the innermost grouped parent, or ``None`` for a flat package."""

    parts = station_group_parts(station_path)
    return parts[-1] if parts else None


def output_station_root(output_dir: Path, station_path: Path) -> Path:
    """Mirror all data-package grouping levels under the output directory."""

    return output_dir.joinpath(*station_group_parts(station_path), station_path.name)
