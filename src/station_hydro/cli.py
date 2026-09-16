"""Command-line entry point for the independent station tool."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .discovery import USGSProvider, write_availability_summary
from .catalog import build_station_directory, build_station_directory_from_local
from .basin import download_contributing_watershed
from .field_measurements import fetch_field_measurements
from .retrieval import (
    DownloadPlanItem,
    available_data_from_json,
    build_download_plan,
    fetch_daily_discharge,
    fetch_annual_peak,
    fetch_unit_values,
)
from .quality import write_station_quality
from .plots import generate_station_figures
from .hydrology import write_station_hydrology
from .ratings import fetch_rating_curves
from .models import DateWindow, StationRequest
from .paths import output_station_root, station_root as resolve_station_root
from .storage import write_json


def _station_root(data_dir: Path, request: StationRequest) -> Path:
    return resolve_station_root(data_dir, request)


def _read_station_timezone(station_root: Path) -> str:
    metadata_path = station_root / "metadata" / "station_metadata.json"
    if not metadata_path.exists():
        return "MST"
    import json

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return metadata.get("timezone") or "MST"


def _write_download_plan(
    path: Path, request: StationRequest, plan: list[DownloadPlanItem]
) -> None:
    """Persist a self-identifying, traceable download plan."""

    write_json(
        path,
        {
            "station_id": request.station_id,
            "provider": request.provider,
            "generated_at": datetime.now(timezone.utc),
            "items": [asdict(item) for item in plan],
        },
    )


def _run_pipeline(args: argparse.Namespace, request: StationRequest) -> dict[str, Path | None]:
    """Run the complete Phase 1 station workflow and return key roots."""

    station_root = _station_root(args.data_dir, request)
    inventory_path = station_root / "metadata" / "available_data.json"
    if request.refresh or not inventory_path.exists():
        USGSProvider(args.data_dir, output_root=args.output_dir).discover(request)

    records = available_data_from_json(inventory_path)
    plan = build_download_plan(records, include_continuous=args.with_continuous)
    plan_path = station_root / "metadata" / "download_plan.json"
    _write_download_plan(plan_path, request, plan)
    write_availability_summary(station_root, records, args.output_dir, plan)
    timezone_name = _read_station_timezone(station_root)

    daily_record = next(
        record
        for record in records
        if record.provider_data_type == "dv" and record.parameter_code == "00060"
    )
    daily_path = fetch_daily_discharge(
        request, daily_record, args.data_dir, station_timezone=timezone_name
    )
    peak_record = next(
        (record for record in records if record.provider_data_type == "pk"), None
    )
    peak_path = None
    if peak_record is not None:
        peak_path = fetch_annual_peak(
            request, peak_record, args.data_dir, station_timezone=timezone_name
        )
    unit_record = next(
        (
            record
            for record in records
            if record.provider_data_type in {"uv", "iv"}
            and record.parameter_code == "00065"
        ),
        None,
    )
    if unit_record is None:
        unit_path = None
    else:
        unit_path = None
        if args.with_continuous:
            unit_item = next(item for item in plan if item.series_id == unit_record.series_id)
            if not unit_item.start or not unit_item.end:
                raise ValueError("Gage-height series has no bounded download window")
            unit_path = fetch_unit_values(
                request,
                unit_record,
                DateWindow(unit_item.start, unit_item.end),
                args.data_dir,
                station_timezone=timezone_name,
                output_name="unit_00065.parquet",
            )
    unit_discharge_record = next(
        (
            record
            for record in records
            if record.provider_data_type in {"uv", "iv"}
            and record.parameter_code == "00060"
        ),
        None,
    )
    unit_discharge_path = None
    if unit_discharge_record is not None and args.with_continuous:
        unit_discharge_item = next(
            item for item in plan if item.series_id == unit_discharge_record.series_id
        )
        if not unit_discharge_item.start or not unit_discharge_item.end:
            raise ValueError("Continuous discharge series has no bounded download window")
        unit_discharge_path = fetch_unit_values(
            request,
            unit_discharge_record,
            DateWindow(unit_discharge_item.start, unit_discharge_item.end),
            args.data_dir,
            station_timezone=timezone_name,
            output_name="unit_00060.parquet",
        )
    fetch_field_measurements(request, args.data_dir, daily_discharge_path=daily_path)
    rating_path = fetch_rating_curves(request, args.data_dir)
    write_availability_summary(station_root, records, args.output_dir, plan)

    metadata_path = station_root / "metadata" / "station_metadata.json"
    import json

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    basin_path = station_root / "spatial" / "contributing_watershed.geojson"
    if request.refresh or not basin_path.exists():
        basin_path = download_contributing_watershed(
            request,
            args.data_dir,
            provider_drainage_area_sq_mi=metadata.get("drainage_area_sq_mi"),
        )
    # A single-station run downloads only the station's own contributing
    # watershed. Regional HUC4 context is a separate basin-view concern.
    flow_network_path = None
    quality_path = write_station_quality(station_root, args.output_dir)
    hydrology_path = write_station_hydrology(station_root)
    # The browser product renders all charts from the local data package.  PNG
    # and SVG exports remain available through the explicit ``plot`` command,
    # but a normal station run does not generate images that the app will not
    # read.
    return {
        "station_root": station_root,
        "plan": plan_path,
        "daily": daily_path,
        "annual_peak": peak_path,
        "ratings": rating_path,
        "unit": unit_path,
        "unit_discharge": unit_discharge_path,
        "basin": basin_path,
        "flow_network": flow_network_path,
        "quality": quality_path,
        "hydrology": hydrology_path,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="station-hydro")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("discover", "fetch", "analyze", "basin", "plot", "run"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("station_id")
        command_parser.add_argument("--refresh", action="store_true")
        command_parser.add_argument("--profile", default="core")
        command_parser.add_argument(
            "--with-continuous",
            action="store_true",
            help="download continuous stage/discharge observations",
        )
        command_parser.add_argument(
            "--skip-continuous",
            action="store_false",
            dest="with_continuous",
            help="skip continuous stage/discharge observations",
        )
        command_parser.set_defaults(with_continuous=True)
        command_parser.add_argument("--data-dir", type=Path, default=Path("data/stations"))
        command_parser.add_argument("--output-dir", type=Path, default=Path("outputs"))

    catalog_parser = subparsers.add_parser(
        "catalog",
        help="validate a seed station list and build a ranked USGS directory",
    )
    catalog_parser.add_argument("--seed", type=Path, required=True)
    catalog_parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/catalog/sj_basin_usgs_station_directory.json"),
    )
    catalog_parser.add_argument("--data-dir", type=Path, default=Path("data/stations"))
    catalog_parser.add_argument("--timeout", type=int, default=60)
    catalog_parser.add_argument(
        "--local",
        action="store_true",
        help="build from cached station packages without contacting USGS",
    )

    serve_parser = subparsers.add_parser(
        "serve",
        help="start the local REST API and browser application",
    )
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--data-dir", type=Path, default=Path("data/stations"))
    serve_parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    serve_parser.add_argument(
        "--overview-dir",
        type=Path,
        default=None,
        help="local basin Overview display release (console/ and gis/)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "catalog":
        if args.local:
            paths = build_station_directory_from_local(
                seed_path=args.seed,
                data_root=args.data_dir,
                output_path=args.output,
            )
        else:
            paths = build_station_directory(
                seed_path=args.seed,
                data_root=args.data_dir,
                output_path=args.output,
                timeout_seconds=args.timeout,
            )
        for kind, path in paths.items():
            print(f"{kind}: {path}")
        return 0

    if args.command == "serve":
        import uvicorn

        from .webapp import create_app

        uvicorn.run(
            create_app(
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                overview_dir=args.overview_dir,
            ),
            host=args.host,
            port=args.port,
        )
        return 0

    request = StationRequest(
        station_id=args.station_id,
        refresh=args.refresh,
        profile=args.profile,
    )
    if args.command == "fetch":
        station_root = _station_root(args.data_dir, request)
        inventory_path = station_root / "metadata" / "available_data.json"
        if not inventory_path.exists():
            raise SystemExit(
                f"Missing discovery inventory: {inventory_path}. Run discover first."
            )
        records = available_data_from_json(inventory_path)
        plan = build_download_plan(records, include_continuous=args.with_continuous)
        plan_path = station_root / "metadata" / "download_plan.json"
        _write_download_plan(plan_path, request, plan)
        write_availability_summary(station_root, records, args.output_dir, plan)
        daily_record = next(
            record
            for record in records
            if record.provider_data_type == "dv" and record.parameter_code == "00060"
        )
        daily_path = fetch_daily_discharge(
            request,
            daily_record,
            args.data_dir,
            station_timezone="MST",
        )
        peak_record = next(
            (record for record in records if record.provider_data_type == "pk"), None
        )
        peak_path = None
        if peak_record is not None:
            peak_path = fetch_annual_peak(
                request, peak_record, args.data_dir, station_timezone="MST"
            )
        unit_record = next(
            (
                record
                for record in records
                if record.provider_data_type in {"uv", "iv"}
                and record.parameter_code == "00065"
            ),
            None,
        )
        unit_path = None
        if unit_record and args.with_continuous:
            unit_item = next(item for item in plan if item.series_id == unit_record.series_id)
            if unit_item.start and unit_item.end:
                unit_path = fetch_unit_values(
                    request,
                    unit_record,
                    DateWindow(unit_item.start, unit_item.end),
                    args.data_dir,
                    station_timezone="MST",
                    output_name="unit_00065.parquet",
                )
        unit_discharge_record = next(
            (
                record
                for record in records
                if record.provider_data_type in {"uv", "iv"}
                and record.parameter_code == "00060"
            ),
            None,
        )
        unit_discharge_path = None
        if unit_discharge_record and args.with_continuous:
            unit_discharge_item = next(
                item for item in plan if item.series_id == unit_discharge_record.series_id
            )
            if unit_discharge_item.start and unit_discharge_item.end:
                unit_discharge_path = fetch_unit_values(
                    request,
                    unit_discharge_record,
                    DateWindow(unit_discharge_item.start, unit_discharge_item.end),
                    args.data_dir,
                    station_timezone="MST",
                    output_name="unit_00060.parquet",
                )
        print(f"Download plan: {plan_path}")
        print(f"Daily discharge: {daily_path}")
        if peak_path:
            print(f"Annual peak discharge: {peak_path}")
        if unit_path:
            print(f"Unit gage height: {unit_path}")
        if unit_discharge_path:
            print(f"Unit discharge: {unit_discharge_path}")
        field_path, field_pairs_path = fetch_field_measurements(
            request,
            args.data_dir,
            daily_discharge_path=daily_path,
        )
        rating_path = fetch_rating_curves(request, args.data_dir)
        write_availability_summary(station_root, records, args.output_dir, plan)
        print(f"Field measurements: {field_path}")
        print(f"Field S-D pairs: {field_pairs_path}")
        print(f"Rating curves: {rating_path}")
        return 0

    if args.command == "analyze":
        station_root = _station_root(args.data_dir, request)
        quality_root = write_station_quality(station_root, args.output_dir)
        hydrology_root = write_station_hydrology(station_root)
        print(f"Quality outputs: {quality_root}")
        print(f"Hydrology outputs: {hydrology_root}")
        return 0

    if args.command == "basin":
        station_root = _station_root(args.data_dir, request)
        metadata_path = station_root / "metadata" / "station_metadata.json"
        drainage_area = None
        if metadata_path.exists():
            import json

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            drainage_area = metadata.get("drainage_area_sq_mi")
        output = download_contributing_watershed(
            request,
            args.data_dir,
            provider_drainage_area_sq_mi=drainage_area,
        )
        print(f"Contributing watershed: {output}")
        return 0

    if args.command == "plot":
        station_root = _station_root(args.data_dir, request)
        figures = generate_station_figures(
            station_root, args.output_dir
        )
        print(f"Figures: {output_station_root(args.output_dir, station_root) / 'figures'}")
        print(f"Generated: {', '.join(figures)}")
        return 0

    if args.command == "run":
        outputs = _run_pipeline(args, request)
        for name, path in outputs.items():
            print(f"{name}: {path}")
        return 0

    if args.command != "discover":
        print(
            f"Phase 1 scaffold: {args.command} {request.provider}:{request.station_id} "
            f"(profile={request.profile}, refresh={request.refresh})"
        )
        return 0

    result = USGSProvider(args.data_dir, output_root=args.output_dir).discover(request)
    print(f"Station: {result.station.provider}:{result.station.station_id}")
    print(f"Name: {result.station.name or 'N/A'}")
    print(f"Available series: {len(result.available_data)}")
    print(f"Raw and metadata root: {_station_root(args.data_dir, request)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
