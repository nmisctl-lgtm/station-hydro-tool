"""Deterministic, local Markdown explanations for station hydrology outputs."""

from __future__ import annotations

import math
import json
from pathlib import Path
from typing import Any

import pandas as pd


def _number(value: Any, digits: int = 0) -> str:
    if value is None:
        return "N/A"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(numeric):
        return "N/A"
    return f"{numeric:,.{digits}f}"


def _date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return parsed.date().isoformat() if pd.notna(parsed) else "N/A"


def _table_value(column: str, value: Any) -> str:
    if pd.isna(value):
        return ""
    if column == "water_year" or column == "duration_days":
        return str(int(value))
    if column == "rank_descending_runoff" or column == "n_complete_water_years":
        return f"{int(value):,}"
    if column.endswith("_acre_ft"):
        return _number(value)
    if column.endswith("_cfs") or column == "discharge_cfs":
        return _number(value, 1)
    if column.endswith("_probability_pct"):
        return _number(value, 2)
    if column.endswith("_r"):
        return _number(value, 3)
    return str(value).replace("|", "\\|")


def _markdown_table(frame: pd.DataFrame, columns: list[str], limit: int = 12) -> list[str]:
    if frame.empty:
        return ["No rows were produced."]
    visible = frame.loc[:, [column for column in columns if column in frame]].head(limit).copy()
    headers = list(visible.columns)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for _, row in visible.iterrows():
        values = [_table_value(column, row[column]) for column in headers]
        lines.append("| " + " | ".join(values) + " |")
    if len(frame) > limit:
        lines.append(f"\nShowing the first {limit} rows; the complete table is retained beside this file.")
    return lines


def _load_catalog_context(station_root: Path) -> tuple[Path, dict[str, Any], dict[str, Any]] | None:
    """Find the project catalog and the matching ranked station entry."""

    catalog_name = "sj_basin_usgs_station_directory.json"
    resolved_root = station_root.resolve()
    # Walk upward so this remains correct for both the grouped layout
    # (data/stations/<river>_<count>/.../USGS_<id>) and the legacy flat layout.
    candidates = [
        ancestor / "data" / "catalog" / catalog_name
        for ancestor in (resolved_root.parent, *resolved_root.parents)
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        station_id = resolved_root.name.removeprefix("USGS_")
        entry = next(
            (item for item in payload.get("stations", []) if item.get("station_id") == station_id),
            None,
        )
        if entry is not None:
            return path, payload, entry
    return None


def build_hydrology_interpretation(
    station_root: Path, result: dict[str, Any]
) -> str:
    """Build a deterministic explanation from the current derived results."""

    station_id = station_root.name.removeprefix("USGS_")
    summary = result.get("summary", {})
    annual = result.get("annual", pd.DataFrame())
    runoff = result.get("annual_runoff_exceedance", pd.DataFrame())
    low_flow = result.get("low_flow_management", pd.DataFrame())
    management = result.get("management_baselines", pd.DataFrame())
    fdc_quantiles = result.get("flow_duration_quantiles", pd.DataFrame())
    correlations = result.get("correlations", pd.DataFrame())
    changes = result.get("change_points", pd.DataFrame())
    method_evidence = result.get("method_evidence", pd.DataFrame())
    baseflow = summary.get("baseflow", {})
    daily_coverage = summary.get("daily_coverage", {})
    long_gap_lines = [
        f"- {gap['start_date']} to {gap['end_date']} ({int(gap['missing_days']):,} missing days)"
        for gap in daily_coverage.get("long_gaps", [])
    ] or ["- None"]
    catalog_context = _load_catalog_context(station_root)

    complete_count = int(summary.get("complete_water_year_count", 0))
    annual_count = int(summary.get("water_year_count", 0))
    lines = [
        f"# Hydrology interpretation — USGS {station_id}",
        "",
        "This file is generated locally from the canonical station artifacts. It records the method, current result, interpretation, and limitations for each analysis. It is explanatory evidence, not a regulatory determination or a design-value certification.",
        "",
        "## 1. Scope and data provenance",
        "",
        f"- Station: `USGS-{station_id}`.",
        f"- Daily source: `{summary.get('daily_source', 'N/A')}`.",
        f"- Water Year definition: {summary.get('water_year_definition', 'October 1 through September 30.')}",
        f"- Annual records: {annual_count}; complete Water Years used for annual statistics: {complete_count}.",
        "- Missing values and date gaps are not silently imputed. Derived tables retain the valid sample size and method text.",
        "",
    ]
    if catalog_context is not None:
        catalog_path, catalog_payload, catalog_entry = catalog_context
        importance = catalog_entry["importance"]
        lines.extend(
            [
                "## 1.1 Project directory and priority",
                "",
                f"- Project directory rank: **{catalog_entry['rank']} of {catalog_payload.get('station_count', 'N/A')}**; tier `{importance['importance_tier']}`; screening score `{importance['screening_priority_score']}`.",
                f"- Catalog role: {importance['basin_role']}; declared daily-discharge record: {importance['daily_discharge_record_years']:.1f} years; status screen: `{importance['operational_status_screen']}`.",
                f"- Why this station is scheduled here: {'; '.join(importance['importance_reasons'])}.",
                "- Ranking algorithm: basin role (mainstem > major Animas/La Plata tributary > other), declared daily-record length, recency of core hydrologic data, analysis readiness, and drainage area. This is a transparent project-work order, not an official USGS importance rating.",
                f"- Full directory: `{catalog_path}`. The station entry preserves the seed metadata, USGS source artifacts, validation result, Available Data series, and ranking evidence.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## 1.1 Project directory and priority",
                "",
                "The ranked SJ Basin directory was not found beside this station package; priority context is therefore omitted from this narrative.",
                "",
            ]
        )
    lines.extend(
        [
        "## 2. Coverage and completeness",
        "",
        "The provider-declared coverage figure describes the date ranges advertised by USGS. The observation-completeness figure measures the locally retained observations against the relevant expected daily, annual, or 15-minute grid. Site visits and event/sample series are not treated as regular observations.",
        "",
        f"Daily coverage check: {int(daily_coverage.get('observed_day_count', 0)):,} observed dates across a {int(daily_coverage.get('calendar_span_days', 0)):,}-day calendar span; {int(daily_coverage.get('missing_day_count', 0)):,} calendar days are absent. The coverage plot shows these as separate observed segments, and the time-series plots retain them as gaps.",
        "",
        "Long daily gaps (at least 7 consecutive calendar days):",
        *long_gap_lines,
        "",
        "Interpretation: a high completeness percentage supports temporal summaries, but it does not by itself establish measurement accuracy, rating-curve stability, or representativeness of hydrologic conditions.",
        "",
        "## 3. Annual variability and Water-Year classes",
        "",
        f"Method: annual mean daily discharge and annual runoff are calculated by Water Year. A complete Water Year requires {summary.get('complete_water_year_rule', 'the configured minimum daily completeness')}. The interannual coefficient of variation is { _number(summary.get('interannual_cv'), 3) }.",
        f"Result: the complete-year Q25 threshold is {_number(summary.get('dry_threshold_annual_mean_discharge_cfs'))} ft³/s and Q75 is {_number(summary.get('wet_threshold_annual_mean_discharge_cfs'))} ft³/s.",
        "Interpretation: `dry` means annual mean Q ≤ Q25, `normal` means Q25 < annual mean Q < Q75, and `wet` means annual mean Q ≥ Q75. These are station-relative empirical classes, not drought-regulation categories.",
        "",
        "## 4. Annual runoff and exceedance probability",
        "",
        "Every complete Water Year is ranked by annual runoff. The descending rank m uses the empirical exceedance probability m/(N+1), so each row in `annual_runoff_exceedance.csv` contains both the Water Year and its probability.",
        "",
        ]
    )
    lines.extend(
        _markdown_table(
            runoff,
            [
                "water_year",
                "annual_runoff_acre_ft",
                "exceedance_probability_pct",
                "hydrologic_year_class",
            ],
        )
    )
    lines.extend(
        [
            "",
            "Interpretation: smaller exceedance probabilities correspond to larger annual runoff; P50/P75/P90/P95 are empirical planning reference levels, not guaranteed future supplies.",
            "",
            "## 5. Monthly and recent Water-Year pattern",
            "",
            "The monthly table first calculates each Water-Year/month mean and volume, then summarizes the historical distribution. The current Water Year is shown separately so current conditions can be compared with the historical monthly distribution without treating an incomplete year as a complete year.",
            "",
            "Interpretation: the monthly result describes timing and seasonality of available flow; it does not separate natural inflow from regulation, diversion, return flow, or storage operations.",
            "",
            "## 6. High-flow screening and flood frequency",
            "",
            f"High-flow events are consecutive observed daily values above the full-record 95th percentile ({_number(summary.get('high_flow_threshold_cfs'), 1)} ft³/s); missing dates break an event. The run contains {int(summary.get('high_flow_event_count', 0))} screened events.",
        ]
    )
    if summary.get("flood_frequency_source") == "daily_annual_maxima_fallback":
        lines.append(
            f"No provider annual-peak series was available. The flood-frequency screen uses annual maxima derived from daily discharge for complete Water Years ({int(summary.get('flood_frequency_systematic_years', 0))} values available to the fit); this is a descriptive proxy, not an official annual-peak record."
        )
    else:
        lines.append(
            f"The flood-frequency screen uses systematic annual maxima, at-site log10 moments, a Pearson III quantile curve, and a seeded parametric bootstrap. {int(summary.get('flood_frequency_excluded_historic_peaks', 0))} provider-flagged historic record(s) are excluded from the systematic fit."
        )
    if not runoff.empty:
        pass
    frequency = result.get("flood_frequency", pd.DataFrame())
    empirical = frequency[
        frequency.get("method", pd.Series(dtype="string"))
        == "empirical_ams_weibull_plotting_position"
    ] if not frequency.empty else pd.DataFrame()
    if not empirical.empty:
        peak = empirical.loc[empirical["estimate_discharge_cfs"].idxmax()]
        lines.append(
            f"The largest systematic observed peak is {_number(peak.get('estimate_discharge_cfs'))} ft³/s on {_date(peak.get('annual_peak_date'))}. Its empirical plotting-position return period is {_number(peak.get('return_period_years'))} years; the fitted LP3 inverse is {_number(peak.get('fitted_return_period_years'))} years. The latter is a far-tail model extrapolation, not an observed recurrence interval."
        )
    lines.extend(
        [
            "",
            "Interpretation: the event count is a descriptive response screen. The frequency curve is not an official Bulletin 17C design result until historical/censored peaks, low-outlier treatment, EMA inputs, regional skew, and the reference implementation are reviewed.",
            "",
            "## 7. Baseflow and ecological-flow screen",
            "",
            f"Method: {baseflow.get('method', 'Lyne-Hollick recursive digital filter')}, alpha={_number(baseflow.get('alpha'), 3)}, passes={baseflow.get('passes', 'N/A')}. BFI is the separated baseflow volume divided by total valid daily-flow volume.",
            f"Result: full-period BFI = {_number(baseflow.get('baseflow_index'), 3)}; valid days = {_number(baseflow.get('valid_days'))}.",
            "Interpretation: BFI and separated baseflow are indicators of delayed-flow contribution. They are not direct groundwater measurements and are not ecological-flow targets. Ecological-flow work must add seasonal habitat needs, temperature, water quality, connectivity, and applicable legal or agency requirements.",
            "",
            "## 8. Flow-Duration Curve",
            "",
            "The empirical daily FDC sorts valid daily mean discharge and reports the discharge equaled or exceeded on approximately p% of observed days. Date order is intentionally ignored.",
        ]
    )
    lines.extend(_markdown_table(fdc_quantiles, ["exceedance_probability_pct", "discharge_cfs", "interpretation"]))
    lines.extend(
        [
            "",
            "Interpretation: D50, D75, D90, and D95 describe frequency of observed daily availability; they do not account for demand, storage, legal constraints, or future nonstationarity.",
            "",
            "## 9. Low-flow and water-management baselines",
            "",
            "Annual minimum k-day mean flow is calculated for k = 7, 14, and 30 days using valid consecutive daily observations. The probability curves use empirical non-exceedance probabilities across complete Water Years.",
        ]
    )
    lines.extend(
        _markdown_table(
            low_flow,
            [
                "duration_days",
                "nonexceedance_probability_pct",
                "annual_min_mean_discharge_cfs",
                "n_complete_water_years",
            ],
        )
    )
    lines.extend(
        [
            "",
            "Interpretation: longer windows smooth short pulses and describe more persistent low-flow conditions. These are descriptive screens, not fitted 7Q10/14Q10/30Q10 statistics or ecological standards.",
            "",
        ]
    )
    lines.extend(_markdown_table(management, ["exceedance_probability_pct", "annual_runoff_acre_ft", "annual_mean_discharge_cfs", "year_type"]))
    lines.extend(
        [
            "",
            "Interpretation: P50 is the median annual runoff; P75, P90, and P95 are progressively conservative empirical planning baselines. They should be combined with storage, demand, conveyance loss, evaporation, return flow, and regulation information before allocation or operating decisions.",
            "",
            "## 10. Correlation and consistency evidence",
            "",
            "Correlation selection is hypothesis-driven: 1/7/30-day discharge lags measure persistence; same-date stage–discharge pairs are evaluated on linear and log10 scales; field-measurement published-Q error is evaluated versus time. Missing dates are not imputed and field measurements remain a separate ground-truth layer.",
        ]
    )
    lines.extend(_markdown_table(correlations, ["comparison", "n", "pearson_r", "spearman_r", "notes"]))
    lines.extend(
        [
            "",
            f"Consistency evidence contains {len(method_evidence)} summarized field-method/control rows and {len(changes)} statistical change-point candidate rows. Dashed change-point lines are prompts for source-record review; they do not establish a dam, datum, instrument, rating, or operational cause.",
            "",
            "## 11. Output inventory and reuse",
            "",
            "The machine-readable tables remain in this directory: `annual_hydrology.csv`, `monthly_hydrology.csv`, `annual_runoff_exceedance.csv`, `high_flow_events.csv`, `flood_frequency.csv`, `baseflow_daily.parquet`, `baseflow_annual.csv`, `flow_duration_curve.csv`, `flow_duration_quantiles.csv`, `water_management_baselines.csv`, `low_flow_management.csv`, `correlation_summary.csv`, `method_evidence_summary.csv`, and `change_point_candidates.csv`.",
            "",
            "The reusable definitions are also maintained in `docs/HYDROLOGY_INTERPRETATION.md`. This station-specific file is regenerated whenever `station-hydro analyze` runs, so the narrative stays aligned with the current local data version and parameters.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_hydrology_interpretation(
    station_root: Path, result: dict[str, Any]
) -> Path:
    """Write the station-specific interpretation next to derived tables."""

    output = station_root / "hydrology" / "hydrology_interpretation.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_hydrology_interpretation(station_root, result), encoding="utf-8")
    return output
