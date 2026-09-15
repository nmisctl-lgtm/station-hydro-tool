"""Core Phase 1 figures with explicit units and reproducible windows."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import Any

if not os.environ.get("MPLCONFIGDIR"):
    _local_matplotlib_cache = Path.cwd() / ".matplotlib-cache"
    _local_matplotlib_cache.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(_local_matplotlib_cache)

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.patheffects as patheffects
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import geopandas as gpd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MaxNLocator
from pyproj import Geod
from scipy.stats import pearson3
from shapely.geometry import Point

from .models import AvailableDataRecord
from .paths import output_station_root
from .retrieval import available_data_from_json
from .storage import write_json
from .time_utils import recent_water_year_window


OKABE_ITO = {
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "orange": "#E69F00",
    "purple": "#CC79A7",
    "black": "#000000",
}


_WGS84_GEOD = Geod(ellps="WGS84")


def _save_figure(
    fig: plt.Figure, output_dir: Path, stem: str, transparent: bool = False
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    png = output_dir / f"{stem}.png"
    svg = output_dir / f"{stem}.svg"
    facecolor = "none" if transparent else "white"
    fig.savefig(
        png,
        dpi=300,
        bbox_inches="tight",
        facecolor=facecolor,
        transparent=transparent,
    )
    fig.savefig(svg, bbox_inches="tight", facecolor=facecolor, transparent=transparent)
    plt.close(fig)
    return {"png": str(png), "svg": str(svg)}


def _analysis_records(records: list[AvailableDataRecord]) -> list[AvailableDataRecord]:
    return [
        record
        for record in records
        if record.analysis_role in {"core", "supporting"}
        and record.declared_start is not None
        and record.declared_end is not None
    ]


def _frequency_code(record: AvailableDataRecord) -> str:
    if record.frequency in {"annual", "daily", "instantaneous", "annual_peak", "sample", "site_visit", "unit"}:
        return record.frequency
    legacy_labels = {
        "Annual data": "annual",
        "Daily values": "daily",
        "Instantaneous values": "instantaneous",
        "Annual peak measurements": "annual_peak",
        "Peak measurements": "annual_peak",
        "Water-quality samples": "sample",
        "Site visits": "site_visit",
        "Unit values (continuous)": "unit",
    }
    if record.frequency in legacy_labels:
        return legacy_labels[record.frequency]
    return {
        "ad": "annual",
        "dv": "daily",
        "iv": "instantaneous",
        "pk": "annual_peak",
        "qw": "sample",
        "sv": "site_visit",
        "uv": "unit",
    }.get(record.provider_data_type or "", "unknown")


def _series_label(record: AvailableDataRecord) -> str:
    return f"{record.variable} — {record.data_type} [frequency: {_frequency_code(record)}]"


def _completeness_label(record: AvailableDataRecord) -> str:
    """Use compact reader-facing labels for the completeness chart."""

    if _frequency_code(record) == "annual_peak":
        return "Annual Peak Q"
    return f"{record.variable} — {record.data_type}"


def _observed_intervals(station_root: Path) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """Return contiguous date intervals found in downloaded observations."""

    observed: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    observation_dir = station_root / "observations"
    if not observation_dir.exists():
        return observed
    for path in observation_dir.glob("*.parquet"):
        try:
            frame = pd.read_parquet(path, columns=["series_id", "observed_date_local"])
        except (KeyError, ValueError, OSError, ImportError):
            # Field-measurement tables do not use the provider series schema.
            continue
        if frame.empty:
            continue
        frame["observed_date_local"] = pd.to_datetime(
            frame["observed_date_local"], errors="coerce"
        ).dt.normalize()
        frame = frame.dropna(subset=["series_id", "observed_date_local"])
        for series_id, group in frame.groupby("series_id"):
            dates = group["observed_date_local"].drop_duplicates().sort_values().reset_index(drop=True)
            if dates.empty:
                continue
            intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
            start = previous = dates.iloc[0]
            for current in dates.iloc[1:]:
                if (current - previous).days > 1:
                    intervals.append((start, previous))
                    start = current
                previous = current
            intervals.append((start, previous))
            observed[str(series_id)] = intervals
    return observed


def plot_coverage(
    records: list[AvailableDataRecord],
    output_dir: Path,
    station_id: str = "USGS station",
    observed_intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] | None = None,
) -> dict[str, str]:
    records = _analysis_records(records)
    fig, ax = plt.subplots(figsize=(11, 5.5))
    observed_intervals = observed_intervals or {}
    if records:
        labels = [_series_label(r) for r in records]
        for index, record in enumerate(records):
            start = mdates.date2num(record.declared_start)
            end = mdates.date2num(record.declared_end)
            color = OKABE_ITO["blue"] if record.variable == "Discharge" else OKABE_ITO["vermillion"]
            # The hatched band is the provider-declared window.  Solid bars
            # below it are limited to dates actually present in local files.
            ax.barh(
                index,
                end - start + 1,
                left=start,
                height=0.60,
                facecolor="none",
                edgecolor=color,
                hatch="//",
                linewidth=0.8,
                alpha=0.65,
            )
            intervals = observed_intervals.get(record.series_id, [])
            if _frequency_code(record) == "annual_peak":
                peak_dates = [mdates.date2num(interval_start) for interval_start, _ in intervals]
                if peak_dates:
                    ax.plot(
                        peak_dates,
                        [index] * len(peak_dates),
                        linestyle="None",
                        marker="|",
                        markersize=10,
                        markeredgewidth=1.6,
                        color=color,
                        zorder=3,
                    )
            else:
                for interval_start, interval_end in intervals:
                    observed_start = mdates.date2num(interval_start)
                    observed_end = mdates.date2num(interval_end)
                    ax.barh(
                        index,
                        observed_end - observed_start + 1,
                        left=observed_start,
                        height=0.32,
                        color=color,
                        alpha=0.95,
                        zorder=3,
                    )
        ax.set_yticks(range(len(labels)), labels)
        ax.invert_yaxis()
        ax.xaxis_date()
        date_span_years = (
            max(record.declared_end for record in records)
            - min(record.declared_start for record in records)
        ).days / 365.2425
        if date_span_years >= 3:
            locator = mdates.YearLocator(
                base=max(1, int(np.ceil(date_span_years / 8)))
            )
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        else:
            locator = mdates.AutoDateLocator(minticks=6, maxticks=14)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    else:
        ax.text(0.5, 0.5, "No analysis series with declared coverage", ha="center", va="center")
        ax.set_axis_off()
    ax.set_xlabel("Date")
    ax.set_ylabel("Available data series")
    ax.set_title(f"USGS {station_id} — declared spans vs observed data segments")
    ax.legend(
        handles=[
            Patch(
                facecolor="none",
                edgecolor="#666666",
                hatch="//",
                label="Provider-declared window",
            ),
            Patch(facecolor=OKABE_ITO["blue"], label="Observed/downloaded interval"),
            Line2D(
                [0],
                [0],
                marker="|",
                color=OKABE_ITO["black"],
                linestyle="None",
                markersize=10,
                label="Observed annual peak record",
            ),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=3,
        frameon=False,
        fontsize=8.5,
    )
    fig.text(
        0.5,
        0.015,
        "Hatched outlines are provider-declared spans; solid segments and ticks are dates present in downloaded files. Blank intervals are not imputed.",
        ha="center",
        fontsize=8,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    return _save_figure(fig, output_dir, "eda_coverage")


def plot_completeness(
    summary_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
    records: list[AvailableDataRecord] | None = None,
) -> dict[str, str]:
    summary = pd.read_csv(summary_path)
    # Site visits are event metadata, not a regularly sampled observation
    # series; they should not occupy a completeness bar.
    records = [
        record
        for record in _analysis_records(records or [])
        if _frequency_code(record) != "site_visit"
    ]
    summary_by_id = summary.set_index("series_id") if not summary.empty else pd.DataFrame()
    if records:
        labels = [_completeness_label(record) for record in records]
        rows = [summary_by_id.loc[record.series_id] if record.series_id in summary_by_id.index else None for record in records]
        values = [
            pd.to_numeric(row.get("completeness"), errors="coerce") * 100 if row is not None else float("nan")
            for row in rows
        ]
        colors = [
            OKABE_ITO["blue"] if record.variable == "Discharge" else OKABE_ITO["vermillion"]
            for record in records
        ]
        # Wrapped notes need vertical space as well as the y-axis labels.
        fig = plt.figure(figsize=(16, max(6.5, 1.15 * len(labels) + 1.4)))
        grid = fig.add_gridspec(
            1,
            2,
            width_ratios=(4.5, 3.0),
            left=0.30,
            right=0.98,
            bottom=0.12,
            top=0.86,
            wspace=0.05,
        )
        ax = fig.add_subplot(grid[0, 0])
        note_ax = fig.add_subplot(grid[0, 1], sharey=ax)
        positions = list(range(len(labels)))
        note_text: list[str] = []
        for index, (value, color) in enumerate(zip(values, colors)):
            if pd.notna(value):
                ax.barh(index, value, color=color, alpha=0.85, height=0.58)
                row = rows[index]
                numerator = row.get("completeness_numerator")
                denominator = row.get("completeness_denominator")
                unit = row.get("expected_count_unit")
                if pd.notna(numerator) and pd.notna(denominator):
                    count_text = f"{int(numerator):,}/{int(denominator):,} {unit or 'observations'}"
                else:
                    count_text = ""
                note_text.append(
                    textwrap.fill(
                        f"{value:.2f}%  ({count_text})" if count_text else f"{value:.2f}%",
                        width=46,
                    )
                )
            else:
                record = records[index]
                row = rows[index]
                metadata = (
                    f"coverage {record.declared_start} to {record.declared_end}; "
                    f"provider records = {record.provider_count if record.provider_count is not None else 'N/A'}"
                )
                if row is None:
                    message = f"Not downloaded — {metadata}"
                elif _frequency_code(record) in {"sample"}:
                    message = "N/A — event/sample series; regular completeness is undefined"
                else:
                    message = f"Not downloaded — {metadata}"
                note_text.append(textwrap.fill(message, width=46))
        ax.set_yticks(positions, labels)
        ax.set_ylim(len(labels) - 0.45, -0.45)
        ax.set_xlim(0, 100)
        ax.set_xlabel("Completeness (%)")
        ax.set_ylabel("Available data series")
        ax.set_title(
            "Completeness percentage",
            loc="left",
            fontsize=10,
        )
        note_ax.set_xlim(0, 1)
        note_ax.set_title("Numerator / denominator", loc="left", fontsize=10)
        note_ax.set_xticks([])
        note_ax.tick_params(axis="y", left=False, labelleft=False)
        note_ax.spines[:].set_visible(False)
        for index, text in enumerate(note_text):
            note_ax.text(
                0.01,
                index,
                text,
                transform=note_ax.get_yaxis_transform(),
                va="center",
                ha="left",
                color="#555555",
                fontsize=8.5,
                linespacing=1.15,
                clip_on=True,
            )
        # Enclose both the quantitative panel and the explanatory column in
        # one quiet frame without distorting the 0–100% completeness scale.
        left = ax.get_position()
        right = note_ax.get_position()
        fig.add_artist(
            Rectangle(
                (left.x0, left.y0),
                right.x1 - left.x0,
                left.y1 - left.y0,
                transform=fig.transFigure,
                fill=False,
                edgecolor="#444444",
                linewidth=0.8,
                zorder=10,
            )
        )
    else:
        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.text(0.5, 0.5, "No downloaded series", ha="center", va="center")
        ax.set_axis_off()
    fig.suptitle(f"USGS {station_id} — observation completeness", fontsize=14, y=0.96)
    if not records:
        fig.tight_layout(rect=(0, 0, 1, 0.94))
    return _save_figure(fig, output_dir, "eda_completeness")


def plot_normalized_monthly_discharge(
    daily_path: Path, output_dir: Path, station_id: str = "USGS station"
) -> dict[str, str]:
    daily = pd.read_parquet(daily_path)
    daily["value"] = pd.to_numeric(daily["value"], errors="coerce")
    daily = daily.dropna(subset=["value", "observed_date_local"])
    if daily.empty:
        month_labels = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        fig, axes = plt.subplots(
            2, 1, figsize=(11, 8), sharex=True, gridspec_kw={"hspace": 0.34}
        )
        panel_details = (
            (
                "Discharge / full-period mean discharge (dimensionless)",
                "Normalized discharge: historical Water-Year monthly means ±1 SD",
            ),
            ("Discharge (ft³/s)", "Actual discharge: historical Water-Year monthly means ±1 SD"),
        )
        for axis, (ylabel, title) in zip(axes, panel_details):
            axis.text(
                0.5,
                0.5,
                "No valid daily discharge observations\navailable for monthly analysis",
                transform=axis.transAxes,
                ha="center",
                va="center",
                color="#555555",
            )
            axis.set_ylabel(ylabel)
            axis.set_title(title, loc="left", fontsize=10, pad=10)
            axis.grid(axis="y", alpha=0.25)
        axes[1].set_xticks(range(1, 13), month_labels)
        axes[1].set_xlabel("Calendar month")
        fig.suptitle(
            f"USGS {station_id} — monthly discharge distribution | no valid daily observations",
            fontsize=14,
        )
        fig.text(
            0.5,
            0.015,
            "USGS returned daily rows for the requested interval, but all discharge values were missing; no statistic or line was imputed.",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#555555",
        )
        fig.subplots_adjust(left=0.13, right=0.98, bottom=0.09, top=0.88, hspace=0.34)
        return _save_figure(fig, output_dir, "normalized_monthly_discharge")
    dates = pd.to_datetime(daily["observed_date_local"])
    latest_date = dates.max().date()
    daily["month"] = dates.dt.month
    daily["water_year"] = dates.dt.year + (dates.dt.month >= 10).astype(int)
    full_mean = float(daily["value"].mean())
    # First aggregate each Water Year/month, then calculate the climatology
    # across Water Years. This makes the band represent interannual spread,
    # rather than the much larger within-month storm variability.
    water_year_month = daily.groupby(["water_year", "month"], as_index=False)["value"].mean()
    monthly = water_year_month.groupby("month")["value"].agg(["mean", "std", "count"]).reindex(range(1, 13))
    current_water_year = int(daily["water_year"].max())
    current_valid_days = int((daily["water_year"] == current_water_year).sum())
    current_start = pd.Timestamp(year=current_water_year - 1, month=10, day=1)
    current_expected_days = int((pd.Timestamp(latest_date) - current_start).days + 1)
    current = water_year_month[water_year_month["water_year"] == current_water_year].set_index("month")["value"].reindex(range(1, 13))
    normalized_mean = monthly["mean"] / full_mean
    normalized_std = monthly["std"] / full_mean
    normalized_current = current / full_mean
    x = list(range(1, 13))
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    fig, axes = plt.subplots(
        2, 1, figsize=(11, 8), sharex=True, gridspec_kw={"hspace": 0.34}
    )

    def draw_monthly_panel(axis, historical_mean, historical_std, current_values, ylabel, title):
        axis.fill_between(
            x,
            historical_mean - historical_std,
            historical_mean + historical_std,
            color=OKABE_ITO["sky_blue"],
            alpha=0.35,
            label="±1 standard deviation",
        )
        axis.plot(
            x,
            historical_mean,
            color=OKABE_ITO["blue"],
            linewidth=2,
            label="Historical monthly mean",
        )
        axis.plot(
            x,
            current_values,
            color=OKABE_ITO["black"],
            linewidth=1.6,
            linestyle="--",
            marker="o",
            markersize=3.5,
            label=(
                f"Current Water Year WY {current_water_year} to date "
                f"({current_valid_days}/{current_expected_days} valid days)"
            ),
        )
        axis.set_ylabel(ylabel)
        axis.set_title(title, loc="left", fontsize=10, pad=10)
        axis.yaxis.set_major_locator(MaxNLocator(integer=True))
        axis.grid(axis="y", alpha=0.25)

    draw_monthly_panel(
        axes[0],
        normalized_mean,
        normalized_std,
        normalized_current,
        "Discharge / full-period mean discharge (dimensionless)",
        "Normalized discharge: historical Water-Year monthly means ±1 SD",
    )
    draw_monthly_panel(
        axes[1],
        monthly["mean"],
        monthly["std"],
        current,
        "Discharge (ft³/s)",
        "Actual discharge: historical Water-Year monthly means ±1 SD",
    )
    axes[1].set_xticks(x, month_labels)
    axes[1].set_xlabel("Calendar month")
    axes[0].legend(loc="upper left", frameon=False, ncol=2)
    fig.suptitle(
        f"USGS {station_id} — monthly discharge distribution | current WY {current_water_year} through {latest_date.isoformat()} ({current_valid_days}/{current_expected_days} valid days)",
        fontsize=14,
    )
    fig.subplots_adjust(left=0.13, right=0.98, bottom=0.09, top=0.88, hspace=0.34)
    return _save_figure(fig, output_dir, "normalized_monthly_discharge")


OVERVIEW_TOWN_NAMES = {
    "Aztec",
    "Blanding",
    "Bloomfield",
    "Chinle",
    "Cortez",
    "Durango",
    "Farmington",
    "Kayenta",
    "Monticello",
    "Pagosa Springs",
    "Shiprock",
}

LOCAL_TOWN_NAMES = {
    "Arboles",
    "Aztec",
    "Bayfield",
    "Bloomfield",
    "Cedar Hill",
    "Dulce",
    "Durango",
    "Ignacio",
    "Lumberton",
    "Pagosa Springs",
    "Piedra",
    "Silverton",
    "Spencerville",
}

OVERVIEW_RIVER_NAMES = {
    "Animas River",
    "Chaco River",
    "Chinle Creek",
    "La Plata River",
    "Mancos River",
    "Piedra River",
    "San Juan River",
}


def _town_points(places: gpd.GeoDataFrame, region: Any = None, buffer_km: float = 0) -> gpd.GeoDataFrame:
    points = places.loc[:, ["name", "geometry"]].copy()
    points = points[points["name"].notna()]
    points["geometry"] = points.representative_point()
    if region is not None:
        region_series = gpd.GeoSeries([region], crs=places.crs)
        if buffer_km:
            region = region_series.to_crs("EPSG:5070").buffer(buffer_km * 1000).to_crs(places.crs).iloc[0]
        points = points[points.geometry.within(region)]
    return points.drop_duplicates("name").sort_values("name")


def _named_rivers(
    rivers: gpd.GeoDataFrame,
    region: Any = None,
    limit: int = 8,
    names: set[str] | None = None,
) -> gpd.GeoDataFrame:
    if "name_at_outlet" not in rivers.columns:
        return rivers.iloc[0:0].copy()
    named = rivers.copy()
    named["river_name"] = named["name_at_outlet"].fillna("").astype(str).str.strip()
    named = named[named["river_name"].ne("")]
    if names is not None:
        named = named[named["river_name"].isin(names)]
    if region is not None:
        named = named[named.geometry.intersects(region)]
    named["river_area"] = pd.to_numeric(
        named.get("outlet_drainagearea_sqkm", pd.Series(index=named.index, dtype=float)),
        errors="coerce",
    ).fillna(0)
    named = named.sort_values(["river_area", "lengthkm"], ascending=False)
    return named.drop_duplicates("river_name").head(limit)


def _line_label_position(geometry: Any) -> tuple[Any, float] | None:
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type == "MultiLineString":
        geometry = max(geometry.geoms, key=lambda line: line.length)
    if geometry.geom_type != "LineString" or geometry.length == 0:
        return None
    midpoint = geometry.interpolate(0.5, normalized=True)
    before = geometry.interpolate(0.47, normalized=True)
    after = geometry.interpolate(0.53, normalized=True)
    angle = float(np.degrees(np.arctan2(after.y - before.y, after.x - before.x)))
    if angle < -90 or angle > 90:
        angle += 180
    return midpoint, angle


def _plot_named_rivers(
    ax: plt.Axes,
    rivers: gpd.GeoDataFrame,
    region: Any = None,
    limit: int = 8,
    linewidth: float = 1.25,
    fontsize: float = 8,
    names: set[str] | None = None,
) -> None:
    selected = _named_rivers(rivers, region, limit, names)
    for _, river in selected.iterrows():
        geometry = river.geometry
        if region is not None:
            geometry = geometry.intersection(region)
        if geometry.is_empty:
            continue
        gpd.GeoSeries([geometry], crs=rivers.crs).plot(
            ax=ax, color=OKABE_ITO["bluish_green"], linewidth=linewidth, alpha=0.9, zorder=2
        )
        label_position = _line_label_position(geometry)
        if label_position is None:
            continue
        point, angle = label_position
        ax.text(
            point.x,
            point.y,
            river["river_name"],
            fontsize=fontsize,
            color=OKABE_ITO["black"],
            rotation=angle,
            rotation_mode="anchor",
            ha="center",
            va="center",
            # A narrow white halo keeps the label readable without placing a
            # solid box over the named river.  The text follows the local line
            # tangent, which is more legible than a horizontal label.
            path_effects=[patheffects.withStroke(linewidth=2.6, foreground="white")],
            zorder=6,
        )


def _plot_town_labels(
    ax: plt.Axes,
    towns: gpd.GeoDataFrame,
    fontsize: float = 7.5,
    offset_map: dict[str, tuple[float, float]] | None = None,
) -> list[Any]:
    offsets = [(4, 4), (4, -10), (-4, 4), (-4, -10)]
    annotations: list[Any] = []
    for index, (_, town) in enumerate(towns.iterrows()):
        point = town.geometry
        dx, dy = (offset_map or {}).get(town["name"], offsets[index % len(offsets)])
        ax.plot(
            point.x,
            point.y,
            marker="o",
            markersize=5.0,
            markerfacecolor="none",
            markeredgecolor=OKABE_ITO["black"],
            markeredgewidth=0.9,
            zorder=7,
        )
        annotations.append(ax.annotate(
            town["name"],
            (point.x, point.y),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=fontsize,
            color=OKABE_ITO["black"],
            path_effects=[patheffects.withStroke(linewidth=2.8, foreground="white")],
            zorder=8,
        ))
    return annotations


def _annotation_overlaps_any(
    annotation: Any,
    others: list[Any],
    fig: plt.Figure,
) -> bool:
    """Return whether a text annotation overlaps a previously drawn label."""

    renderer = fig.canvas.get_renderer()
    box = annotation.get_window_extent(renderer=renderer).expanded(1.04, 1.10)
    return any(box.overlaps(other.get_window_extent(renderer=renderer).expanded(1.04, 1.10)) for other in others)


def _plot_north_arrow(ax: plt.Axes) -> None:
    """Add a compact north arrow in axes-relative coordinates."""

    ax.annotate(
        "",
        xy=(0.93, 0.86),
        xytext=(0.93, 0.72),
        xycoords="axes fraction",
        arrowprops={"arrowstyle": "-|>", "color": OKABE_ITO["black"], "linewidth": 1.4},
        zorder=12,
    )
    ax.text(
        0.93,
        0.88,
        "N",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=9,
        color=OKABE_ITO["black"],
        path_effects=[patheffects.withStroke(linewidth=2.8, foreground="white")],
        zorder=12,
    )


def _plot_scale_bar(ax: plt.Axes, crs: Any, distance_miles: float = 10) -> None:
    """Add a simple geodesic scale bar without requiring a mapping package."""

    x_min, x_max = ax.get_xlim()
    y_min, y_max = ax.get_ylim()
    x_start = x_min + 0.69 * (x_max - x_min)
    y_start = y_min + 0.055 * (y_max - y_min)
    distance_m = distance_miles * 1609.344
    if crs is not None and getattr(crs, "is_geographic", False):
        x_end, _, _ = _WGS84_GEOD.fwd(x_start, y_start, 90, distance_m)
    else:
        x_end = x_start + distance_m
    if x_end > x_max - 0.02 * (x_max - x_min):
        distance_miles = 5
        distance_m = distance_miles * 1609.344
        if crs is not None and getattr(crs, "is_geographic", False):
            x_end, _, _ = _WGS84_GEOD.fwd(x_start, y_start, 90, distance_m)
        else:
            x_end = x_start + distance_m
    tick_height = 0.012 * (y_max - y_min)
    ax.plot(
        [x_start, x_end],
        [y_start, y_start],
        color=OKABE_ITO["black"],
        linewidth=2.2,
        solid_capstyle="butt",
        zorder=12,
    )
    ax.plot(
        [x_start, x_start],
        [y_start - tick_height, y_start + tick_height],
        color=OKABE_ITO["black"],
        linewidth=1.0,
        zorder=12,
    )
    ax.plot(
        [x_end, x_end],
        [y_start - tick_height, y_start + tick_height],
        color=OKABE_ITO["black"],
        linewidth=1.0,
        zorder=12,
    )
    ax.text(
        (x_start + x_end) / 2,
        y_start + 1.8 * tick_height,
        f"{distance_miles:g} mi",
        ha="center",
        va="bottom",
        fontsize=8,
        color=OKABE_ITO["black"],
        path_effects=[patheffects.withStroke(linewidth=2.8, foreground="white")],
        zorder=12,
    )


def plot_station_watershed_network(
    station_metadata_path: Path,
    watershed_path: Path,
    flow_network_path: Path,
    output_dir: Path,
    basin_context: dict[str, Path] | None = None,
) -> dict[str, str]:
    """Plot the local watershed and a regional SJ Basin overview."""

    metadata = json.loads(station_metadata_path.read_text(encoding="utf-8"))
    latitude = metadata.get("latitude")
    longitude = metadata.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError("Station metadata has no coordinates")
    watershed = gpd.read_file(watershed_path)
    network = gpd.read_file(flow_network_path)
    if watershed.empty or network.empty:
        raise ValueError("Watershed or flow network is empty")
    station = gpd.GeoDataFrame(
        {"station_id": [metadata.get("station_id")]},
        geometry=[Point(longitude, latitude)],
        crs="EPSG:4326",
    ).to_crs(watershed.crs)
    network = network.to_crs(watershed.crs)

    regional_basin = regional_rivers = regional_places = None
    if basin_context and all(path.exists() for path in basin_context.values()):
        regional_basin = gpd.read_file(basin_context["basin_boundary"]).to_crs(watershed.crs)
        regional_rivers = gpd.read_file(basin_context["main_rivers"]).to_crs(watershed.crs)
        regional_places = gpd.read_file(basin_context["places"]).to_crs(watershed.crs)

    if regional_basin is None:
        fig, ax = plt.subplots(figsize=(10, 8))
        overview_ax = None
    else:
        fig = plt.figure(figsize=(13.5, 9))
        # Use the wide right-hand whitespace for the inset.  The inset is
        # intentionally allowed to sit close to, or slightly over, the local
        # map so the watershed itself remains the visual focus.
        ax = fig.add_axes([0.025, 0.10, 0.72, 0.82])
        overview_ax = None
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    watershed.boundary.plot(ax=ax, color=OKABE_ITO["blue"], linewidth=1.4, alpha=0.9)
    main = network[network["network_role"] == "upstream_main"]
    tributaries = network[network["network_role"] == "upstream_tributaries"]
    local_town_annotations: list[Any] = []
    if not tributaries.empty:
        tributaries.plot(ax=ax, color=OKABE_ITO["sky_blue"], linewidth=0.35, alpha=0.65)
    if not main.empty:
        main.plot(ax=ax, color=OKABE_ITO["blue"], linewidth=1.2, alpha=0.95)
    if regional_rivers is not None:
        _plot_named_rivers(
            ax,
            regional_rivers,
            region=watershed.geometry.iloc[0],
            limit=5,
            linewidth=1.4,
            fontsize=8,
        )
        local_towns = _town_points(
            regional_places,
            region=watershed.geometry.iloc[0],
            buffer_km=15,
        )
        local_towns = local_towns[local_towns["name"].isin(LOCAL_TOWN_NAMES)]
        local_towns = local_towns.head(8)
        local_town_annotations = _plot_town_labels(
            ax,
            local_towns,
            fontsize=7.5,
            offset_map={
                "Aztec": (8, -12),
                "Bloomfield": (8, -22),
                "Cedar Hill": (8, -10),
                "Lumberton": (8, -14),
            },
        )
    station.plot(ax=ax, color=OKABE_ITO["vermillion"], edgecolor="white", linewidth=0.8, markersize=65, zorder=5)
    area = metadata.get("drainage_area_sq_mi")
    area_label = f"{float(area):,.0f} mi²" if area is not None else "area unavailable"
    station_point = station.geometry.iloc[0]
    fig.canvas.draw()
    station_annotation = ax.annotate(
        f"USGS {metadata.get('station_id')}\n{area_label}",
        (station_point.x, station_point.y),
        xytext=(8, 8),
        textcoords="offset points",
        fontsize=8,
        color=OKABE_ITO["black"],
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 2},
        zorder=9,
    )
    for offset in ((8, 8), (8, 24), (-72, 24), (-72, -28), (8, -28)):
        station_annotation.set_position(offset)
        fig.canvas.draw()
        if not _annotation_overlaps_any(station_annotation, local_town_annotations, fig):
            break
    # Keep the outlet marker and the full boundary away from the image edge;
    # this also leaves room for a later DEM background layer.
    ax.margins(x=0.01, y=0.025)
    ax.set_axis_off()
    ax.set_title(
        f"USGS {metadata.get('station_id')} — local contributing watershed",
        fontsize=12,
        pad=8,
    )
    _plot_north_arrow(ax)
    _plot_scale_bar(ax, watershed.crs)
    if regional_basin is not None:
        fig.canvas.draw()
        x_min, x_max = ax.get_xlim()
        basin_max_x = float(watershed.total_bounds[2])
        shape_right = ax.get_position().x0 + (basin_max_x - x_min) / (x_max - x_min) * ax.get_position().width
        overview_width = 0.36
        overview_height = 0.34
        overview_x = min(0.94 - overview_width, max(0.27, shape_right - 0.02))
        overview_ax = fig.add_axes([overview_x, 0.56, overview_width, overview_height], zorder=20)
        overview_ax.set_facecolor("none")
        # The regional HUC4 boundary and the selected station watershed use
        # separate styles so the inset explains both geographic context and
        # the station's actual contributing area.
        watershed.plot(
            ax=overview_ax,
            facecolor=OKABE_ITO["orange"],
            edgecolor="none",
            alpha=0.10,
            zorder=2,
        )
        regional_basin.boundary.plot(
            ax=overview_ax,
            color=OKABE_ITO["purple"],
            linewidth=1.5,
            linestyle="--",
            alpha=0.9,
            zorder=3,
        )
        watershed.boundary.plot(
            ax=overview_ax,
            color=OKABE_ITO["orange"],
            linewidth=2.0,
            alpha=0.95,
            zorder=4,
        )
        _plot_named_rivers(
            overview_ax,
            regional_rivers,
            limit=7,
            linewidth=0.9,
            fontsize=7,
            names=OVERVIEW_RIVER_NAMES,
        )
        overview_towns = _town_points(regional_places)
        overview_towns = overview_towns[overview_towns["name"].isin(OVERVIEW_TOWN_NAMES)]
        overview_town_annotations = _plot_town_labels(
            overview_ax,
            overview_towns,
            fontsize=6.5,
            offset_map={
                "Bloomfield": (6, -12),
                "Farmington": (6, 8),
                "Aztec": (6, -12),
                "Cortez": (-24, 6),
                "Blanding": (6, -12),
                "Monticello": (6, 6),
                "Shiprock": (6, 6),
                "Chinle": (6, -12),
            },
        )
        station_overview = station.to_crs(regional_basin.crs)
        station_overview.plot(
            ax=overview_ax,
            color=OKABE_ITO["vermillion"],
            edgecolor="white",
            linewidth=0.8,
            markersize=60,
            zorder=10,
        )
        overview_point = station_overview.geometry.iloc[0]
        fig.canvas.draw()
        overview_station_annotation = overview_ax.annotate(
            str(metadata.get("station_id")),
            (overview_point.x, overview_point.y),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=7,
            color=OKABE_ITO["black"],
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 1.5},
            zorder=11,
        )
        for offset in ((5, 5), (5, 16), (-58, 16), (-58, -18), (5, -18)):
            overview_station_annotation.set_position(offset)
            fig.canvas.draw()
            if not _annotation_overlaps_any(overview_station_annotation, overview_town_annotations, fig):
                break
        overview_ax.set_title("SJ Basin overview (HUC4-1408)", fontsize=10, pad=8)
        overview_ax.set_axis_off()

    handles = [
        Patch(facecolor="none", edgecolor=OKABE_ITO["blue"], label="Contributing watershed"),
        Line2D([0], [0], color=OKABE_ITO["orange"], linewidth=2.0, label="Current station watershed (overview)"),
        Line2D([0], [0], color=OKABE_ITO["blue"], linewidth=1.2, label="Upstream mainstem"),
        Line2D([0], [0], color=OKABE_ITO["sky_blue"], linewidth=1.0, label="Upstream tributaries"),
        Line2D([0], [0], color=OKABE_ITO["bluish_green"], linewidth=1.2, label="Named rivers"),
        Line2D([0], [0], color=OKABE_ITO["purple"], linestyle="--", linewidth=1.2, label="SJ Basin boundary"),
        Line2D(
            [0],
            [0],
            marker="o",
            color="none",
            markerfacecolor="none",
            markeredgecolor=OKABE_ITO["black"],
            markersize=5,
            label="Town",
        ),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=OKABE_ITO["vermillion"], markeredgecolor="white", label="USGS station"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=8)
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.09, top=0.91)
    return _save_figure(fig, output_dir, "station_watershed_network", transparent=True)


def _daily_stage(unit_path: Path) -> pd.DataFrame:
    unit = pd.read_parquet(unit_path)
    unit["value"] = pd.to_numeric(unit["value"], errors="coerce")
    unit = unit.dropna(subset=["value", "observed_date_local"])
    return unit.groupby("observed_date_local", as_index=False)["value"].mean().rename(
        columns={"value": "gage_height_ft"}
    )


def _reindex_daily_values(
    frame: pd.DataFrame,
    date_column: str,
    value_column: str,
    start: Any,
    end: Any,
) -> pd.Series:
    """Return a daily series on a complete calendar index without filling gaps."""

    index = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    if frame.empty:
        return pd.Series(index=index, dtype=float)
    dates = pd.to_datetime(frame[date_column], errors="coerce").dt.normalize()
    values = pd.to_numeric(frame[value_column], errors="coerce")
    series = pd.Series(values.to_numpy(), index=dates)
    series = series[~series.index.isna()]
    series = series.groupby(level=0).first().sort_index()
    return series.reindex(index)


def plot_hydrograph(
    daily_path: Path,
    unit_path: Path,
    output_dir: Path,
    count: int = 10,
    annual_peak_path: Path | None = None,
    station_id: str = "USGS station",
) -> dict[str, str]:
    daily = pd.read_parquet(daily_path)
    daily["value"] = pd.to_numeric(daily["value"], errors="coerce")
    daily["date"] = pd.to_datetime(daily["observed_date_local"], errors="coerce")
    daily = daily.dropna(subset=["date", "value"])
    if daily.empty:
        fig, axes = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True)
        axes[0].text(
            0.5,
            0.5,
            "No valid daily discharge observations available",
            transform=axes[0].transAxes,
            ha="center",
            va="center",
            color="#555555",
        )
        axes[1].text(
            0.5,
            0.5,
            "Continuous gage height not downloaded\n(use --with-continuous)",
            transform=axes[1].transAxes,
            ha="center",
            va="center",
            color="#555555",
        )
        axes[0].set_ylabel("Discharge (ft³/s)", rotation=90, labelpad=12)
        axes[0].set_title(
            "Daily discharge | no valid observations returned by USGS",
            loc="left",
            fontsize=10,
        )
        axes[1].set_ylabel("Gage height (ft)", rotation=90, labelpad=12)
        axes[1].set_xlabel("Date")
        axes[1].set_title(
            "Daily mean gage height from unit values | not available",
            loc="left",
            fontsize=10,
        )
        fig.suptitle(f"USGS {station_id} — recent {count} Water Years hydrograph", fontsize=14)
        fig.text(
            0.5,
            0.015,
            "No line is plotted because the downloaded daily discharge table contains no valid numeric values.",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#555555",
        )
        fig.tight_layout(rect=(0.07, 0.06, 1, 0.93))
        return _save_figure(fig, output_dir, "hydrograph")
    latest = daily["date"].max().date()
    full_period_mean = float(daily["value"].mean()) if not daily.empty else None
    mean_annual_peak = None
    peak_count = 0
    if annual_peak_path is not None and annual_peak_path.exists():
        annual_peaks = _read_csv_or_empty(annual_peak_path)
        peak_values = pd.to_numeric(
            annual_peaks.get("annual_peak_discharge_cfs"), errors="coerce"
        ).dropna()
        if not peak_values.empty:
            mean_annual_peak = float(peak_values.mean())
            peak_count = int(len(peak_values))
    if mean_annual_peak is None and not daily.empty:
        daily_water_year = daily["date"].dt.year + (daily["date"].dt.month >= 10).astype(int)
        peak_values = daily.assign(_water_year=daily_water_year).groupby("_water_year")["value"].max()
        if not peak_values.empty:
            mean_annual_peak = float(peak_values.mean())
            peak_count = int(len(peak_values))
    window = recent_water_year_window(latest, count)
    daily = daily[(daily["date"].dt.date >= window.start) & (daily["date"].dt.date <= window.end)]
    daily_plot = _reindex_daily_values(
        daily, "date", "value", window.start, window.end
    )
    daily_valid_count = int(daily_plot.notna().sum())
    daily_missing_count = int(daily_plot.isna().sum())
    if unit_path.exists():
        stage = _daily_stage(unit_path)
        stage["date"] = pd.to_datetime(stage["observed_date_local"], errors="coerce")
        stage = stage[(stage["date"].dt.date >= window.start) & (stage["date"].dt.date <= window.end)]
        stage_plot = _reindex_daily_values(
            stage, "date", "gage_height_ft", window.start, window.end
        )
    else:
        stage = pd.DataFrame(columns=["date", "gage_height_ft"])
        stage_plot = _reindex_daily_values(
            stage, "date", "gage_height_ft", window.start, window.end
        )

    fig, axes = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True)
    axes[0].plot(
        daily_plot.index,
        daily_plot.to_numpy(),
        color=OKABE_ITO["blue"],
        linewidth=0.75,
        label="Daily discharge (observed values; gaps retained)",
    )
    if full_period_mean is not None:
        axes[0].axhline(
            full_period_mean,
            color=OKABE_ITO["black"],
            linestyle="--",
            linewidth=1.0,
            label=f"Full-record mean daily Q ({full_period_mean:,.0f} ft³/s)",
        )
    if mean_annual_peak is not None:
        axes[0].axhline(
            mean_annual_peak,
            color=OKABE_ITO["vermillion"],
            linestyle=":",
            linewidth=1.2,
            label=f"Mean annual peak Q ({mean_annual_peak:,.0f} ft³/s; n={peak_count})",
        )
    axes[0].set_ylabel("Discharge (ft³/s)", rotation=90, labelpad=12)
    axes[0].set_title(
        f"Daily discharge | {window.start.isoformat()} to {window.end.isoformat()} | observed n = {daily_valid_count:,}; missing days = {daily_missing_count:,}",
        loc="left",
        fontsize=10,
    )
    if not stage.empty:
        axes[1].plot(
            stage_plot.index,
            stage_plot.to_numpy(),
            color=OKABE_ITO["vermillion"],
            linewidth=0.75,
            label="Daily mean gage height from unit values (gaps retained)",
        )
    else:
        axes[1].text(
            0.5,
            0.5,
            "Continuous gage height not downloaded\n(use --with-continuous)",
            transform=axes[1].transAxes,
            ha="center",
            va="center",
            color="#555555",
        )
    axes[1].set_ylabel("Gage height (ft)", rotation=90, labelpad=12)
    axes[1].set_xlabel("Date")
    axes[1].set_title(
        f"Daily mean gage height from unit values | {window.start.isoformat()} to {window.end.isoformat()} | observed n = {int(stage_plot.notna().sum()):,}; missing days = {int(stage_plot.isna().sum()):,}",
        loc="left",
        fontsize=10,
    )
    locator = mdates.AutoDateLocator(minticks=10, maxticks=22)
    axes[1].xaxis.set_major_locator(locator)
    axes[1].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    for axis in axes:
        axis.yaxis.set_major_locator(MaxNLocator(integer=True))
        axis.grid(axis="y", alpha=0.25)
        if axis.get_legend_handles_labels()[0]:
            axis.legend(loc="upper left", frameon=False, fontsize=8.5)
    fig.suptitle(f"USGS {station_id} — recent {count} Water Years hydrograph", fontsize=14)
    fig.tight_layout(rect=(0.07, 0.06, 1, 0.93))
    return _save_figure(fig, output_dir, "hydrograph")


def plot_flood_frequency(
    flood_frequency_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
) -> dict[str, str]:
    """Plot systematic annual maxima and B17C-style estimates with CI."""

    table = _read_csv_or_empty(flood_frequency_path)
    if table.empty:
        fig, ax = plt.subplots(figsize=(10.5, 7.0))
        ax.set_xscale("log")
        ax.set_xlim(1, 100)
        ax.set_ylim(0, 1)
        ax.set_xlabel("Return period (years; log scale)")
        ax.set_ylabel("Annual maximum daily discharge (ft³/s)")
        ax.text(
            0.5,
            0.5,
            "No flood-frequency results available\n"
            "No usable annual-maximum sample was retained for this station.",
            transform=ax.transAxes,
            ha="center",
            va="center",
            color="#555555",
        )
        ax.set_title(
            f"USGS {station_id} — annual maximum discharge frequency\n"
            "Analysis withheld because the input result table is empty",
            loc="left",
            fontsize=11,
        )
        ax.grid(False)
        fig.subplots_adjust(left=0.12, right=0.98, bottom=0.11, top=0.86)
        return _save_figure(fig, output_dir, "flood_frequency")
    empirical = table[
        table["method"] == "empirical_ams_weibull_plotting_position"
    ].dropna(subset=["return_period_years", "estimate_discharge_cfs"])
    b17c = table[
        table["method"] == "b17c_systematic_ema_station_skew"
    ].dropna(subset=["return_period_years", "estimate_discharge_cfs"])
    excluded = table[table["method"] == "excluded_historical_peak"]
    source_values = set(table.get("source", pd.Series(dtype="string")).dropna().astype(str))
    daily_fallback = "daily_annual_maxima_fallback" in source_values
    fig, ax = plt.subplots(figsize=(10.5, 7.0))
    if not empirical.empty:
        empirical_label = (
            "Daily-derived empirical annual maxima"
            if daily_fallback
            else "Systematic empirical annual maxima"
        )
        ax.scatter(
            empirical["return_period_years"],
            empirical["estimate_discharge_cfs"],
            s=18,
            color=OKABE_ITO["blue"],
            alpha=0.75,
            label=f"{empirical_label} (n = {int(empirical['n_systematic_years'].iloc[0])})",
        )
        peak = empirical.loc[empirical["estimate_discharge_cfs"].idxmax()]
        peak_date = pd.to_datetime(peak.get("annual_peak_date"), errors="coerce")
        peak_date_text = (
            peak_date.strftime("%b-%d-%Y") if pd.notna(peak_date) else "Unknown date"
        )
        peak_label = (
            f"{peak_date_text}: {float(peak['estimate_discharge_cfs']):,.0f} cfs, "
            f"empirical T = {float(peak['return_period_years']):.0f} yr\n"
            f"fitted LP3 T ≈ {float(peak['fitted_return_period_years']):,.0f} yr"
            if pd.notna(peak.get("fitted_return_period_years"))
            else f"{float(peak['return_period_years']):.0f} yr flood"
        )
        ax.annotate(
            peak_label,
            xy=(peak["return_period_years"], peak["estimate_discharge_cfs"]),
            xytext=(-18, -92),
            textcoords="offset points",
            ha="right",
            va="top",
            fontsize=9,
            color=OKABE_ITO["blue"],
            arrowprops={"arrowstyle": "->", "color": OKABE_ITO["blue"], "lw": 0.9},
            bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": OKABE_ITO["blue"], "alpha": 0.9},
        )
    if not excluded.empty:
        excluded_count = len(excluded)
    else:
        excluded_count = 0
    if not b17c.empty:
        b17c = b17c.sort_values("return_period_years")
        ax.plot(
            b17c["return_period_years"],
            b17c["estimate_discharge_cfs"],
            color=OKABE_ITO["vermillion"],
            linewidth=1.8,
            marker="o",
            markersize=3,
            label="B17C-style systematic LP3/EMA-equivalent",
        )
        lower = pd.to_numeric(
            b17c["lower_confidence_cfs"]
            if "lower_confidence_cfs" in b17c
            else pd.Series(index=b17c.index, dtype=float),
            errors="coerce",
        )
        upper = pd.to_numeric(
            b17c["upper_confidence_cfs"]
            if "upper_confidence_cfs" in b17c
            else pd.Series(index=b17c.index, dtype=float),
            errors="coerce",
        )
        ci_mask = lower.notna() & upper.notna()
        if ci_mask.any():
            ax.fill_between(
                b17c.loc[ci_mask, "return_period_years"],
                lower.loc[ci_mask],
                upper.loc[ci_mask],
                color=OKABE_ITO["sky_blue"],
                alpha=0.30,
                label="95% parametric-bootstrap confidence interval",
            )
        fit_row = b17c.iloc[0]
        fit_columns = {"fit_log10_skew", "fit_log10_location", "fit_log10_scale"}
        if fit_columns.issubset(b17c.columns) and all(pd.notna(fit_row[column]) for column in fit_columns):
            fitted_peak_period = (
                float(peak.get("fitted_return_period_years"))
                if not empirical.empty and pd.notna(peak.get("fitted_return_period_years"))
                else None
            )
            max_period = max(100.0, fitted_peak_period * 1.15 if fitted_peak_period else 100.0)
            grid = np.geomspace(2.0, max_period, 240)
            curve = 10 ** pearson3.ppf(
                1 - 1 / grid,
                float(fit_row["fit_log10_skew"]),
                loc=float(fit_row["fit_log10_location"]),
                scale=float(fit_row["fit_log10_scale"]),
            )
            tail = grid > float(b17c["return_period_years"].max())
            if tail.any():
                ax.plot(
                    grid[tail],
                    curve[tail],
                    color=OKABE_ITO["vermillion"],
                    linestyle="--",
                    linewidth=1.3,
                    label="LP3 tail extrapolation beyond plotted CI range",
                )
            if fitted_peak_period and np.isfinite(fitted_peak_period):
                peak_flow = float(peak["estimate_discharge_cfs"])
                ax.scatter(
                    fitted_peak_period,
                    peak_flow,
                    marker="x",
                    s=55,
                    linewidth=1.5,
                    color=OKABE_ITO["black"],
                    zorder=5,
                    label="Fitted-line inverse at largest observed peak",
                )
                ax.annotate(
                    f"LP3 inverse ≈ {fitted_peak_period:,.0f} yr\n(tail extrapolation)",
                    xy=(fitted_peak_period, peak_flow),
                    xytext=(-12, -48),
                    textcoords="offset points",
                    ha="right",
                    va="top",
                    fontsize=8.5,
                    color=OKABE_ITO["black"],
                    arrowprops={"arrowstyle": "->", "color": OKABE_ITO["black"], "lw": 0.8},
                    bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#777777", "alpha": 0.9},
                )
    ax.set_xscale("log")
    ax.set_xlabel("Return period (years; log scale)")
    ax.set_ylabel("Annual maximum daily discharge (ft³/s)")
    fit_count = int(empirical["n_systematic_years"].iloc[0]) if not empirical.empty else 0
    source_note = (
        "daily-maxima fallback; no provider annual-peak series"
        if daily_fallback
        else "provider annual-peak series"
    )
    fit_note = (
        "B17C-style fit"
        if not b17c.empty
        else f"B17C-style fit withheld ({fit_count} complete Water Years; minimum 8)"
    )
    ax.set_title(
        f"USGS {station_id} — annual maximum discharge frequency\n"
        f"Source: {source_note}; {fit_note}; {excluded_count} HISTORIC record(s) excluded\n"
        f"Empirical points are descriptive only when the fitted curve is withheld",
        loc="left",
        fontsize=11,
    )
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper left", bbox_to_anchor=(0, 0.98), frameon=False, fontsize=9)
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.11, top=0.86)
    return _save_figure(fig, output_dir, "flood_frequency")


def plot_consistency_evidence(
    annual_path: Path,
    field_measurements_path: Path,
    method_evidence_path: Path,
    change_points_path: Path,
    rating_summary_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
) -> dict[str, str]:
    """Present consistency evidence without turning candidates into causes."""

    annual = _read_csv_or_empty(annual_path)
    field = (
        pd.read_parquet(field_measurements_path)
        if field_measurements_path.exists()
        else pd.DataFrame()
    )
    evidence = _read_csv_or_empty(method_evidence_path)
    candidates = _read_csv_or_empty(change_points_path)
    ratings = _read_csv_or_empty(rating_summary_path)

    fig, axes = plt.subplots(2, 1, figsize=(12, 8.5), sharex=True)
    ax = axes[0]
    if not annual.empty:
        annual["plot_date"] = pd.to_datetime(annual["end_date"], errors="coerce")
        annual["mean_discharge_cfs"] = pd.to_numeric(
            annual["mean_discharge_cfs"], errors="coerce"
        )
        complete_means = annual.loc[
            annual["is_complete"].fillna(False), "mean_discharge_cfs"
        ].dropna()
        dry_threshold = float(complete_means.quantile(0.25)) if not complete_means.empty else None
        wet_threshold = float(complete_means.quantile(0.75)) if not complete_means.empty else None
        class_colors = {
            "dry": OKABE_ITO["vermillion"],
            "normal": OKABE_ITO["blue"],
            "wet": OKABE_ITO["bluish_green"],
        }
        for year_class, group in annual.dropna(subset=["plot_date", "mean_discharge_cfs"]).groupby(
            "hydrologic_year_class", dropna=False
        ):
            color = class_colors.get(str(year_class), OKABE_ITO["black"])
            ax.plot(
                group["plot_date"],
                group["mean_discharge_cfs"],
                color=color,
                linewidth=1.0,
                marker="o",
                markersize=2.5,
                label=str(year_class).title() if pd.notna(year_class) else "Unclassified",
            )
        if not candidates.empty and "candidate_water_year" in candidates:
            candidate_years = sorted(
                pd.to_numeric(candidates["candidate_water_year"], errors="coerce")
                .dropna()
                .astype(int)
                .unique()
            )
            for candidate_year in candidate_years:
                ax.axvline(
                    pd.Timestamp(year=candidate_year, month=9, day=30),
                    color=OKABE_ITO["purple"],
                    linestyle="--",
                    linewidth=0.8,
                    alpha=0.65,
                )
            ax.text(
                0.01,
                0.97,
                "Dashed lines: statistical change-point candidates; source-record review required",
                transform=ax.transAxes,
                va="top",
                fontsize=8.5,
                color="#555555",
            )
    else:
        ax.text(0.5, 0.5, "No annual hydrology table", ha="center", va="center")
    ax.set_ylabel("Annual mean discharge (ft³/s)")
    ax.set_title(
        "Annual flow consistency screen — Water-Year means and candidate shifts"
        + (
            f" | Dry ≤ Q25 ({dry_threshold:,.0f}); normal between; wet ≥ Q75 ({wet_threshold:,.0f}) ft³/s"
            if dry_threshold is not None and wet_threshold is not None
            else ""
        ),
        loc="left",
        fontsize=11,
    )
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.grid(axis="y", alpha=0.22)
    if not annual.empty:
        ax.legend(loc="upper right", frameon=False, ncol=3, fontsize=8)

    ax = axes[1]
    evidence_rows: list[tuple[str, pd.Timestamp, pd.Timestamp, int]] = []
    if not evidence.empty:
        allowed = {"observing_procedure", "control_condition", "measurement_rated", "measuring_agency"}
        evidence = evidence[evidence["evidence_dimension"].isin(allowed)].copy()
        evidence["record_count"] = pd.to_numeric(evidence["record_count"], errors="coerce")
        evidence["first"] = pd.to_datetime(evidence["first_observed_at_utc"], errors="coerce", utc=True)
        evidence["last"] = pd.to_datetime(evidence["last_observed_at_utc"], errors="coerce", utc=True)
        evidence_values = evidence["evidence_value"].astype("string").str.strip()
        evidence = evidence[
            evidence_values.notna()
            & ~evidence_values.str.lower().isin({"<missing>", "nan", "none"})
        ]
        for dimension, group in evidence.groupby("evidence_dimension", sort=False):
            for _, row in group.sort_values("record_count", ascending=False).head(2).iterrows():
                if pd.notna(row["first"]) and pd.notna(row["last"]):
                    label = f"{dimension.replace('_', ' ')}: {str(row['evidence_value'])[:30]}"
                    evidence_rows.append(
                        (label, row["first"], row["last"], int(row["record_count"]))
                    )
    if evidence_rows:
        for position, (label, first, last, count) in enumerate(evidence_rows):
            ax.plot(
                [first, last],
                [position, position],
                color=OKABE_ITO["sky_blue"],
                linewidth=4,
                solid_capstyle="round",
            )
            ax.scatter([first, last], [position, position], color=OKABE_ITO["blue"], s=18, zorder=3)
            ax.text(
                1.005,
                position,
                f"n={count}",
                transform=ax.get_yaxis_transform(),
                va="center",
                fontsize=8,
            )
        ax.set_yticks(range(len(evidence_rows)), [row[0] for row in evidence_rows], fontsize=8)
        ax.invert_yaxis()
        ax.set_ylim(len(evidence_rows) - 0.5, -0.5)
    else:
        ax.text(0.5, 0.5, "No field-method evidence available locally", ha="center", va="center")
        ax.set_yticks([])
    effective = pd.Series(dtype="datetime64[ns]")
    if not ratings.empty:
        effective_column = next(
            (
                column
                for column in ("effective_begin", "rating_datetime_begin")
                if column in ratings.columns
            ),
            None,
        )
        effective = (
            pd.to_datetime(ratings[effective_column], errors="coerce", utc=True)
            if effective_column
            else pd.Series(dtype="datetime64[ns]")
        ).dropna()
        if not effective.empty:
            rating_date = effective.max()
            ax.axvline(
                rating_date,
                color=OKABE_ITO["orange"],
                linestyle=":",
                linewidth=1.5,
            )
    ax.set_xlabel("Date")
    rating_note = ""
    if not ratings.empty and effective_column and not effective.empty:
        rating_note = f"\nDotted line: current rating snapshot begins {rating_date.date().isoformat()}"
    ax.set_title(
        "Observed field-method and control evidence — evidence layer, not causal attribution"
        + rating_note,
        loc="left",
        fontsize=11,
    )
    date_values = [value for _, first, last, _ in evidence_rows for value in (first, last)]
    if not effective.empty:
        date_values.extend(effective.tolist())
    if date_values:
        date_span_years = (max(date_values) - min(date_values)).days / 365.2425
        if date_span_years >= 3:
            locator = mdates.YearLocator(base=max(1, int(np.ceil(date_span_years / 8))))
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        else:
            locator = mdates.AutoDateLocator(minticks=7, maxticks=14)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.grid(axis="x", alpha=0.20)
    fig.suptitle(
        f"USGS {station_id} — consistency and change-evidence overview",
        fontsize=14,
    )
    fig.subplots_adjust(left=0.25, right=0.96, bottom=0.10, top=0.90, hspace=0.42)
    return _save_figure(fig, output_dir, "consistency_evidence")


def plot_hydrology_summary(
    annual_path: Path,
    high_flow_annual_path: Path,
    summary_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
) -> dict[str, str]:
    """Present annual variability and high-flow screening results."""

    annual = _read_csv_or_empty(annual_path)
    high_flow = _read_csv_or_empty(high_flow_annual_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    annual_cv = summary.get("interannual_cv")
    annual_cv_text = f"{float(annual_cv):.3f}" if annual_cv is not None else "N/A"
    dry_threshold = summary.get("dry_threshold_annual_mean_discharge_cfs")
    wet_threshold = summary.get("wet_threshold_annual_mean_discharge_cfs")
    high_flow_threshold = summary.get("high_flow_threshold_cfs")
    dry_threshold_text = f"{float(dry_threshold):,.0f}" if dry_threshold is not None else "N/A"
    wet_threshold_text = f"{float(wet_threshold):,.0f}" if wet_threshold is not None else "N/A"
    high_flow_threshold_text = (
        f"{float(high_flow_threshold):,.1f}" if high_flow_threshold is not None else "N/A"
    )
    annual_years = (
        pd.to_numeric(annual.get("water_year"), errors="coerce").dropna().astype(int)
        if not annual.empty
        else pd.Series(dtype=int)
    )
    high_flow_years = (
        pd.to_numeric(high_flow.get("water_year"), errors="coerce").dropna().astype(int)
        if not high_flow.empty
        else pd.Series(dtype=int)
    )
    all_year_values = pd.concat([annual_years, high_flow_years], ignore_index=True)
    water_year_index = (
        pd.Index(
            range(int(all_year_values.min()), int(all_year_values.max()) + 1),
            name="water_year",
        )
        if not all_year_values.empty
        else pd.Index([], name="water_year")
    )
    annual_year_set = set(annual_years.tolist())
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    if not annual.empty:
        classes = {
            "dry": OKABE_ITO["vermillion"],
            "normal": OKABE_ITO["blue"],
            "wet": OKABE_ITO["bluish_green"],
        }
        for year_class, group in annual.groupby("hydrologic_year_class", dropna=False):
            class_name = str(year_class) if pd.notna(year_class) else "unclassified"
            class_labels = {
                "dry": f"Dry (≤ Q25: {dry_threshold_text} ft³/s)",
                "normal": "Normal (Q25–Q75)",
                "wet": f"Wet (≥ Q75: {wet_threshold_text} ft³/s)",
            }
            class_series = (
                group.assign(
                    water_year=pd.to_numeric(group["water_year"], errors="coerce")
                )
                .dropna(subset=["water_year"])
                .drop_duplicates("water_year")
                .set_index("water_year")["mean_discharge_cfs"]
                .reindex(water_year_index)
            )
            axes[0].plot(
                water_year_index,
                pd.to_numeric(class_series, errors="coerce"),
                color=classes.get(str(year_class), OKABE_ITO["black"]),
                linewidth=1.0,
                marker="o",
                markersize=2.5,
                label=class_labels.get(class_name, "Unclassified"),
            )
        axes[0].legend(loc="upper right", frameon=False, ncol=3, fontsize=8)
    axes[0].set_ylabel("Annual mean discharge (ft³/s)")
    axes[0].set_title(
        f"Interannual variation — complete WY classes; blank WY = no daily observations; Cv = {annual_cv_text}",
        loc="left",
        fontsize=11,
    )

    if not high_flow.empty:
        high_flow_indexed = (
            high_flow.assign(
                water_year=pd.to_numeric(high_flow["water_year"], errors="coerce")
            )
            .dropna(subset=["water_year"])
            .drop_duplicates("water_year")
            .set_index("water_year")
        )
        event_counts = pd.to_numeric(
            high_flow_indexed["high_flow_event_count"], errors="coerce"
        ).reindex(water_year_index)
        # A year present in annual_hydrology but absent from high_flow_annual
        # had no detected event. A year absent from annual_hydrology remains
        # NaN so missing observations are not mistaken for zero events.
        event_counts.loc[
            event_counts.index.isin(annual_year_set) & event_counts.isna()
        ] = 0
        axes[1].bar(
            water_year_index,
            event_counts,
            color=OKABE_ITO["vermillion"],
            alpha=0.78,
            width=0.82,
        )
    axes[1].set_ylabel("Event count")
    axes[1].set_title(
        f"High-flow episodes — daily discharge above full-record 95th percentile ({high_flow_threshold_text} ft³/s)",
        loc="left",
        fontsize=11,
    )

    if not high_flow.empty:
        largest_peaks = pd.to_numeric(
            high_flow_indexed["largest_high_flow_peak_cfs"], errors="coerce"
        ).reindex(water_year_index)
        axes[2].plot(
            water_year_index,
            largest_peaks,
            color=OKABE_ITO["purple"],
            linewidth=1.1,
            marker="o",
            markersize=2.8,
        )
    axes[2].set_ylabel("Largest event peak (ft³/s)")
    axes[2].set_xlabel("Water Year (WY; Oct 1–Sep 30)")
    axes[2].set_title(
        "Largest high-flow episode peak by Water Year — descriptive flood-response screen",
        loc="left",
        fontsize=11,
    )
    for axis in axes:
        axis.yaxis.set_major_locator(MaxNLocator(integer=True))
        axis.grid(axis="y", alpha=0.22)
    fig.suptitle(f"USGS {station_id} — annual and high-flow statistics", fontsize=14)
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.08, top=0.91, hspace=0.45)
    return _save_figure(fig, output_dir, "hydrology_summary")


def plot_baseflow_summary(
    baseflow_daily_path: Path,
    baseflow_annual_path: Path,
    baseflow_summary_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
) -> dict[str, str]:
    """Show recent baseflow separation and annual baseflow index."""

    daily = pd.read_parquet(baseflow_daily_path) if baseflow_daily_path.exists() else pd.DataFrame()
    annual = _read_csv_or_empty(baseflow_annual_path)
    summary = json.loads(baseflow_summary_path.read_text(encoding="utf-8")) if baseflow_summary_path.exists() else {}
    bfi = summary.get("baseflow_index")
    bfi_text = f"{float(bfi):.3f}" if bfi is not None else "N/A"
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [2.1, 1]})
    if not daily.empty:
        daily["date"] = pd.to_datetime(daily["observed_date_local"], errors="coerce")
        for column in ("discharge_cfs", "baseflow_cfs"):
            daily[column] = pd.to_numeric(daily[column], errors="coerce")
        daily = daily.dropna(subset=["date"])
        latest = daily["date"].max().date()
        window = recent_water_year_window(latest, 10)
        recent = daily[
            (daily["date"].dt.date >= window.start) & (daily["date"].dt.date <= window.end)
        ]
        recent_index = pd.date_range(window.start, window.end, freq="D")
        recent_indexed = (
            recent.drop_duplicates("date")
            .set_index("date")[["discharge_cfs", "baseflow_cfs"]]
            .reindex(recent_index)
        )
        recent_valid_count = int(recent_indexed["discharge_cfs"].notna().sum())
        recent_missing_count = int(recent_indexed["discharge_cfs"].isna().sum())
        axes[0].plot(
            recent_indexed.index,
            recent_indexed["discharge_cfs"],
            color=OKABE_ITO["blue"],
            linewidth=0.65,
            label="Daily discharge (observed values; gaps retained)",
        )
        axes[0].plot(
            recent_indexed.index,
            recent_indexed["baseflow_cfs"],
            color=OKABE_ITO["vermillion"],
            linewidth=1.1,
            label="Separated baseflow (gaps retained)",
        )
        axes[0].set_title(
            f"Recent 10 Water Years | {window.start.isoformat()} to {window.end.isoformat()} | observed n = {recent_valid_count:,}; missing days = {recent_missing_count:,}",
            loc="left",
            fontsize=10,
        )
        locator = mdates.AutoDateLocator(minticks=8, maxticks=18)
        axes[0].xaxis.set_major_locator(locator)
        axes[0].xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        axes[0].legend(loc="upper left", frameon=False, fontsize=8.5)
    else:
        axes[0].text(0.5, 0.5, "No baseflow separation output", ha="center", va="center")
        axes[0].set_axis_off()
    axes[0].set_ylabel("Discharge (ft³/s)")

    if not annual.empty:
        annual["baseflow_index"] = pd.to_numeric(annual["baseflow_index"], errors="coerce")
        annual["water_year"] = pd.to_numeric(annual["water_year"], errors="coerce")
        annual = annual.dropna(subset=["water_year"]).drop_duplicates("water_year")
        annual_index = pd.Index(
            range(int(annual["water_year"].min()), int(annual["water_year"].max()) + 1),
            name="water_year",
        )
        annual_bfi = annual.set_index("water_year")["baseflow_index"].reindex(annual_index)
        axes[1].plot(
            annual_index,
            annual_bfi,
            color=OKABE_ITO["purple"],
            linewidth=1.1,
            marker="o",
            markersize=2.8,
            label="Annual baseflow index",
        )
        axes[1].set_ylim(0, 1)
        axes[1].legend(loc="upper right", frameon=False, fontsize=8.5)
    axes[1].set_ylabel("Baseflow index\n(baseflow / total flow)")
    axes[1].set_xlabel("Water Year (WY; Oct 1–Sep 30)")
    axes[1].set_title(
        f"Annual baseflow index — full-period BFI = {bfi_text}; screening indicator only",
        loc="left",
        fontsize=10,
    )
    for axis in axes:
        axis.yaxis.set_major_locator(MaxNLocator(integer=False))
        axis.grid(axis="y", alpha=0.22)
    fig.suptitle(f"USGS {station_id} — baseflow separation and ecological-flow screen", fontsize=14)
    fig.subplots_adjust(left=0.11, right=0.98, bottom=0.09, top=0.90, hspace=0.42)
    return _save_figure(fig, output_dir, "baseflow_summary")


def plot_flow_duration_curve(
    curve_path: Path,
    quantiles_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
) -> dict[str, str]:
    """Plot the empirical daily flow-duration curve and management quantiles."""

    curve = _read_csv_or_empty(curve_path)
    quantiles = _read_csv_or_empty(quantiles_path)
    fig, ax = plt.subplots(figsize=(10.5, 6.5))
    if not curve.empty:
        curve["exceedance_probability_pct"] = pd.to_numeric(curve["exceedance_probability_pct"], errors="coerce")
        curve["discharge_cfs"] = pd.to_numeric(curve["discharge_cfs"], errors="coerce")
        curve = curve.dropna(subset=["exceedance_probability_pct", "discharge_cfs"])
        curve = curve[curve["discharge_cfs"] > 0]
        ax.plot(
            curve["exceedance_probability_pct"],
            curve["discharge_cfs"],
            color=OKABE_ITO["blue"],
            linewidth=1.2,
            label="Empirical daily FDC",
        )
    selected = quantiles[
        quantiles["exceedance_probability_pct"].isin([50, 75, 90, 95])
    ].copy() if not quantiles.empty else pd.DataFrame()
    if not selected.empty:
        selected["exceedance_probability_pct"] = pd.to_numeric(selected["exceedance_probability_pct"], errors="coerce")
        selected["discharge_cfs"] = pd.to_numeric(selected["discharge_cfs"], errors="coerce")
        for _, row in selected.sort_values("exceedance_probability_pct").iterrows():
            level = int(row["exceedance_probability_pct"])
            value = float(row["discharge_cfs"])
            ax.scatter(
                level,
                value,
                s=34,
                color=OKABE_ITO["vermillion"],
                zorder=3,
                label=f"D{level} = {value:,.0f} ft³/s",
            )
    if curve.empty:
        ax.text(0.5, 0.5, "No daily flow-duration curve available", ha="center", va="center")
    ax.set_yscale("log")
    ax.set_xlim(0, 100)
    ax.set_xlabel("Exceedance probability (% of observed days)")
    ax.set_ylabel("Daily mean discharge (ft³/s; log scale)")
    ax.set_title(
        "Empirical flow-duration curve — Dp is the discharge equaled or exceeded on approximately p% of observed days",
        loc="left",
        fontsize=10.5,
    )
    ax.grid(which="major", alpha=0.22)
    ax.legend(loc="upper right", frameon=False, fontsize=8.5, ncol=2)
    fig.suptitle(f"USGS {station_id} — daily flow-duration curve", fontsize=14)
    fig.subplots_adjust(left=0.12, right=0.98, bottom=0.11, top=0.88)
    return _save_figure(fig, output_dir, "flow_duration_curve")


def plot_water_management_summary(
    management_path: Path,
    low_flow_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
    annual_runoff_path: Path | None = None,
) -> dict[str, str]:
    """Plot annual runoff probabilities and multi-duration low-flow screens."""

    management = _read_csv_or_empty(management_path)
    low_flow = _read_csv_or_empty(low_flow_path)
    annual_runoff = (
        _read_csv_or_empty(annual_runoff_path)
        if annual_runoff_path is not None
        else pd.DataFrame()
    )
    fig = plt.figure(figsize=(13, 10), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=(1.0, 1.05))
    time_ax = fig.add_subplot(grid[0, 0])
    probability_ax = fig.add_subplot(grid[0, 1])
    low_ax = fig.add_subplot(grid[1, :])

    class_colors = {
        "dry": OKABE_ITO["vermillion"],
        "normal": OKABE_ITO["blue"],
        "wet": OKABE_ITO["bluish_green"],
    }
    if not annual_runoff.empty:
        annual_runoff["water_year"] = pd.to_numeric(annual_runoff["water_year"], errors="coerce")
        annual_runoff["annual_runoff_acre_ft"] = pd.to_numeric(
            annual_runoff["annual_runoff_acre_ft"], errors="coerce"
        )
        annual_runoff = annual_runoff.dropna(subset=["water_year", "annual_runoff_acre_ft"])
        annual_runoff = annual_runoff.sort_values("water_year")
        time_ax.plot(
            annual_runoff["water_year"],
            annual_runoff["annual_runoff_acre_ft"],
            color="#777777",
            linewidth=0.7,
            alpha=0.65,
            zorder=1,
        )
        for year_class, group in annual_runoff.groupby(
            annual_runoff.get("hydrologic_year_class", pd.Series("unclassified", index=annual_runoff.index)).fillna("unclassified")
        ):
            class_name = str(year_class)
            time_ax.scatter(
                group["water_year"],
                group["annual_runoff_acre_ft"],
                s=20,
                color=class_colors.get(class_name, "#777777"),
                label=class_name.replace("_", " ").title(),
                zorder=2,
            )
        time_ax.set_title(
            f"Annual runoff by Water Year — complete years (n = {len(annual_runoff):,})",
            loc="left",
            fontsize=10.5,
        )
        time_ax.legend(loc="upper right", frameon=False, fontsize=8)
    else:
        time_ax.text(0.5, 0.5, "No annual runoff exceedance table", ha="center", va="center")
    time_ax.set_xlabel("Water Year (WY; Oct 1–Sep 30)")
    time_ax.set_ylabel("Annual runoff (acre-ft)")
    time_ax.grid(axis="y", alpha=0.22)

    if not annual_runoff.empty:
        annual_runoff["exceedance_probability_pct"] = pd.to_numeric(
            annual_runoff["exceedance_probability_pct"], errors="coerce"
        )
        annual_runoff = annual_runoff.dropna(subset=["exceedance_probability_pct"])
        ranked = annual_runoff.sort_values("exceedance_probability_pct")
        probability_ax.plot(
            ranked["exceedance_probability_pct"],
            ranked["annual_runoff_acre_ft"],
            color=OKABE_ITO["blue"],
            linewidth=1.0,
            alpha=0.8,
            zorder=1,
        )
        for year_class, group in ranked.groupby(
            ranked.get("hydrologic_year_class", pd.Series("unclassified", index=ranked.index)).fillna("unclassified")
        ):
            class_name = str(year_class)
            probability_ax.scatter(
                group["exceedance_probability_pct"],
                group["annual_runoff_acre_ft"],
                s=18,
                color=class_colors.get(class_name, "#777777"),
                alpha=0.78,
                label=class_name.replace("_", " ").title(),
                zorder=2,
            )
    if not management.empty:
        management["exceedance_probability_pct"] = pd.to_numeric(
            management["exceedance_probability_pct"], errors="coerce"
        )
        management["annual_runoff_acre_ft"] = pd.to_numeric(
            management["annual_runoff_acre_ft"], errors="coerce"
        )
        management = management.dropna(
            subset=["exceedance_probability_pct", "annual_runoff_acre_ft"]
        )
        for _, row in management.iterrows():
            level = int(row["exceedance_probability_pct"])
            offsets = {
                50: (0, -24),
                75: (0, 10),
                90: (-5, 10),
                95: (5, 10),
            }
            offset_x, offset_y = offsets.get(level, (0, 10))
            vertical_alignment = "top" if level == 50 else "bottom"
            horizontal_alignment = "right" if level == 90 else (
                "left" if level == 95 else "center"
            )
            probability_ax.scatter(
                level,
                row["annual_runoff_acre_ft"],
                marker="x",
                s=40,
                linewidth=1.2,
                color=OKABE_ITO["black"],
                zorder=3,
            )
            probability_ax.annotate(
                f"P{level}: {row['annual_runoff_acre_ft']:,.0f} acre-ft",
                (level, row["annual_runoff_acre_ft"]),
                xytext=(offset_x, offset_y),
                textcoords="offset points",
                ha=horizontal_alignment,
                va=vertical_alignment,
                fontsize=8,
            )
    probability_ax.set_title(
        "Annual runoff versus exceedance probability — each point is one complete WY",
        loc="left",
        fontsize=10.5,
    )
    probability_ax.set_xlabel("Exceedance probability (%)")
    probability_ax.set_ylabel("Annual runoff (acre-ft)")
    probability_ax.set_xlim(0, 100)
    probability_ax.grid(axis="y", alpha=0.22)
    if not annual_runoff.empty:
        probability_ax.legend(loc="upper right", frameon=False, fontsize=7.5)
    if not annual_runoff.empty:
        probability_ax.text(
            0.02,
            0.03,
            "Year, runoff, rank, and probability: annual_runoff_exceedance.csv",
            transform=probability_ax.transAxes,
            fontsize=7.5,
            color="#555555",
        )

    duration_colors = {
        7: OKABE_ITO["blue"],
        14: OKABE_ITO["vermillion"],
        30: OKABE_ITO["bluish_green"],
    }
    if not low_flow.empty and "duration_days" in low_flow:
        low_flow["duration_days"] = pd.to_numeric(low_flow["duration_days"], errors="coerce")
        low_flow["nonexceedance_probability_pct"] = pd.to_numeric(
            low_flow["nonexceedance_probability_pct"], errors="coerce"
        )
        low_flow["annual_min_mean_discharge_cfs"] = pd.to_numeric(
            low_flow["annual_min_mean_discharge_cfs"], errors="coerce"
        )
        low_flow = low_flow.dropna(
            subset=[
                "duration_days",
                "nonexceedance_probability_pct",
                "annual_min_mean_discharge_cfs",
            ]
        )
        for duration, group in low_flow.groupby("duration_days"):
            duration_int = int(duration)
            group = group.sort_values("nonexceedance_probability_pct")
            low_ax.plot(
                group["nonexceedance_probability_pct"],
                group["annual_min_mean_discharge_cfs"],
                color=duration_colors.get(duration_int, OKABE_ITO["purple"]),
                marker="o",
                markersize=4,
                linewidth=1.3,
                label=f"{duration_int}-day minimum mean Q",
            )
    else:
        low_ax.text(0.5, 0.5, "No multi-duration low-flow table", ha="center", va="center")
    low_ax.set_title(
        "Annual minimum k-day mean discharge — empirical non-exceedance screen",
        loc="left",
        fontsize=10.5,
    )
    low_ax.set_xlabel("Non-exceedance probability (% of complete Water Years)")
    low_ax.set_ylabel("Annual minimum k-day mean Q (ft³/s)")
    low_ax.set_xlim(0, 55)
    low_ax.set_ylim(bottom=0)
    low_ax.grid(axis="y", alpha=0.22)
    if low_ax.get_legend_handles_labels()[0]:
        low_ax.legend(loc="upper left", frameon=False, ncol=3, fontsize=8.5)

    for axis in (time_ax, probability_ax, low_ax):
        axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    fig.suptitle(f"USGS {station_id} — water-management planning statistics", fontsize=14)
    return _save_figure(fig, output_dir, "water_management_summary")


def plot_correlation_summary(
    correlation_path: Path,
    output_dir: Path,
    station_id: str = "USGS station",
) -> dict[str, str]:
    """Plot Pearson/Spearman associations with sample sizes."""

    table = _read_csv_or_empty(correlation_path)
    labels = {
        "lag_1_calendar_days": "Discharge persistence, 1-day lag",
        "lag_7_calendar_days": "Discharge persistence, 7-day lag",
        "lag_30_calendar_days": "Discharge persistence, 30-day lag",
        "linear_scale": "Stage–discharge, linear scale",
        "log10_scale": "Stage–discharge, log10 scale",
        "relative_error_vs_time": "Field published error vs time",
    }
    if not table.empty:
        table["display"] = table.apply(
            lambda row: f"{str(row['analysis']).replace('_', ' ').title()} — {labels.get(str(row['comparison']), str(row['comparison']).replace('_', ' '))}",
            axis=1,
        )
        table = table.iloc[::-1].reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(11, max(5.5, 0.62 * max(len(table), 1) + 1.8)))
    if table.empty:
        ax.text(0.5, 0.5, "No correlation results", ha="center", va="center")
        ax.set_axis_off()
    else:
        positions = list(range(len(table)))
        pearson = pd.to_numeric(table["pearson_r"], errors="coerce")
        spearman = pd.to_numeric(table["spearman_r"], errors="coerce")
        for position, p_value, s_value in zip(positions, pearson, spearman):
            if pd.notna(p_value) and pd.notna(s_value):
                ax.plot([p_value, s_value], [position, position], color="#999999", linewidth=1.2, zorder=1)
            if pd.notna(p_value):
                ax.scatter(p_value, position, color=OKABE_ITO["blue"], s=32, zorder=3)
            if pd.notna(s_value):
                ax.scatter(s_value, position, color=OKABE_ITO["vermillion"], s=32, marker="D", zorder=3)
            ax.text(
                1.03,
                position,
                f"n={int(table.loc[position, 'n']):,}",
                transform=ax.get_yaxis_transform(),
                va="center",
                fontsize=8.5,
            )
        ax.axvline(0, color="#444444", linewidth=0.8)
        ax.set_xlim(-1.0, 1.18)
        ax.set_yticks(positions, table["display"], fontsize=8.5)
        ax.set_xlabel("Correlation coefficient (r; Pearson and Spearman)")
        ax.set_title(
            "Correlation screening — association only; no causal or rating-curve conclusion",
            loc="left",
            fontsize=11,
        )
        ax.legend(
            handles=[
                Line2D([0], [0], marker="o", color="none", markerfacecolor=OKABE_ITO["blue"], label="Pearson r"),
                Line2D([0], [0], marker="D", color="none", markerfacecolor=OKABE_ITO["vermillion"], label="Spearman ρ"),
            ],
            loc="lower right",
            frameon=False,
            fontsize=8.5,
        )
        ax.grid(axis="x", alpha=0.22)
    fig.suptitle(f"USGS {station_id} — correlation results", fontsize=14)
    fig.subplots_adjust(left=0.36, right=0.90, bottom=0.12, top=0.88)
    return _save_figure(fig, output_dir, "correlation_summary")


def build_stage_discharge_pairs(daily_path: Path, unit_path: Path) -> pd.DataFrame:
    if not unit_path.exists():
        return pd.DataFrame(
            columns=[
                "observed_date_local",
                "discharge_cfs",
                "discharge_quality_code",
                "gage_height_ft",
                "pair_method",
            ]
        )
    daily = pd.read_parquet(daily_path)
    daily["discharge_cfs"] = pd.to_numeric(daily["value"], errors="coerce")
    daily = daily[["observed_date_local", "discharge_cfs", "quality_code"]].rename(
        columns={"quality_code": "discharge_quality_code"}
    )
    stage = _daily_stage(unit_path)
    pairs = daily.merge(stage, on="observed_date_local", how="inner")
    pairs = pairs.dropna(subset=["discharge_cfs", "gage_height_ft"])
    pairs = pairs[(pairs["discharge_cfs"] > 0) & (pairs["gage_height_ft"] > 0)].copy()
    pairs["pair_method"] = "same_local_calendar_date; unit gage height aggregated by daily mean"
    return pairs.sort_values("observed_date_local").reset_index(drop=True)


def plot_stage_discharge(
    daily_path: Path,
    unit_path: Path,
    output_dir: Path,
    field_measurements: pd.DataFrame | None = None,
    station_id: str = "USGS station",
) -> dict[str, str]:
    pairs = build_stage_discharge_pairs(daily_path, unit_path)
    fig, ax = plt.subplots(figsize=(8.5, 6.5))
    ax.scatter(
        pairs["gage_height_ft"],
        pairs["discharge_cfs"],
        s=5,
        alpha=0.20,
        color=OKABE_ITO["blue"],
        rasterized=True,
        label=f"All paired daily observations (n = {len(pairs):,})",
    )
    field_count = 0
    if field_measurements is not None and not field_measurements.empty:
        field_count = len(field_measurements)
        ax.scatter(
            field_measurements["gage_height_ft"],
            field_measurements["discharge_cfs"],
            s=28,
            color=OKABE_ITO["vermillion"],
            edgecolor="white",
            linewidth=0.5,
            label=f"Field measurements / ground truth (n = {field_count:,})",
            zorder=3,
        )
    if not pairs.empty or (field_measurements is not None and not field_measurements.empty):
        ax.set_xscale("log")
        ax.set_yscale("log")
    else:
        ax.text(
            0.5,
            0.5,
            "Continuous gage height not downloaded\n(use --with-continuous)",
            transform=ax.transAxes,
            ha="center",
            va="center",
            color="#555555",
        )
    ax.set_xlabel("Gage height (ft)")
    ax.set_ylabel("Discharge (ft³/s)")
    ax.set_title(
        f"USGS {station_id} — stage–discharge validation | daily pairs n = {len(pairs):,}; field n = {field_count:,}"
    )
    ax.grid(which="major", alpha=0.25)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()
    pairs_output = output_dir.parent / "observations" / "stage_discharge_daily_pairs.parquet"
    pairs_output.parent.mkdir(parents=True, exist_ok=True)
    pairs.to_parquet(pairs_output, index=False)
    return _save_figure(fig, output_dir, "stage_discharge")


def validate_figure_files(figure_dir: Path) -> list[str]:
    """Perform a lightweight file-level visual preflight."""

    from PIL import Image

    checked: list[str] = []
    for path in sorted(figure_dir.glob("*.png")):
        with Image.open(path) as image:
            if image.width < 1000 or image.height < 500:
                raise ValueError(f"Figure resolution is too small: {path}")
            if image.getbbox() is None:
                raise ValueError(f"Figure appears blank: {path}")
        checked.append(str(path))
    return checked


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    """Read an optional derived table, including a valid empty CSV result."""

    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def generate_station_figures(
    station_root: Path,
    output_root: Path,
    field_measurements: pd.DataFrame | None = None,
    basin_context: dict[str, Path] | None = None,
) -> dict[str, Any]:
    records = available_data_from_json(station_root / "metadata" / "available_data.json")
    daily_path = station_root / "observations" / "daily_discharge.parquet"
    unit_path = station_root / "observations" / "unit_00065.parquet"
    annual_peak_path = station_root / "hydrology" / "annual_peak_summary.csv"
    summary_path = station_root / "quality" / "series_summary.csv"
    field_pairs_path = station_root / "observations" / "field_stage_discharge_pairs.parquet"
    flood_frequency_path = station_root / "hydrology" / "flood_frequency.csv"
    annual_hydrology_path = station_root / "hydrology" / "annual_hydrology.csv"
    high_flow_annual_path = station_root / "hydrology" / "high_flow_annual.csv"
    hydrology_summary_path = station_root / "hydrology" / "hydrology_summary.json"
    baseflow_daily_path = station_root / "hydrology" / "baseflow_daily.parquet"
    baseflow_annual_path = station_root / "hydrology" / "baseflow_annual.csv"
    baseflow_summary_path = station_root / "hydrology" / "baseflow_summary.json"
    fdc_path = station_root / "hydrology" / "flow_duration_curve.csv"
    fdc_quantiles_path = station_root / "hydrology" / "flow_duration_quantiles.csv"
    management_path = station_root / "hydrology" / "water_management_baselines.csv"
    low_flow_path = station_root / "hydrology" / "low_flow_management.csv"
    annual_runoff_path = station_root / "hydrology" / "annual_runoff_exceedance.csv"
    correlation_path = station_root / "hydrology" / "correlation_summary.csv"
    method_evidence_path = station_root / "hydrology" / "method_evidence_summary.csv"
    change_points_path = station_root / "hydrology" / "change_point_candidates.csv"
    rating_summary_path = station_root / "metadata" / "rating_version_summary.csv"
    field_measurements_path = station_root / "observations" / "field_measurements.parquet"
    station_metadata_path = station_root / "metadata" / "station_metadata.json"
    watershed_path = station_root / "spatial" / "contributing_watershed.geojson"
    flow_network_path = station_root / "spatial" / "flow_network.geojson"
    figure_dir = output_station_root(output_root, station_root) / "figures"
    field_measurements = (
        pd.read_parquet(field_pairs_path) if field_pairs_path.exists() else None
    )
    station_id = station_root.name.removeprefix("USGS_")
    observed_intervals = _observed_intervals(station_root)
    figures = {
        "coverage": plot_coverage(records, figure_dir, station_id, observed_intervals),
        "completeness": plot_completeness(summary_path, figure_dir, station_id, records),
        "normalized_monthly_discharge": plot_normalized_monthly_discharge(
            daily_path, figure_dir, station_id
        ),
        "hydrograph": plot_hydrograph(
            daily_path,
            unit_path,
            figure_dir,
            annual_peak_path=annual_peak_path,
            station_id=station_id,
        ),
        "stage_discharge": plot_stage_discharge(
            daily_path, unit_path, figure_dir, field_measurements, station_id
        ),
    }
    if flood_frequency_path.exists():
        figures["flood_frequency"] = plot_flood_frequency(
            flood_frequency_path, figure_dir, station_id
        )
    if annual_hydrology_path.exists():
        figures["hydrology_summary"] = plot_hydrology_summary(
            annual_hydrology_path,
            high_flow_annual_path,
            hydrology_summary_path,
            figure_dir,
            station_id,
        )
    if baseflow_daily_path.exists() and baseflow_annual_path.exists():
        figures["baseflow_summary"] = plot_baseflow_summary(
            baseflow_daily_path,
            baseflow_annual_path,
            baseflow_summary_path,
            figure_dir,
            station_id,
        )
    if fdc_path.exists() and fdc_quantiles_path.exists():
        figures["flow_duration_curve"] = plot_flow_duration_curve(
            fdc_path,
            fdc_quantiles_path,
            figure_dir,
            station_id,
        )
    if management_path.exists() and low_flow_path.exists():
        figures["water_management_summary"] = plot_water_management_summary(
            management_path,
            low_flow_path,
            figure_dir,
            station_id,
            annual_runoff_path,
        )
    if correlation_path.exists():
        figures["correlation_summary"] = plot_correlation_summary(
            correlation_path, figure_dir, station_id
        )
    if annual_hydrology_path.exists() and method_evidence_path.exists():
        figures["consistency_evidence"] = plot_consistency_evidence(
            annual_hydrology_path,
            field_measurements_path,
            method_evidence_path,
            change_points_path,
            rating_summary_path,
            figure_dir,
            station_id,
        )
    if station_metadata_path.exists() and watershed_path.exists() and flow_network_path.exists():
        figures["station_watershed_network"] = plot_station_watershed_network(
            station_metadata_path,
            watershed_path,
            flow_network_path,
            figure_dir,
            basin_context=basin_context,
        )
    checked = validate_figure_files(figure_dir)
    write_json(
        figure_dir.parent / "figure_manifest.json",
        {"figures": figures, "validated_pngs": checked},
    )
    return figures
