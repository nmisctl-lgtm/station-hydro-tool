import { Map as MapLibreMap, NavigationControl, ScaleControl, Marker, Popup, LngLatBounds } from "./vendor/maplibre-gl.mjs";

/* Read-only station dashboard. Existing station-tool artifacts are rendered,
   not recalculated, in this browser module. */
const $ = (selector) => document.querySelector(selector);
const state = {
  stationKey: null,
  analysis: null,
  hydrology: null,
  fdc: [],
  fdcRange: null,
  fdcValueRange: null,
  baseflowAnnualRange: null,
  baseflowAnnualValueRange: null,
  dailyPattern: [],
  hydrographData: null,
  hydroZoom: null,
  hydroDischargeRange: null,
  hydroStageRange: null,
  hydroVisibility: { stage: true, discharge: true, baseflow: true },
  regionalMap: null,
  localMap: null,
  watershed: null,
  flowNetwork: null,
  regionalStations: null,
  coloradoBasin: null,
  stateBoundaries: null,
  dams: null,
  stageDischargePoints: [],
  stageDischargeMode: "paired",
  stageDischargeXRange: null,
  stageDischargeYRange: null,
  ffaPeriodRange: null,
  ffaValueRange: null,
  stageDischargeTimeline: null,
  stageDischargeLoadTimer: null,
  coverageFilter: "core",
  annualRunoffX: "water_year",
  annualRunoffY: "total_runoff_acre_ft",
  annualRunoffRange: null,
  annualRunoffValueRange: null,
  monthlyRunoffYear: null,
  waterYearDayRange: null,
  waterYearValueRange: null,
};

const COLORS = {
  ink: "#10241f",
  muted: "#5e716c",
  grid: "#dbe5e1",
  discharge: "#176c88",
  stage: "#c96f2f",
  baseflow: "#7570b3",
  reference: "#52645e",
  band: "#b9ddea",
  observed: "#176c88",
  historic: "#e6ab02",
  palette: ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#D55E00", "#56B4E9", "#000000", "#F0E442"],
  heat: ["#440154", "#414487", "#2a788e", "#22a884", "#7ad151", "#fde725"],
};
const ACRE_FEET_PER_CFS_DAY = 1.9834710743801653;

const ANNUAL_RUNOFF_FIELDS = {
  water_year: { label: "Water Year", unit: "year", integer: true },
  total_runoff_acre_ft: { label: "Annual runoff", unit: "acre-ft" },
  mean_discharge_cfs: { label: "Annual mean discharge", unit: "ft³/s" },
  max_daily_discharge_cfs: { label: "Annual maximum daily flow", unit: "ft³/s" },
  min_daily_discharge_cfs: { label: "Annual minimum daily flow", unit: "ft³/s" },
  valid_days: { label: "Valid days", unit: "days", integer: true },
};

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || "Request failed (" + response.status + ")");
  return payload.data;
}

function text(value) { return value == null || value === "" ? "—" : String(value); }
function safeNumber(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }
function escapeHtml(value) {
  return text(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}
function formatNumber(value, digits = 1) {
  const number = safeNumber(value);
  if (number == null) return "—";
  return number.toLocaleString(undefined, { maximumFractionDigits: digits });
}
function axisDigits(minimum, maximum) {
  const span = Math.abs(Number(maximum) - Number(minimum));
  return span < 1 ? 2 : span < 10 ? 1 : 0;
}
function formatDate(value) { return value ? String(value).slice(0, 10) : "—"; }
function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC").replace(/Z$/, " UTC");
}
function formatUnit(value) { return text(value).replace(/^ft\^?3\/s$/i, "ft³/s"); }
function isoDate(value) { return value ? String(value).slice(0, 10) : ""; }
function frequencyKey(value) { return String(value || "").trim().toLowerCase().replace(/[\s_-]+/g, "_"); }
function frequencyLabel(value) {
  const labels = { unit: "continuous", points: "instantaneous", daily: "daily", annual_peak: "annual peak", annual_peaks: "annual peaks", water_year: "Water Year" };
  return labels[frequencyKey(value)] || text(value);
}
function isWaterYearSeries(series) { return frequencyKey(series?.frequency) === "water_year"; }
function stationParameter() { return new URLSearchParams(window.location.search).get("station"); }
function daysBefore(value, count) {
  const date = new Date(String(value) + "T00:00:00Z");
  date.setUTCDate(date.getUTCDate() - count);
  return date.toISOString().slice(0, 10);
}
function daysAfter(value, count) {
  const date = new Date(String(value) + "T00:00:00Z");
  date.setUTCDate(date.getUTCDate() + count);
  return date.toISOString().slice(0, 10);
}
function prettyName(value) {
  const raw = String(value || "").trim();
  if (!raw || raw !== raw.toUpperCase()) return raw;
  return raw.toLowerCase().replace(/\b\w/g, (match) => match.toUpperCase()).replace(/\b(Nr|At|Of|The|And|On|To|In)\b/g, (match) => match.toLowerCase()).replace(/\bUsgs\b/g, "USGS").replace(/\bCo\b/g, "CO").replace(/\bNm\b/g, "NM").replace(/\bHuc\b/g, "HUC").replace(/\bNavd88\b/g, "NAVD88");
}
function sourceVariable(series, name) {
  const values = [series.source_variable, series.variable_name, series.variable].filter(Boolean).map((value) => String(value).toLowerCase());
  const aliases = name === "stage" ? ["stage", "00065", "gage_ht", "gage height", "gauge height"] : ["discharge", "dischrg", "00060", "d1", "d2", "q1", "q2", "flow"];
  return values.some((value) => aliases.includes(value));
}
function seriesCategory(series) {
  if (sourceVariable(series, "stage")) return "stage";
  if (sourceVariable(series, "discharge")) return "discharge";
  return "other";
}
function variableColor(series) {
  return seriesCategory(series) === "stage" ? COLORS.stage : seriesCategory(series) === "discharge" ? COLORS.discharge : COLORS.baseflow;
}
function setNotice(message) {
  const notice = $("#station-notice");
  if (!notice) return;
  notice.textContent = message || "";
  notice.hidden = !message;
}
function setPill(selector, value, kind = "") {
  const target = $(selector);
  if (!target) return;
  target.textContent = value;
  target.className = "pill" + (kind ? " " + kind : "");
}
function renderHeaderStatus(station) {
  const daily = availabilitySeries("discharge", "daily") || stationSeries("discharge", "daily") || stationSeries("discharge");
  const stage = availabilitySeries("stage", "unit") || stationSeries("stage", "daily") || stationSeries("stage");
  const coverage = (series) => ({ start: series?.declared_start || series?.observed_date_start, end: series?.declared_end || series?.observed_date_end });
  const dischargeCoverage = coverage(daily);
  const stageCoverage = coverage(stage);
  const chips = [
    text(station.activity_status || "status unavailable"),
    dischargeCoverage.start && dischargeCoverage.end ? "daily discharge " + dischargeCoverage.start + " → " + dischargeCoverage.end : "daily discharge coverage unavailable",
    stageCoverage.start && stageCoverage.end ? "gage height daily mean " + stageCoverage.start + " → " + stageCoverage.end : "gage height daily coverage unavailable",
    state.hydrology ? "station hydrology package ready" : "station hydrology package unavailable",
  ];
  $("#station-status-chips").innerHTML = chips.map((chip) => '<span class="status-chip">' + escapeHtml(chip) + "</span>").join("");
}

function downloadCsv(filename, rows) {
  if (!rows?.length) return;
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const quote = (value) => '"' + String(value ?? "").replaceAll('"', '""') + '"';
  const csv = [columns, ...rows.map((row) => columns.map((column) => row[column]))].map((row) => row.map(quote).join(",")).join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function bindNavigation() {
  const links = [...document.querySelectorAll(".station-toc a[href^='#']")];
  links.forEach((link) => link.addEventListener("click", (event) => {
    const target = document.querySelector(link.getAttribute("href"));
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    history.replaceState(null, "", link.getAttribute("href"));
  }));
  const sections = links.map((link) => document.querySelector(link.getAttribute("href"))).filter(Boolean);
  const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
    if (!entry.isIntersecting) return;
    links.forEach((link) => link.classList.toggle("is-active", link.getAttribute("href") === "#" + entry.target.id));
  }), { rootMargin: "-20% 0px -65% 0px", threshold: 0 });
  sections.forEach((section) => observer.observe(section));
}
function stationSeries(category, frequency) {
  return (state.analysis?.series || []).find((series) => seriesCategory(series) === category && (!frequency || frequencyKey(series.frequency) === frequencyKey(frequency)));
}
function availabilitySeries(category, frequency) {
  return (state.hydrology?.availability || []).find((series) => seriesCategory(series) === category && (!frequency || frequencyKey(series.frequency) === frequencyKey(frequency)));
}
function coverageCategory(series) {
  return seriesCategory(series);
}
function isCoreCoverageSeries(series) {
  const category = coverageCategory(series);
  if (isWaterYearSeries(series)) return false;
  if (category === "stage" || category === "discharge") return ["daily", "points", "unit", "continuous", "instantaneous"].includes(frequencyKey(series.frequency || series.data_type));
  return ["annual_peak", "annual_peaks"].includes(frequencyKey(series.frequency || series.data_type));
}
function coverageStatus(series) {
  return String(series.local_status || (series.raw_observation_count ? "downloaded" : "catalog_only")).toLowerCase();
}
function coverageRows() {
  const all = (state.hydrology?.availability?.length ? state.hydrology.availability : state.analysis.series || [])
    .filter((series) => !isWaterYearSeries(series));
  const visible = state.coverageFilter === "core" ? all.filter(isCoreCoverageSeries) : state.coverageFilter === "local" ? all.filter((series) => coverageStatus(series) !== "catalog_only") : all;
  const priority = (series) => {
    const category = coverageCategory(series);
    const frequency = frequencyKey(series.frequency || series.data_type);
    if (category === "discharge" && frequency === "daily") return 0;
    if (category === "stage" && frequency === "points") return 1;
    if (category === "discharge" && frequency === "points") return 2;
    if (["annual_peak", "annual_peaks"].includes(frequency)) return 3;
    return coverageStatus(series) === "catalog_only" ? 5 : 4;
  };
  return { all, visible: visible.slice().sort((left, right) => priority(left) - priority(right) || String(left.variable || left.variable_name || "").localeCompare(String(right.variable || right.variable_name || ""))) };
}
function svgFrame(title, subtitle, body, width = 1120, height = 420) {
  return '<svg class="station-svg" viewBox="0 0 ' + width + " " + height + '" role="img" aria-label="' + escapeHtml(title) + '"><title>' + escapeHtml(title) + "</title><desc>" + escapeHtml(subtitle) + "</desc>" + body + "</svg>";
}
function niceTicks(minimum, maximum, count = 5) {
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return [];
  if (minimum === maximum) return [minimum];
  const rough = Math.abs(maximum - minimum) / Math.max(1, count);
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const normalized = rough / power;
  const factor = normalized >= 5 ? 5 : normalized >= 2 ? 2 : 1;
  const step = factor * power;
  const start = Math.ceil(minimum / step) * step;
  const ticks = [];
  for (let value = start; value <= maximum + step * 0.001 && ticks.length < 12; value += step) ticks.push(Number(value.toPrecision(12)));
  return ticks.length ? ticks : [minimum, maximum];
}
function scaleFor(values, top, bottom, logarithmic = false) {
  let minimum = Infinity;
  let maximum = -Infinity;
  for (const value of values) {
    if (!Number.isFinite(value) || (logarithmic && value <= 0)) continue;
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;
  if (logarithmic) {
    minimum = Math.max(Number.MIN_VALUE, minimum);
    const low = Math.log10(minimum);
    const high = Math.log10(maximum);
    return { minimum, maximum, logarithmic, scale: (value) => bottom - (Math.log10(value) - low) / (high - low || 1) * (bottom - top), ticks: niceLogTicks(minimum, maximum) };
  }
  const padding = (maximum - minimum) * 0.06 || 1;
  minimum -= padding;
  maximum += padding;
  return { minimum, maximum, logarithmic, scale: (value) => bottom - (value - minimum) / (maximum - minimum || 1) * (bottom - top), ticks: niceTicks(minimum, maximum) };
}
function niceLogTicks(minimum, maximum) {
  const low = Math.floor(Math.log10(minimum));
  const high = Math.ceil(Math.log10(maximum));
  const values = [];
  for (let exponent = low; exponent <= high; exponent += 1) {
    for (const multiplier of [1, 2, 5]) {
      const value = multiplier * Math.pow(10, exponent);
      if (value >= minimum && value <= maximum) values.push(value);
    }
  }
  return values.length ? values : [minimum, maximum];
}
function linePath(points, xScale, yScale) {
  const usable = points.filter((point) => point.time != null && point.value != null).sort((left, right) => left.time - right.time);
  if (!usable.length) return "";
  const gaps = usable.slice(1).map((point, index) => point.time - usable[index].time).filter((gap) => gap > 0).sort((left, right) => left - right);
  const median = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 0;
  const breakGap = Math.max(7 * 86400000, median * 4);
  const paths = [[]];
  for (const point of usable) {
    const current = paths[paths.length - 1];
    const previous = current[current.length - 1];
    if (previous && point.time - previous.time > breakGap) paths.push([]);
    paths[paths.length - 1].push(point);
  }
  return paths.filter((segment) => segment.length).map((segment) => segment.map((point, index) => (index ? "L" : "M") + xScale(point.time).toFixed(1) + "," + yScale(point.value).toFixed(1)).join(" ")).join(" ");
}
function dateLabel(timestamp) {
  const date = new Date(timestamp);
  return date.getUTCFullYear() + "-" + String(date.getUTCMonth() + 1).padStart(2, "0");
}
function nearestPoint(points, timestamp) {
  let best = null;
  for (const point of points) {
    if (point.time == null || point.value == null) continue;
    if (!best || Math.abs(point.time - timestamp) < Math.abs(best.time - timestamp)) best = point;
  }
  return best;
}
function dailyPoints(response) {
  return (response?.points || []).map((point) => ({
    time: Date.parse(String(point.observed_date).slice(0, 10) + "T12:00:00Z"),
    value: safeNumber(point.value),
    date: String(point.observed_date || "").slice(0, 10),
    raw: point,
  })).filter((point) => Number.isFinite(point.time) && point.value != null);
}
function addChartInteraction(target, svg, options) {
  const hit = svg.querySelector(".plot-hit");
  if (!hit) return;
  let dragStart = null;
  let suppressClick = false;
  const box = () => svg.getBoundingClientRect();
  const xPosition = (event) => {
    const rect = box();
    return (event.clientX - rect.left) * options.width / rect.width;
  };
  const yPosition = (event) => {
    const rect = box();
    return (event.clientY - rect.top) * options.height / rect.height;
  };
  const valueAt = (scale, pixel) => {
    if (!scale) return null;
    const fraction = (options.bottom - Math.max(options.top, Math.min(options.bottom, pixel))) / (options.bottom - options.top || 1);
    if (scale.logarithmic) return Math.pow(10, Math.log10(scale.minimum) + fraction * (Math.log10(scale.maximum) - Math.log10(scale.minimum)));
    return scale.minimum + fraction * (scale.maximum - scale.minimum);
  };
  const timeAt = (x) => options.timeStart + (Math.max(options.left, Math.min(options.right, x)) - options.left) / (options.right - options.left || 1) * (options.timeEnd - options.timeStart || 1);
  const removeTip = () => target.querySelector(".chart-tooltip")?.remove();
  const showCrosshair = (event) => {
    const crosshair = svg.querySelector("#hydro-crosshair");
    if (!crosshair) return;
    const x = Math.max(options.left, Math.min(options.right, xPosition(event)));
    crosshair.setAttribute("x1", x); crosshair.setAttribute("x2", x); crosshair.setAttribute("display", "block");
  };
  const showTip = (event) => {
    const timestamp = timeAt(xPosition(event));
    const values = options.series.map((series) => ({ label: series.label, unit: series.unit, point: nearestPoint(series.points, timestamp) })).filter((item) => item.point);
    if (!values.length) return;
    removeTip();
    const rect = target.getBoundingClientRect();
    const chartRect = svg.getBoundingClientRect();
    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.innerHTML = "<strong>" + escapeHtml(dateLabel(timestamp)) + "</strong>" + values.map((item) => "<span>" + escapeHtml(item.label) + ": " + escapeHtml(formatNumber(item.point.value, 3)) + " " + escapeHtml(item.unit || "") + "</span>").join("");
    tooltip.style.left = Math.max(8, Math.min(Math.max(8, rect.width - 340), event.clientX - chartRect.left - 120)) + "px";
    tooltip.style.top = Math.max(8, event.clientY - rect.top - 118) + "px";
    target.append(tooltip);
  };
  hit.addEventListener("mousemove", (event) => { if (dragStart == null) { showCrosshair(event); showTip(event); } });
  hit.addEventListener("mouseleave", () => { removeTip(); svg.querySelector("#hydro-crosshair")?.setAttribute("display", "none"); });
  hit.addEventListener("pointerdown", (event) => { dragStart = { x: xPosition(event), y: yPosition(event) }; hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    const preview = svg.querySelector("#hydro-brush-preview");
    if (!preview) return;
    const end = { x: xPosition(event), y: yPosition(event) };
    const x1 = Math.max(options.left, Math.min(options.right, dragStart.x)), x2 = Math.max(options.left, Math.min(options.right, end.x));
    const y1 = Math.max(options.top, Math.min(options.bottom, dragStart.y)), y2 = Math.max(options.top, Math.min(options.bottom, end.y));
    preview.setAttribute("x", Math.min(x1, x2)); preview.setAttribute("y", Math.min(y1, y2)); preview.setAttribute("width", Math.abs(x2 - x1)); preview.setAttribute("height", Math.abs(y2 - y1)); preview.setAttribute("display", "block");
  });
  hit.addEventListener("pointerup", (event) => {
    if (dragStart == null) return;
    const dragEnd = { x: xPosition(event), y: yPosition(event) };
    const horizontalDistance = Math.abs(dragEnd.x - dragStart.x), verticalDistance = Math.abs(dragEnd.y - dragStart.y);
    if (horizontalDistance > 12 || verticalDistance > 12) {
      const startTime = timeAt(Math.min(dragStart.x, dragEnd.x));
      const endTime = timeAt(Math.max(dragStart.x, dragEnd.x));
      if (horizontalDistance > 12) { state.hydroZoom = [startTime, endTime]; options.onRangeSelected?.(startTime, endTime); }
      if (verticalDistance > 12) options.onValueRangeSelected?.(dragStart.y, dragEnd.y, valueAt(options.dischargeScale, dragStart.y), valueAt(options.dischargeScale, dragEnd.y), valueAt(options.stageScale, dragStart.y), valueAt(options.stageScale, dragEnd.y));
      suppressClick = true;
      svg.querySelector("#hydro-brush-preview")?.setAttribute("display", "none");
      renderHydrograph();
    }
    dragStart = null;
  });
  hit.addEventListener("click", () => { if (suppressClick) suppressClick = false; });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.hydroZoom = null; state.hydroDischargeRange = null; state.hydroStageRange = null; renderHydrograph(); });
}

function renderMetadata(station) {
  const fields = [
    ["Location key", station.location_key],
    ["Provider station ID", station.provider_station_id],
    ["Source", station.source_name],
    ["Station name", prettyName(station.display_name)],
    ["Station type", station.station_type],
    ["Water source", station.water_source],
    ["State", station.state_name],
    ["HUC", station.huc],
    ["Site location", station.latitude != null && station.longitude != null ? Number(station.latitude).toFixed(5) + ", " + Number(station.longitude).toFixed(5) + " (" + text(station.coordinate_status) + ")" : station.coordinate_status],
    ["Station kind", station.station_kind],
    ["Activity status", station.activity_status],
    ["Timezone", station.timezone || state.hydrology?.station_metadata?.timezone],
    ["Latest observed", formatTimestamp(station.latest_observed_at)],
    ["Latest retrieved", formatTimestamp(station.latest_retrieved_at)],
    ["Local snapshot", station.local_snapshot_id],
  ];
  $("#station-metadata").innerHTML = fields.map((field) => "<div><dt>" + escapeHtml(field[0]) + "</dt><dd>" + escapeHtml(field[1]) + "</dd></div>").join("");
  const facts = [
    ["Regional basin", "Colorado River Basin"],
    ["Sub-basin", "San Juan River Basin"],
    ["Drainage area", state.hydrology?.station_metadata?.drainage_area_sq_mi ? formatNumber(state.hydrology.station_metadata.drainage_area_sq_mi, 1) + " mi²" : station.drainage_area_sq_mi ? formatNumber(station.drainage_area_sq_mi, 1) + " mi²" : "—"],
    ["Elevation", state.hydrology?.station_metadata?.elevation_ft ? formatNumber(state.hydrology.station_metadata.elevation_ft, 1) + " ft" : "—"],
    ["Coordinates", station.latitude != null && station.longitude != null ? Number(station.latitude).toFixed(5) + ", " + Number(station.longitude).toFixed(5) : "—"],
  ];
  $("#location-facts").innerHTML = facts.map((fact) => "<div class=\"fact-row\"><span>" + escapeHtml(fact[0]) + "</span><strong>" + escapeHtml(fact[1]) + "</strong></div>").join("");
}
function renderExternalLinks(station) {
  const id = station.provider_station_id || state.stationKey.split(":").pop();
  const links = [
    ["USGS monitoring location", "https://waterdata.usgs.gov/monitoring-location/USGS-" + encodeURIComponent(id) + "/"],
    ["USGS current conditions", "https://waterdata.usgs.gov/nwis/uv?site_no=" + encodeURIComponent(id)],
    ["NWIS inventory", "https://waterdata.usgs.gov/nwis/inventory/?site_no=" + encodeURIComponent(id)],
    ["StreamStats plots", "https://streamstats.usgs.gov/ss/?gage=" + encodeURIComponent(id) + "&tab=plots"],
  ];
  $("#station-external-links").innerHTML = links.slice(0, 2).map((link) => '<a href="' + link[1] + '" target="_blank" rel="noopener">' + escapeHtml(link[0]) + "</a>").join("");
  $("#context-links").innerHTML = links.slice(2).map((link) => '<a href="' + link[1] + '" target="_blank" rel="noopener">' + escapeHtml(link[0]) + " ↗</a>").join("");
}
function renderQuickSummary() {
  const station = state.analysis.station;
  const summary = state.hydrology?.summary || {};
  const daily = state.analysis.series.find((series) => seriesCategory(series) === "discharge" && String(series.frequency).toLowerCase() === "daily") || stationSeries("discharge");
  const annual = state.hydrology?.annual_hydrology || [];
  const peak = annual.reduce((best, row) => safeNumber(row.max_daily_discharge_cfs) != null && (!best || safeNumber(row.max_daily_discharge_cfs) > safeNumber(best.max_daily_discharge_cfs)) ? row : best, null);
  const low = annual.reduce((best, row) => safeNumber(row.min_daily_discharge_cfs) != null && (!best || safeNumber(row.min_daily_discharge_cfs) < safeNumber(best.min_daily_discharge_cfs)) ? row : best, null);
  const raw = safeNumber(daily?.raw_observation_count);
  const numeric = safeNumber(daily?.numeric_observation_count);
  const completeness = raw ? (numeric / raw * 100).toFixed(1) + "%" : "—";
  const periodStart = daily?.observed_date_start || "";
  const periodEnd = daily?.observed_date_end || "";
  const metrics = [
    ["Period of record", periodStart && periodEnd ? periodStart.slice(0, 4) + "–" + periodEnd.slice(0, 4) : "—", periodStart && periodEnd ? "Daily discharge · full dates in Data inventory" : "Daily discharge"],
    ["Daily completeness", completeness, numeric != null ? formatNumber(numeric, 0) + " numeric records" : "No count"],
    ["Mean discharge", formatNumber(summary.interannual_mean_discharge_cfs, 1) + " ft³/s", "Complete Water Years"],
    ["Median discharge", formatNumber((state.hydrology?.flow_duration_quantiles || []).find((row) => Number(row.exceedance_probability_pct) === 50)?.discharge_cfs, 1) + " ft³/s", "FDC Q50"],
    ["Minimum daily flow", formatNumber(low?.min_daily_discharge_cfs, 1) + " ft³/s", low ? "WY " + text(low.water_year) + " annual minimum" : "Annual summary"],
    ["Maximum daily flow", formatNumber(peak?.max_daily_discharge_cfs, 0) + " ft³/s", peak?.date_of_max_daily_discharge || "Annual summary"],
    ["Latest observation", formatDate(station.latest_observed_at || daily?.observed_date_end), station.activity_status || "Status unavailable"],
  ];
  $("#quick-summary").innerHTML = metrics.map((metric) => "<div class=\"summary-metric\"><span>" + escapeHtml(metric[0]) + "</span><strong>" + escapeHtml(metric[1]) + "</strong><small>" + escapeHtml(metric[2]) + "</small></div>").join("");
  setPill("#summary-status", "Station loaded");
}
function renderHydrologicIntro() {
  const station = state.analysis?.station || {};
  const metadata = state.hydrology?.station_metadata || {};
  const summary = state.hydrology?.summary || {};
  const quality = state.hydrology?.quality_summary || [];
  const series = state.analysis?.series || [];
  const daily = quality.find((item) => frequencyKey(item.frequency) === "daily")
    || series.find((item) => seriesCategory(item) === "discharge" && frequencyKey(item.frequency) === "daily")
    || {};
  const annualPeak = quality.find((item) => ["annual_peak", "annual_peaks"].includes(frequencyKey(item.frequency))) || {};
  const stationName = prettyName(metadata.name || station.display_name || state.stationKey);
  const drainageArea = safeNumber(metadata.drainage_area_sq_mi || station.drainage_area_sq_mi);
  const dailyStart = daily.declared_start || daily.observed_date_start;
  const dailyEnd = daily.declared_end || daily.observed_date_end;
  const dailyCount = safeNumber(daily.valid_value_count ?? daily.numeric_observation_count);
  const dailyExpected = safeNumber(daily.completeness_denominator ?? daily.raw_observation_count);
  const dailyCompleteness = safeNumber(daily.completeness) ?? (dailyExpected ? dailyCount / dailyExpected : null);
  const waterYearCount = safeNumber(summary.water_year_count);
  const completeWaterYearCount = safeNumber(summary.complete_water_year_count);
  const annualHydrology = state.hydrology?.annual_hydrology || [];
  const derivedMaximaCount = annualHydrology.filter((row) => String(row.is_complete).toLowerCase() === "true" && safeNumber(row.max_daily_discharge_cfs) != null).length;
  const fallbackPeakSeries = summary.flood_frequency_source === "daily_annual_maxima_fallback";
  const peakCount = safeNumber(annualPeak.valid_value_count) ?? (fallbackPeakSeries ? derivedMaximaCount : safeNumber(summary.flood_frequency_systematic_years));
  const meanDischarge = safeNumber(summary.interannual_mean_discharge_cfs);
  const interannualCv = safeNumber(summary.interannual_cv);
  const record = dailyStart && dailyEnd ? "The retained daily discharge record spans " + dailyStart + " through " + dailyEnd + "." : "The retained daily discharge record has no complete date range in the local package.";
  const setting = drainageArea != null ? "The station drains approximately " + formatNumber(drainageArea, 0) + " mi²." : "The station drainage area is not available in the local metadata.";
  const regime = meanDischarge != null ? "Across the available complete Water Years, mean daily discharge is " + formatNumber(meanDischarge, 1) + " ft³/s" + (interannualCv != null ? " and the interannual coefficient of variation is " + formatNumber(interannualCv, 2) + "." : ".") : "The local package does not contain enough annual information to summarize the long-term discharge regime.";
  const coverage = dailyCompleteness != null ? "Daily numeric completeness is " + formatNumber(dailyCompleteness * 100, 1) + "%" + (dailyCount != null && dailyExpected != null ? " (" + formatNumber(dailyCount, 0) + "/" + formatNumber(dailyExpected, 0) + " expected records)." : ".") : "Daily numeric completeness is not available.";
  const annual = waterYearCount != null && completeWaterYearCount != null ? completeWaterYearCount + " of " + waterYearCount + " Water Years meet the package's completeness rule." : "The number of complete Water Years is not available.";
  const limitations = [];
  if (dailyCompleteness != null && dailyCompleteness < 0.9) limitations.push("the daily record is incomplete");
  if (waterYearCount != null && completeWaterYearCount != null && (completeWaterYearCount < 10 || completeWaterYearCount / Math.max(1, waterYearCount) < 0.8)) limitations.push("few complete Water Years are available");
  if (fallbackPeakSeries) limitations.push("no provider annual-peak series is available; daily maxima are used as a derived fallback");
  if (peakCount != null && peakCount < 20) limitations.push("the annual-peak sample is small (n=" + formatNumber(peakCount, 0) + ")");
  const evidenceLabel = limitations.length >= 2 ? "Limited evidence" : limitations.length ? "Use with caution" : "Descriptive evidence";
  const boundary = limitations.length ? "Data-confidence note: " + limitations.join("; ") + ". Therefore annual variability, dry/normal/wet classes, flood-frequency estimates, and other derived statistics should be treated as screening-level evidence rather than definitive long-term or design values." : "Data-confidence note: the retained record supports descriptive station analysis. Derived statistics still describe the archived record and should not be treated as regulatory or design values without method-specific review.";
  const peakStatement = fallbackPeakSeries
    ? "No provider annual-peak series is available; flood screening uses " + formatNumber(peakCount || 0, 0) + " complete-Water-Year daily maxima as a derived proxy."
    : "Annual-peak analysis currently uses " + (peakCount != null ? formatNumber(peakCount, 0) + " recorded annual peak value" + (peakCount === 1 ? "" : "s") + "." : "the available annual-peak records.");
  $("#hydrologic-intro").textContent = stationName + " is a USGS stream gage in the San Juan Basin. " + setting + " " + record + " " + coverage + " " + annual + " " + regime + " " + peakStatement;
  $("#hydrologic-confidence-note").textContent = boundary;
  setPill("#hydrologic-confidence", evidenceLabel, limitations.length ? "warn" : "");
}
function renderCoverage() {
  const { all, visible: rows } = coverageRows();
  $("#coverage-rows").innerHTML = rows.map((series) => {
    const variable = series.variable || series.variable_name || series.source_variable;
    const frequency = frequencyLabel(series.frequency || series.data_type || "—");
    const providerStart = series.declared_start || series.observed_date_start;
    const providerEnd = series.declared_end || series.observed_date_end;
    const localStatus = series.local_status || (series.raw_observation_count ? "downloaded" : "catalog_only");
    const localCount = series.local_record_count || series.numeric_observation_count || "";
    return "<tr><td><strong>" + escapeHtml(variable) + "</strong><span class=\"station-key\">" + escapeHtml(series.parameter_code || series.provider_data_type || "") + "</span></td><td>" + escapeHtml(frequency) + "<br><span class=\"station-key\">" + escapeHtml(formatUnit(series.unit || series.unit_canonical || "")) + "</span></td><td>" + escapeHtml(providerStart ? providerStart + " → " + providerEnd : "—") + "</td><td><span class=\"status-text status-" + escapeHtml(localStatus) + "\">" + escapeHtml(localStatus) + "</span></td><td>" + escapeHtml(localCount ? formatNumber(localCount, 0) : "—") + "</td></tr>";
  }).join("");
  const filterLabels = { core: "core analytical series", local: "downloaded or locally available series", all: "all provider inventory rows" };
  $("#coverage-filter-note").textContent = "Showing " + rows.length.toLocaleString() + " of " + all.length.toLocaleString() + " rows: " + filterLabels[state.coverageFilter] + ". Provider inventory and local archive counts are separate from the chart-ready release series. Water-Year catalog series remain excluded from analysis views.";
  const spans = rows.map((series) => ({ ...series, timelineStart: series.declared_start || series.observed_date_start, timelineEnd: series.declared_end || series.observed_date_end })).filter((series) => series.timelineStart && series.timelineEnd);
  const timeline = $("#coverage-timeline");
  if (!spans.length) { timeline.innerHTML = '<p class="chart-empty">No dated coverage is available for the selected inventory view.</p>'; setPill("#data-status", rows.length.toLocaleString() + " shown / " + all.length.toLocaleString() + " total"); return; }
  const first = Math.min(...spans.map((series) => Date.parse(series.timelineStart + "T00:00:00Z")));
  const last = Math.max(...spans.map((series) => Date.parse(series.timelineEnd + "T00:00:00Z")));
  const total = Math.max(1, last - first);
  const ticks = [0, .25, .5, .75, 1].map((fraction) => {
    const timestamp = first + (last - first) * fraction;
    return '<span style="left:' + (fraction * 100).toFixed(2) + '%" class="timeline-tick">' + escapeHtml(formatDate(new Date(timestamp).toISOString())) + "</span>";
  }).join("");
  timeline.innerHTML = '<div class="timeline-heading"><strong>Coverage timeline · shared axis</strong><span>' + escapeHtml(formatDate(new Date(first).toISOString())) + " → " + escapeHtml(formatDate(new Date(last).toISOString())) + '</span></div>' + spans.map((series) => {
    const start = Date.parse(series.timelineStart + "T00:00:00Z");
    const end = Date.parse(series.timelineEnd + "T00:00:00Z");
    const left = Math.max(0, (start - first) / total * 100);
    const width = Math.max(1.5, (end - start) / total * 100);
    const variable = series.variable || series.variable_name || series.source_variable || "Provider series";
    const frequency = frequencyLabel(series.frequency || series.data_type || "");
    return '<div class="coverage-track"><span><strong>' + escapeHtml(variable) + '</strong><small>' + escapeHtml(frequency + (series.unit || series.unit_canonical ? " · " + formatUnit(series.unit || series.unit_canonical) : "")) + '</small></span><div><i style="--track-color:' + variableColor(series) + ";--track-left:" + left.toFixed(2) + "%;--track-width:" + width.toFixed(2) + '%"></i></div><small>' + escapeHtml(series.timelineStart + " → " + series.timelineEnd) + "</small></div>";
  }).join("") + '<div class="timeline-axis-bottom">' + ticks + "</div>";
  setPill("#data-status", rows.length.toLocaleString() + " shown / " + all.length.toLocaleString() + " total");
}
function populateSeries() {
  const series = state.analysis.series;
  const analyticalSeries = series.filter((seriesItem) => !isWaterYearSeries(seriesItem));
  for (const item of [["#stage-series", "stage", "No stage series"], ["#discharge-series", "discharge", "No discharge series"]]) {
    const choices = analyticalSeries.filter((seriesItem) => seriesCategory(seriesItem) === item[1]);
    const select = $(item[0]);
    select.replaceChildren(new Option(item[2], ""), ...choices.map((seriesItem) => new Option((seriesItem.variable_name || seriesItem.source_variable) + " · " + frequencyLabel(seriesItem.frequency) + " · " + (seriesItem.unit_canonical || ""), seriesItem.series_key)));
    select.disabled = !choices.length;
    if (choices.length) select.value = choices.find((choice) => String(choice.frequency).toLowerCase() === (item[1] === "discharge" ? "daily" : "unit"))?.series_key || choices[0].series_key;
  }
  const daily = analyticalSeries.find((seriesItem) => seriesCategory(seriesItem) === "discharge" && String(seriesItem.frequency).toLowerCase() === "daily") || analyticalSeries.find((seriesItem) => seriesCategory(seriesItem) === "discharge");
  const selectedStageKey = $("#stage-series").value;
  const stage = analyticalSeries.find((seriesItem) => seriesItem.series_key === selectedStageKey);
  const dischargePoints = analyticalSeries.find((seriesItem) => seriesCategory(seriesItem) === "discharge" && frequencyKey(seriesItem.frequency) === "points") || daily;
  const dailyAvailability = availabilitySeries("discharge", "daily");
  const end = dailyAvailability?.declared_end || daily?.observed_date_end || stage?.observed_date_end || new Date().toISOString().slice(0, 10);
  const start = daily?.observed_date_start || daysBefore(end, 3650);
  $("#hydrograph-end").value = end;
  $("#hydrograph-start").value = daysBefore(end, 3650);
  const stageStart = stage?.observed_date_start && dischargePoints?.observed_date_start ? (stage.observed_date_start > dischargePoints.observed_date_start ? stage.observed_date_start : dischargePoints.observed_date_start) : start;
  const stageEnd = stage?.observed_date_end && dischargePoints?.observed_date_end ? (stage.observed_date_end < dischargePoints.observed_date_end ? stage.observed_date_end : dischargePoints.observed_date_end) : end;
  $("#stage-discharge-start").value = stageStart;
  $("#stage-discharge-end").value = stageEnd;
  configureStageDischargeRange(stage?.observed_date_start || stageStart, dischargePoints?.observed_date_end || stageEnd, stageStart, stageEnd);
  renderHydrographToggles();
}
function dateToDay(value) {
  return Math.round(Date.parse(String(value) + "T00:00:00Z") / 86400000);
}
function dayToDate(day) {
  return new Date(Number(day) * 86400000).toISOString().slice(0, 10);
}
function configureStageDischargeRange(minDate, maxDate, startDate, endDate) {
  const minDay = dateToDay(minDate), maxDay = dateToDay(maxDate);
  if (!Number.isFinite(minDay) || !Number.isFinite(maxDay) || maxDay <= minDay) return;
  state.stageDischargeTimeline = { minDate, maxDate, minDay, maxDay };
  const start = $("#stage-discharge-start-range"), end = $("#stage-discharge-end-range");
  [start, end].forEach((input) => { input.min = "0"; input.max = String(maxDay - minDay); });
  start.value = String(Math.max(0, Math.min(maxDay - minDay, dateToDay(startDate) - minDay)));
  end.value = String(Math.max(0, Math.min(maxDay - minDay, dateToDay(endDate) - minDay)));
  syncStageDischargeRange();
}
function syncStageDischargeRange(changed) {
  const timeline = state.stageDischargeTimeline;
  if (!timeline) return;
  const start = $("#stage-discharge-start-range"), end = $("#stage-discharge-end-range");
  let startOffset = Number(start.value), endOffset = Number(end.value);
  if (startOffset >= endOffset) {
    if (changed === "start") endOffset = Math.min(Number(end.max), startOffset + 1);
    else startOffset = Math.max(0, endOffset - 1);
    start.value = String(startOffset); end.value = String(endOffset);
  }
  const startDate = dayToDate(timeline.minDay + startOffset), endDate = dayToDate(timeline.minDay + endOffset);
  $("#stage-discharge-start").value = startDate;
  $("#stage-discharge-end").value = endDate;
  $("#stage-discharge-start-label").textContent = startDate;
  $("#stage-discharge-end-label").textContent = endDate;
  $("#stage-discharge-min-label").textContent = timeline.minDate;
  $("#stage-discharge-max-label").textContent = timeline.maxDate;
}
function scheduleStageDischargeLoad() {
  window.clearTimeout(state.stageDischargeLoadTimer);
  state.stageDischargeLoadTimer = window.setTimeout(loadStageDischarge, 220);
}
function renderHydrographToggles() {
  const items = [
    ["stage", "Gage height", COLORS.stage],
    ["discharge", "Discharge", COLORS.discharge],
  ];
  if (state.hydrographData?.baseflow?.points?.length) items.push(["baseflow", "Baseflow", COLORS.baseflow]);
  $("#hydrograph-variable-toggles").innerHTML = items.map((item) => '<label class="variable-toggle"><input type="checkbox" data-hydro-variable="' + item[0] + '" ' + (state.hydroVisibility[item[0]] ? "checked" : "") + '><span style="color:' + item[2] + '">' + item[1] + "</span></label>").join("");
}
function renderHydrograph() {
  const data = state.hydrographData;
  const target = $("#hydrograph");
  if (!data) { target.innerHTML = '<p class="chart-empty">Select series and dates to load the hydrograph.</p>'; return; }
  const stage = state.hydroVisibility.stage ? data.stage : { points: [], unit: data.stage.unit };
  const discharge = state.hydroVisibility.discharge ? data.discharge : { points: [], unit: data.discharge.unit };
  const baseflow = state.hydroVisibility.baseflow && data.baseflow ? data.baseflow : { points: [], unit: "ft³/s" };
  const stageValues = stage.points.map((point) => point.value).filter((value) => value > 0);
  const dischargeValues = [...discharge.points, ...baseflow.points].map((point) => point.value).filter((value) => value > 0);
  const allTimes = [...stage.points, ...discharge.points, ...baseflow.points].map((point) => point.time).filter(Number.isFinite);
  if (!allTimes.length) { target.innerHTML = '<p class="chart-empty">No numeric values are available for this interval.</p>'; return; }
  const width = 1120, height = 430, left = 92, right = 1035, topA = 46, bottomA = 330;
  const fullStart = Math.min(...allTimes), fullEnd = Math.max(...allTimes);
  const requested = state.hydroZoom && state.hydroZoom[1] > state.hydroZoom[0] ? state.hydroZoom : [fullStart, fullEnd];
  const timeStart = Math.max(fullStart, requested[0]), timeEnd = Math.min(fullEnd, requested[1]);
  const visible = (points) => points.filter((point) => point.time >= timeStart && point.time <= timeEnd);
  const shownStage = visible(stage.points), shownDischarge = visible(discharge.points), shownBaseflow = visible(baseflow.points);
  const sx = (value) => left + (value - timeStart) / (timeEnd - timeStart || 1) * (right - left);
  const logarithmic = $("#log-scale").checked;
  const stageScale = shownStage.length ? scaleFor(state.hydroStageRange || shownStage.map((point) => point.value), topA, bottomA, logarithmic) : null;
  const dischargeScale = dischargeValues.length ? scaleFor(state.hydroDischargeRange || shownDischarge.map((point) => point.value).concat(shownBaseflow.map((point) => point.value)), topA, bottomA, logarithmic) : null;
  const axis = (scale, label, color, side) => {
    if (!scale) return "";
    const axisX = side === "right" ? right : left;
    const labelX = side === "right" ? right + 62 : left - 62;
    const tickX = side === "right" ? right + 9 : left - 9;
    const anchor = side === "right" ? "start" : "end";
    const transform = side === "right" ? "rotate(90 " + labelX + " " + ((topA + bottomA) / 2) + ")" : "rotate(-90 " + labelX + " " + ((topA + bottomA) / 2) + ")";
    return '<text x="' + labelX + '" y="' + ((topA + bottomA) / 2) + '" transform="' + transform + '" text-anchor="middle" class="axis-label" style="fill:' + color + '">' + escapeHtml(label) + '</text>' + scale.ticks.map((value) => '<line x1="' + (side === "right" ? left : left) + '" y1="' + scale.scale(value) + '" x2="' + (side === "right" ? right : right) + '" y2="' + scale.scale(value) + '" class="' + (side === "right" ? "stage-grid" : "chart-grid") + '"/><text x="' + tickX + '" y="' + (scale.scale(value) + 4) + '" text-anchor="' + anchor + '" class="axis-label">' + escapeHtml(formatNumber(value, value < 10 ? 2 : 0)) + "</text>").join("");
  };
  const xTicks = [0, .2, .4, .6, .8, 1].map((fraction) => {
    const time = timeStart + (timeEnd - timeStart) * fraction;
    return '<line x1="' + sx(time) + '" y1="' + topA + '" x2="' + sx(time) + '" y2="' + bottomA + '" class="chart-grid"/><text x="' + sx(time) + '" y="' + (bottomA + 28) + '" text-anchor="' + (fraction === 0 ? "start" : fraction === 1 ? "end" : "middle") + '" class="axis-label">' + escapeHtml(dateLabel(time)) + "</text>";
  }).join("");
  const average = (points, widthDays) => {
    if (!widthDays) return [];
    const sorted = points.filter((point) => point.value != null).sort((a, b) => a.time - b.time);
    const windowMilliseconds = widthDays * 86400000;
    let startIndex = 0;
    let windowSum = 0;
    return sorted.map((point, index) => {
      windowSum += point.value;
      while (startIndex < index && sorted[startIndex].time < point.time - windowMilliseconds) {
        windowSum -= sorted[startIndex].value;
        startIndex += 1;
      }
      return { time: point.time, value: windowSum / (index - startIndex + 1) };
    });
  };
  const moving = Number($("#moving-average").value);
  const stageTitle = "Gage height · daily mean (" + (data.stage.unit || "ft") + ")";
  const dischargeTitle = "Discharge · daily mean (" + (data.discharge.unit || "ft³/s") + ")";
  const baseflowLine = dischargeScale && shownBaseflow.length ? '<path d="' + linePath(shownBaseflow, sx, dischargeScale.scale) + '" class="baseflow-line"/>' : "";
  const baseflowAverage = dischargeScale && moving && shownBaseflow.length ? '<path d="' + linePath(average(shownBaseflow, moving), sx, dischargeScale.scale) + '" class="baseflow-average"/>' : "";
  const baseflowTitle = shownBaseflow.length ? '<text x="' + (left + 210) + '" y="' + (topA - 17) + '" class="panel-title" style="fill:' + COLORS.baseflow + '">Baseflow · retained filter output</text>' : "";
  const hydroRangeText = state.hydroDischargeRange || state.hydroStageRange ? " Selected value ranges are active;" : "";
  const body = '<rect x="' + left + '" y="' + topA + '" width="' + (right - left) + '" height="' + (bottomA - topA) + '" class="chart-plot"/>' + xTicks + axis(dischargeScale, "Discharge / baseflow (" + (data.discharge.unit || "ft³/s") + ")", COLORS.discharge, "left") + axis(stageScale, "Gage height (" + (data.stage.unit || "ft") + ")", COLORS.stage, "right") + (stageScale ? '<path d="' + linePath(shownStage, sx, stageScale.scale) + '" class="stage-line"/>' : "") + (dischargeScale ? '<path d="' + linePath(shownDischarge, sx, dischargeScale.scale) + '" class="discharge-line"/>' : "") + baseflowLine + (stageScale && moving ? '<path d="' + linePath(average(shownStage, moving), sx, stageScale.scale) + '" class="stage-average"/>' : "") + (dischargeScale && moving ? '<path d="' + linePath(average(shownDischarge, moving), sx, dischargeScale.scale) + '" class="discharge-average"/>' : "") + baseflowAverage + '<text x="' + left + '" y="' + (topA - 17) + '" class="panel-title">' + escapeHtml(dischargeTitle) + '</text>' + baseflowTitle + '<text x="' + right + '" y="' + (topA - 17) + '" text-anchor="end" class="panel-title" style="fill:' + COLORS.stage + '">' + escapeHtml(stageTitle) + '</text><line id="hydro-crosshair" x1="' + left + '" y1="' + topA + '" x2="' + left + '" y2="' + bottomA + '" class="crosshair-line" display="none"/><rect id="hydro-brush-preview" x="' + left + '" y="' + topA + '" width="0" height="0" class="annual-brush-preview" display="none"/><rect x="' + left + '" y="' + topA + '" width="' + (right - left) + '" height="' + (bottomA - topA) + '" class="plot-hit"/>';
  const subtitle = "Selected date range. Discharge and baseflow use the left axis; gage height uses the right axis. Baseflow is the retained Lyne–Hollick filter output, not a direct groundwater measurement. Drag horizontally for dates or vertically for value ranges; double-click resets." + hydroRangeText;
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Daily discharge and gage height hydrograph", subtitle, body, width, height) + "</div>";
  const svg = target.querySelector("svg");
  addChartInteraction(target, svg, { width, height, top: topA, bottom: bottomA, left, right, timeStart, timeEnd, stageScale, dischargeScale, series: [{ label: stageTitle, unit: data.stage.unit || "ft", points: shownStage }, { label: dischargeTitle, unit: data.discharge.unit || "ft³/s", points: shownDischarge }, ...(shownBaseflow.length ? [{ label: "Baseflow · retained filter output", unit: "ft³/s", points: shownBaseflow }] : [])], onRangeSelected: (startTime, endTime) => {
    $("#hydrograph-start").value = isoDate(new Date(startTime).toISOString());
    $("#hydrograph-end").value = isoDate(new Date(endTime).toISOString());
    $("#hydrograph-window").value = "custom";
  }, onValueRangeSelected: (_startY, _endY, dischargeStart, dischargeEnd, stageStart, stageEnd) => {
    if (dischargeStart != null && dischargeEnd != null) state.hydroDischargeRange = [Math.min(dischargeStart, dischargeEnd), Math.max(dischargeStart, dischargeEnd)];
    if (stageStart != null && stageEnd != null) state.hydroStageRange = [Math.min(stageStart, stageEnd), Math.max(stageStart, stageEnd)];
  } });
}
function renderFdc() {
  const target = $("#flow-duration-chart");
  const usable = state.fdc.map((row) => ({ x: safeNumber(row.exceedance_probability_pct), y: safeNumber(row.discharge_cfs), n: row.n_valid_days })).filter((row) => row.x != null && row.y != null && row.y > 0).sort((a, b) => a.x - b.x);
  if (!usable.length) { target.innerHTML = '<p class="chart-empty">No Flow Duration Curve output is available.</p>'; return; }
  const width = 1120, height = 390, left = 86, right = 1065, top = 38, bottom = 305;
  const logarithmic = $("#fdc-scale").value === "log";
  const xRange = state.fdcRange || [0, 100];
  const yRange = state.fdcValueRange;
  const selected = usable.filter((row) => row.x >= xRange[0] && row.x <= xRange[1] && (!yRange || (row.y >= yRange[0] && row.y <= yRange[1])));
  const displayed = selected.length ? selected : usable.filter((row) => row.x >= xRange[0] && row.x <= xRange[1]);
  const scale = scaleFor((yRange || displayed.map((row) => row.y)).filter((value) => value > 0), top, bottom, logarithmic);
  const sx = (value) => left + (value - xRange[0]) / (xRange[1] - xRange[0] || 1) * (right - left);
  const display = displayed.length > 1800 ? displayed.filter((_row, index) => index % Math.ceil(displayed.length / 1800) === 0) : displayed;
  const probabilityTicks = [...new Set([xRange[0], 0, 5, 10, 25, 50, 75, 90, 100, xRange[1]])].filter((value) => value >= xRange[0] && value <= xRange[1]).sort((a, b) => a - b);
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + (scale ? scale.ticks.map((value) => '<line x1="' + left + '" y1="' + scale.scale(value) + '" x2="' + right + '" y2="' + scale.scale(value) + '" class="chart-grid"/><text x="' + (left - 9) + '" y="' + (scale.scale(value) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(formatNumber(value, value < 10 ? 2 : 0)) + "</text>").join("") : "") + probabilityTicks.map((value, index) => '<line x1="' + sx(value) + '" y1="' + top + '" x2="' + sx(value) + '" y2="' + bottom + '" class="chart-grid"/><text x="' + sx(value) + '" y="' + (bottom + 21) + '" text-anchor="' + (index === 0 ? "start" : index === probabilityTicks.length - 1 ? "end" : "middle") + '" class="axis-label">' + value + "%</text>").join("") + (scale ? '<path d="' + display.map((row, index) => (index ? "L" : "M") + sx(row.x).toFixed(1) + "," + scale.scale(row.y).toFixed(1)).join(" ") + '" class="discharge-line"/>' : "") + '<line id="fdc-crosshair" x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + bottom + '" class="crosshair-line" display="none"/><circle id="fdc-hover-point" cx="0" cy="0" r="4" class="hover-point" display="none"/><text x="' + ((left + right) / 2) + '" y="' + (height - 20) + '" text-anchor="middle" class="axis-label">Exceedance probability (%)</text><text x="22" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 22 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">Daily discharge (ft³/s)</text><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="plot-hit"/>';
  const rangeText = state.fdcRange ? " Selected exceedance probability " + formatNumber(xRange[0], 1) + "–" + formatNumber(xRange[1], 1) + "%." + (yRange ? " Selected discharge " + formatNumber(yRange[0], 1) + "–" + formatNumber(yRange[1], 1) + " ft³/s." : "") : "";
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Daily Flow Duration Curve", "Empirical daily discharge; drag a rectangle to select probability and discharge ranges. Double-click resets the selection." + rangeText, body, width, height) + "</div>";
  const hit = target.querySelector(".plot-hit");
  const svg = target.querySelector("svg");
  const preview = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  preview.setAttribute("id", "fdc-brush-preview"); preview.setAttribute("class", "annual-brush-preview"); preview.setAttribute("display", "none");
  svg.insertBefore(preview, hit);
  const pixelPosition = (event) => { const rect = svg.getBoundingClientRect(); return { x: (event.clientX - rect.left) * width / rect.width, y: (event.clientY - rect.top) * height / rect.height }; };
  const clampX = (value) => Math.max(left, Math.min(right, value));
  const clampY = (value) => Math.max(top, Math.min(bottom, value));
  const xValueAt = (pixel) => xRange[0] + (clampX(pixel) - left) / (right - left || 1) * (xRange[1] - xRange[0]);
  const yValueAt = (pixel) => {
    const fraction = (bottom - clampY(pixel)) / (bottom - top || 1);
    if (logarithmic) return Math.pow(10, Math.log10(scale.minimum) + fraction * (Math.log10(scale.maximum) - Math.log10(scale.minimum)));
    return scale.minimum + fraction * (scale.maximum - scale.minimum);
  };
  let dragStart = null;
  const tooltip = (event) => {
    const rect = target.querySelector("svg").getBoundingClientRect();
    const pixel = (event.clientX - rect.left) * width / rect.width;
    const x = xValueAt(pixel);
    const row = displayed.reduce((best, item) => !best || Math.abs(item.x - x) < Math.abs(best.x - x) ? item : best, null);
    if (!row || !scale) return;
    target.querySelector(".chart-tooltip")?.remove();
    const crosshair = target.querySelector("#fdc-crosshair");
    const hoverPoint = target.querySelector("#fdc-hover-point");
    crosshair.setAttribute("x1", sx(row.x)); crosshair.setAttribute("x2", sx(row.x)); crosshair.setAttribute("display", "block");
    hoverPoint.setAttribute("cx", sx(row.x)); hoverPoint.setAttribute("cy", scale.scale(row.y)); hoverPoint.setAttribute("display", "block");
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.innerHTML = "<strong>" + formatNumber(row.x, 2) + "% exceedance</strong><span>" + formatNumber(row.y, 2) + " ft³/s</span><span>n = " + formatNumber(row.n, 0) + " valid days</span>";
    tip.style.left = Math.max(8, event.clientX - target.getBoundingClientRect().left - 80) + "px";
    tip.style.top = Math.max(8, event.clientY - target.getBoundingClientRect().top - 75) + "px";
    target.append(tip);
  };
  hit.addEventListener("mousemove", tooltip);
  hit.addEventListener("mouseleave", () => { target.querySelector(".chart-tooltip")?.remove(); target.querySelector("#fdc-crosshair")?.setAttribute("display", "none"); target.querySelector("#fdc-hover-point")?.setAttribute("display", "none"); });
  hit.addEventListener("pointerdown", (event) => { dragStart = pixelPosition(event); hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    const end = pixelPosition(event);
    const x1 = clampX(dragStart.x), x2 = clampX(end.x), y1 = clampY(dragStart.y), y2 = clampY(end.y);
    preview.setAttribute("x", Math.min(x1, x2)); preview.setAttribute("y", Math.min(y1, y2)); preview.setAttribute("width", Math.abs(x2 - x1)); preview.setAttribute("height", Math.abs(y2 - y1)); preview.setAttribute("display", "block");
  });
  hit.addEventListener("pointerup", (event) => {
    if (!dragStart) return;
    const end = pixelPosition(event);
    const horizontalDistance = Math.abs(end.x - dragStart.x), verticalDistance = Math.abs(end.y - dragStart.y);
    if (horizontalDistance > 12 || verticalDistance > 12) {
      state.fdcRange = [Math.min(xValueAt(dragStart.x), xValueAt(end.x)), Math.max(xValueAt(dragStart.x), xValueAt(end.x))];
      state.fdcValueRange = verticalDistance > 12 ? [Math.min(yValueAt(dragStart.y), yValueAt(end.y)), Math.max(yValueAt(dragStart.y), yValueAt(end.y))] : null;
      renderFdc();
    }
    preview.setAttribute("display", "none"); dragStart = null;
  });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.fdcRange = null; state.fdcValueRange = null; renderFdc(); });
}
function renderFdcTable() {
  const requested = [1, 2, 3, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 96, 97, 98, 99];
  const supplied = state.hydrology?.flow_duration_quantiles || [];
  const full = state.fdc || [];
  const rows = requested.map((probability) => {
    const exact = supplied.find((row) => Math.abs(Number(row.exceedance_probability_pct) - probability) < 0.001);
    if (exact) return exact;
    const nearest = full.filter((row) => safeNumber(row.exceedance_probability_pct) != null && safeNumber(row.discharge_cfs) != null).reduce((best, row) => !best || Math.abs(Number(row.exceedance_probability_pct) - probability) < Math.abs(Number(best.exceedance_probability_pct) - probability) ? row : best, null);
    if (!nearest) return null;
    return { ...nearest, display_exceedance_probability_pct: probability };
  }).filter(Boolean);
  $("#fdc-table").innerHTML = rows.map((row) => "<tr><td>" + escapeHtml(formatNumber(row.display_exceedance_probability_pct ?? row.exceedance_probability_pct, 1)) + "%</td><td>" + escapeHtml(formatNumber(row.discharge_cfs, 2)) + " ft³/s</td><td>" + escapeHtml(formatNumber(row.n_valid_days, 0)) + "</td></tr>").join("") || '<tr><td colspan="3" class="muted">No FDC quantile table is available.</td></tr>';
}
function waterYear(date) {
  const value = new Date(String(date) + "T00:00:00Z");
  return value.getUTCMonth() >= 9 ? value.getUTCFullYear() + 1 : value.getUTCFullYear();
}
function waterYearDay(date) {
  const value = new Date(String(date) + "T00:00:00Z");
  const year = value.getUTCMonth() >= 9 ? value.getUTCFullYear() : value.getUTCFullYear() - 1;
  return Math.floor((value - Date.UTC(year, 9, 1)) / 86400000) + 1;
}
function waterYearMonth(date) {
  const value = new Date(String(date) + "T00:00:00Z");
  return value.getUTCMonth() >= 9 ? value.getUTCMonth() - 8 : value.getUTCMonth() + 4;
}
function normalizeDailyPattern(points) {
  return points.map((point) => ({ ...point, wy: waterYear(point.date), day: waterYearDay(point.date) })).filter((point) => point.day >= 1 && point.day <= 367);
}
function heatColor(value, minimum, maximum) {
  const low = Math.log10(Math.max(0.1, minimum));
  const high = Math.log10(Math.max(minimum, maximum));
  const fraction = Math.max(0, Math.min(1, (Math.log10(Math.max(0.1, value)) - low) / (high - low || 1)));
  return COLORS.heat[Math.min(COLORS.heat.length - 1, Math.floor(fraction * COLORS.heat.length))];
}
function renderWaterYearControls() {
  const years = [...new Set(state.dailyPattern.map((point) => point.wy))].sort((a, b) => b - a);
  const current = years[0];
  const selected = Array.isArray(state.selectedYears) ? state.selectedYears : [current];
  state.selectedYears = selected.filter((year) => years.includes(year)).slice(0, 8);
  if (!state.selectedYears.length && current != null) state.selectedYears = [current];
  $("#water-year-controls").innerHTML = '<label class="water-year-select-label">Compare Water Years<select id="water-year-select" multiple size="4">' + years.map((year) => '<option value="' + year + '" ' + (state.selectedYears.includes(year) ? "selected" : "") + ">WY " + year + (year === current ? " (current)" : "") + "</option>").join("") + '</select><small class="control-hint">Select one or more; hold Ctrl/Cmd to add years.</small></label><label>Mode<select id="wy-mode"><option value="absolute">Absolute discharge (ft³/s)</option><option value="normalized">Normalized by full-period mean discharge</option></select></label>';
  $("#water-year-select").addEventListener("change", () => { state.selectedYears = [...$("#water-year-select").selectedOptions].map((option) => Number(option.value)).slice(0, 8); renderWaterYearComparison(); });
  $("#wy-mode").addEventListener("change", renderWaterYearComparison);
}
function renderWaterYearHeatmap() {
  const target = $("#water-year-heatmap");
  if (!state.dailyPattern.length) { target.innerHTML = '<p class="chart-empty">Daily discharge is unavailable for the Water-Year pattern.</p>'; return; }
  const years = [...new Set(state.dailyPattern.map((point) => point.wy))].sort((a, b) => a - b);
  const width = 1120, left = 112, right = 1065, top = 32, rowHeight = Math.max(4, Math.min(9, 370 / years.length)), height = top + years.length * rowHeight + 72;
  const values = state.dailyPattern.map((point) => point.value).filter((value) => value > 0);
  const minimum = Math.min(...values), maximum = Math.max(...values);
  const rowMap = new globalThis.Map(years.map((year, index) => [year, index]));
  const cells = state.dailyPattern.filter((point) => point.value > 0).map((point) => {
    const x = left + (point.day - 1) / 366 * (right - left);
    const y = top + rowMap.get(point.wy) * rowHeight;
    const cellWidth = Math.max(1.2, (right - left) / 367 + .2);
    return '<rect x="' + x.toFixed(2) + '" y="' + y.toFixed(2) + '" width="' + cellWidth.toFixed(2) + '" height="' + Math.max(2, rowHeight - .4).toFixed(2) + '" fill="' + heatColor(point.value, minimum, maximum) + '"/>';
  }).join("");
  const labels = years.filter((_year, index) => index % Math.max(1, Math.ceil(years.length / 9)) === 0).map((year) => '<text x="' + (left - 9) + '" y="' + (top + rowMap.get(year) * rowHeight + rowHeight) + '" text-anchor="end" class="axis-label">WY ' + year + "</text>").join("");
  const monthPositions = [["Oct", 0], ["Jan", 92], ["Apr", 183], ["Jul", 274], ["Sep", 334]].map((item) => '<text x="' + (left + item[1] / 366 * (right - left)) + '" y="' + (height - 25) + '" text-anchor="middle" class="axis-label">' + item[0] + "</text>").join("");
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + years.length * rowHeight + '" class="chart-plot"/>' + cells + labels + monthPositions + '<text x="' + ((left + right) / 2) + '" y="' + (height - 5) + '" text-anchor="middle" class="axis-label">Water-Year day (October–September)</text>';
  target.innerHTML = '<div class="chart-shell heatmap-shell">' + svgFrame("Water-Year daily discharge heatmap", "Daily discharge by Water Year; color uses a log-scaled cividis-like palette; annual mean is shown in the comparison view.", body, width, height) + "</div>";
  const svg = target.querySelector("svg");
  svg.addEventListener("mousemove", (event) => {
    const rect = svg.getBoundingClientRect();
    const px = (event.clientX - rect.left) * width / rect.width;
    const py = (event.clientY - rect.top) * height / rect.height;
    const day = Math.round((px - left) / (right - left) * 366) + 1;
    const row = Math.floor((py - top) / rowHeight);
    if (day < 1 || day > 367 || row < 0 || row >= years.length) return;
    const year = years[row];
    const point = state.dailyPattern.find((candidate) => candidate.wy === year && Math.abs(candidate.day - day) <= 1);
    if (!point) return;
    target.querySelector(".chart-tooltip")?.remove();
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.innerHTML = "<strong>WY " + year + "</strong><span>" + escapeHtml(point.date) + "</span><span>" + formatNumber(point.value, 2) + " ft³/s</span>";
    tip.style.left = Math.max(8, event.clientX - target.getBoundingClientRect().left - 60) + "px";
    tip.style.top = Math.max(8, event.clientY - target.getBoundingClientRect().top - 70) + "px";
    target.append(tip);
  });
  svg.addEventListener("mouseleave", () => target.querySelector(".chart-tooltip")?.remove());
}
function quantile(values, fraction) {
  const usable = values.filter((value) => value != null).sort((a, b) => a - b);
  if (!usable.length) return null;
  const index = (usable.length - 1) * fraction;
  const lower = Math.floor(index), upper = Math.ceil(index);
  return usable[lower] + (usable[upper] - usable[lower]) * (index - lower);
}
function renderWaterYearComparison() {
  const target = $("#water-year-comparison");
  if (!state.dailyPattern.length) { target.innerHTML = ""; return; }
  const mode = $("#wy-mode")?.value || "absolute";
  const groups = new Map();
  for (const point of state.dailyPattern) {
    if (!groups.has(point.day)) groups.set(point.day, []);
    groups.get(point.day).push(point.value);
  }
  const meanReference = [...groups.values()].flat().filter((value) => value != null);
  const denominator = meanReference.length ? meanReference.reduce((sum, value) => sum + value, 0) / meanReference.length : 1;
  const reference = [...groups.entries()].map(([day, values]) => ({ day, mean: values.reduce((sum, value) => sum + value, 0) / values.length, p25: quantile(values, .25), p75: quantile(values, .75) })).sort((a, b) => a.day - b.day);
  const availableYears = [...new Set(state.dailyPattern.map((point) => point.wy))].sort((a, b) => b - a);
  const years = state.selectedYears?.length ? state.selectedYears : availableYears.slice(0, 1);
  const series = years.map((year, index) => ({ year, points: state.dailyPattern.filter((point) => point.wy === year).sort((a, b) => a.day - b.day), color: COLORS.palette[index % COLORS.palette.length] })).filter((item) => item.points.length);
  const values = [...reference.flatMap((row) => [row.p25, row.p75, row.mean]), ...series.flatMap((item) => item.points.map((point) => point.value))].filter((value) => value != null);
  const width = 1120, height = 390, left = 86, right = 1065, top = 40, bottom = 302;
  const dayDomain = state.waterYearDayRange || [1, 367];
  const valueDomain = state.waterYearValueRange;
  const scale = scaleFor(valueDomain || (mode === "normalized" ? values.map((value) => value / denominator) : values), top, bottom, false);
  const sx = (day) => left + (day - dayDomain[0]) / (dayDomain[1] - dayDomain[0] || 1) * (right - left);
  const value = (number) => mode === "normalized" ? number / denominator : number;
  const visiblePoint = (point) => point.day >= dayDomain[0] && point.day <= dayDomain[1] && (!valueDomain || (value(point.value ?? point.mean) >= valueDomain[0] && value(point.value ?? point.mean) <= valueDomain[1]));
  const visibleReference = reference.filter((point) => point.day >= dayDomain[0] && point.day <= dayDomain[1]);
  const visibleSeries = series.map((item) => ({ ...item, points: item.points.filter(visiblePoint) }));
  const path = (points, key) => points.length ? points.map((point, index) => (index ? "L" : "M") + sx(point.day).toFixed(1) + "," + scale.scale(value(point[key])).toFixed(1)).join(" ") : "";
  const band = visibleReference.map((point, index) => (index ? "L" : "M") + sx(point.day).toFixed(1) + "," + scale.scale(value(point.p75)).toFixed(1)).join(" ") + " " + visibleReference.slice().reverse().map((point) => "L" + sx(point.day).toFixed(1) + "," + scale.scale(value(point.p25)).toFixed(1)).join(" ") + " Z";
  const tickDigits = mode === "normalized" ? 0 : undefined;
  const rangeText = state.waterYearDayRange ? " Selected Water-Year day " + dayDomain[0] + "–" + dayDomain[1] + "." + (valueDomain ? " Selected discharge " + formatNumber(valueDomain[0], 1) + "–" + formatNumber(valueDomain[1], 1) + " ft³/s." : "") : "";
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + scale.ticks.map((tick) => '<line x1="' + left + '" y1="' + scale.scale(tick) + '" x2="' + right + '" y2="' + scale.scale(tick) + '" class="chart-grid"/><text x="' + (left - 9) + '" y="' + (scale.scale(tick) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(formatNumber(tick, tickDigits == null ? (tick < 10 ? 2 : 0) : tickDigits)) + "</text>").join("") + '<path d="' + band + '" class="reference-band"/><path d="' + path(visibleReference, "mean") + '" class="reference-line"/><text x="' + (left + 8) + '" y="' + (top + 17) + '" class="legend-label">Full-period mean · P25–P75 band</text>' + visibleSeries.map((item) => '<path d="' + item.points.map((point, index) => (index ? "L" : "M") + sx(point.day).toFixed(1) + "," + scale.scale(value(point.value)).toFixed(1)).join(" ") + '" class="year-line" style="stroke:' + item.color + '"/><text x="' + (right - 6) + '" y="' + (top + 16 + visibleSeries.indexOf(item) * 16) + '" text-anchor="end" class="legend-label" style="fill:' + item.color + '">WY ' + item.year + "</text>").join("") + '<text x="' + ((left + right) / 2) + '" y="' + (height - 22) + '" text-anchor="middle" class="axis-label">Water-Year day (October–September)</text><text x="22" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 22 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">' + (mode === "normalized" ? "Discharge / full-period mean discharge" : "Discharge (ft³/s)") + "</text><line id=\"wy-crosshair\" x1=\"" + left + '" y1="' + top + '" x2="' + left + '" y2="' + bottom + '" class="crosshair-line" display="none"/><rect id="wy-brush-preview" x="' + left + '" y="' + top + '" width="0" height="0" class="annual-brush-preview" display="none"/><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="plot-hit"/>';
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Selected Water-Year daily discharge", "Colored lines are selected Water Years; dark line is the full-period daily mean; band is P25–P75. Drag a rectangle to select Water-Year days and discharge values; double-click resets." + rangeText, body, width, height) + "</div>";
  const svg = target.querySelector("svg");
  svg.addEventListener("mousemove", (event) => {
    const rect = svg.getBoundingClientRect();
    const px = (event.clientX - rect.left) * width / rect.width;
    const day = Math.max(1, Math.min(367, Math.round((px - left) / (right - left) * 366) + 1));
    const referencePoint = reference.reduce((best, point) => !best || Math.abs(point.day - day) < Math.abs(best.day - day) ? point : best, null);
    const selected = visibleSeries.map((item) => ({ ...item, point: item.points.reduce((best, point) => !best || Math.abs(point.day - day) < Math.abs(best.day - day) ? point : best, null) })).filter((item) => item.point);
    const crosshair = svg.querySelector("#wy-crosshair");
    crosshair.setAttribute("x1", sx(day)); crosshair.setAttribute("x2", sx(day)); crosshair.setAttribute("display", "block");
    target.querySelector(".chart-tooltip")?.remove();
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.innerHTML = "<strong>Water-Year day " + day + "</strong>" + (referencePoint ? "<span>Full-period mean: " + formatNumber(value(referencePoint.mean), mode === "normalized" ? 2 : 1) + (mode === "normalized" ? "×" : " ft³/s") + "</span><span>P25–P75: " + formatNumber(value(referencePoint.p25), mode === "normalized" ? 2 : 1) + "–" + formatNumber(value(referencePoint.p75), mode === "normalized" ? 2 : 1) + (mode === "normalized" ? "×" : " ft³/s") + "</span>" : "") + selected.map((item) => "<span style=\"color:" + item.color + "\">WY " + item.year + ": " + formatNumber(value(item.point.value), mode === "normalized" ? 2 : 1) + (mode === "normalized" ? "×" : " ft³/s") + "</span>").join("");
    tip.style.left = Math.max(8, event.clientX - target.getBoundingClientRect().left - 90) + "px";
    tip.style.top = Math.max(8, event.clientY - target.getBoundingClientRect().top - 86) + "px";
    target.append(tip);
  });
  svg.addEventListener("mouseleave", () => { target.querySelector(".chart-tooltip")?.remove(); target.querySelector("#wy-crosshair")?.setAttribute("display", "none"); });
  const hit = svg.querySelector(".plot-hit");
  const preview = svg.querySelector("#wy-brush-preview");
  const pixelPosition = (event) => { const rect = svg.getBoundingClientRect(); return { x: (event.clientX - rect.left) * width / rect.width, y: (event.clientY - rect.top) * height / rect.height }; };
  const clampX = (value) => Math.max(left, Math.min(right, value));
  const clampY = (value) => Math.max(top, Math.min(bottom, value));
  const dayAt = (pixel) => Math.round(dayDomain[0] + (clampX(pixel) - left) / (right - left || 1) * (dayDomain[1] - dayDomain[0]));
  const valueAt = (pixel) => scale.minimum + (bottom - clampY(pixel)) / (bottom - top || 1) * (scale.maximum - scale.minimum);
  let dragStart = null;
  hit.addEventListener("pointerdown", (event) => { dragStart = pixelPosition(event); hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => { if (!dragStart) return; const end = pixelPosition(event); const x1 = clampX(dragStart.x), x2 = clampX(end.x), y1 = clampY(dragStart.y), y2 = clampY(end.y); preview.setAttribute("x", Math.min(x1, x2)); preview.setAttribute("y", Math.min(y1, y2)); preview.setAttribute("width", Math.abs(x2 - x1)); preview.setAttribute("height", Math.abs(y2 - y1)); preview.setAttribute("display", "block"); });
  hit.addEventListener("pointerup", (event) => { if (!dragStart) return; const end = pixelPosition(event); if (Math.abs(end.x - dragStart.x) > 12 || Math.abs(end.y - dragStart.y) > 12) { state.waterYearDayRange = [Math.min(dayAt(dragStart.x), dayAt(end.x)), Math.max(dayAt(dragStart.x), dayAt(end.x))]; state.waterYearValueRange = [Math.min(valueAt(dragStart.y), valueAt(end.y)), Math.max(valueAt(dragStart.y), valueAt(end.y))]; renderWaterYearComparison(); } preview.setAttribute("display", "none"); dragStart = null; });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.waterYearDayRange = null; state.waterYearValueRange = null; renderWaterYearComparison(); });
}
function renderAnnualFlow() {
  const rows = (state.hydrology?.annual_hydrology || []).slice().sort((a, b) => Number(b.water_year) - Number(a.water_year));
  $("#annual-flow-rows").innerHTML = rows.map((row) => "<tr><td>WY " + escapeHtml(row.water_year) + "</td><td>" + (String(row.is_complete).toLowerCase() === "true" ? "Yes" : "No") + "</td><td>" + escapeHtml(formatNumber(row.mean_discharge_cfs, 1)) + "</td><td>" + escapeHtml(formatNumber(row.min_daily_discharge_cfs, 1)) + "</td><td>" + escapeHtml(formatNumber(row.max_daily_discharge_cfs, 1)) + "</td><td>" + escapeHtml(row.date_of_max_daily_discharge) + "</td></tr>").join("") || '<tr><td colspan="6" class="muted">No annual flow table is available.</td></tr>';
}

function annualRunoffRows() {
  return (state.hydrology?.annual_hydrology || []).map((row) => ({
    ...row,
    year: safeNumber(row.water_year),
    complete: String(row.is_complete).toLowerCase() === "true",
  })).filter((row) => Number.isFinite(row.year) && Object.keys(ANNUAL_RUNOFF_FIELDS).some((key) => safeNumber(row[key]) != null)).sort((left, right) => left.year - right.year);
}

function annualFieldValue(row, key) {
  return safeNumber(row[key]);
}

function horizontalScale(values, left, right) {
  const usable = values.filter((value) => Number.isFinite(value));
  if (!usable.length) return null;
  let minimum = Math.min(...usable);
  let maximum = Math.max(...usable);
  const padding = (maximum - minimum) * 0.04 || Math.max(Math.abs(minimum) * 0.04, 1);
  minimum -= padding;
  maximum += padding;
  return {
    minimum,
    maximum,
    scale: (value) => left + (value - minimum) / (maximum - minimum || 1) * (right - left),
    ticks: niceTicks(minimum, maximum, 6),
  };
}

function linearFit(rows, xKey, yKey) {
  const samples = rows.map((row) => ({ x: annualFieldValue(row, xKey), y: annualFieldValue(row, yKey) })).filter((point) => point.x != null && point.y != null);
  if (samples.length < 2) return null;
  const meanX = samples.reduce((sum, point) => sum + point.x, 0) / samples.length;
  const meanY = samples.reduce((sum, point) => sum + point.y, 0) / samples.length;
  const denominator = samples.reduce((sum, point) => sum + (point.x - meanX) ** 2, 0);
  if (!denominator) return null;
  const slope = samples.reduce((sum, point) => sum + (point.x - meanX) * (point.y - meanY), 0) / denominator;
  const intercept = meanY - slope * meanX;
  const total = samples.reduce((sum, point) => sum + (point.y - meanY) ** 2, 0);
  const residual = samples.reduce((sum, point) => sum + (point.y - (slope * point.x + intercept)) ** 2, 0);
  const degreesOfFreedom = samples.length - 2;
  const residualStandardError = degreesOfFreedom > 0 ? Math.sqrt(residual / degreesOfFreedom) : null;
  return { slope, intercept, rSquared: total ? Math.max(0, 1 - residual / total) : null, samples, meanX, denominator, degreesOfFreedom, residualStandardError };
}

function tCritical95(degreesOfFreedom) {
  const critical = [
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
  ];
  if (!Number.isFinite(degreesOfFreedom) || degreesOfFreedom < 1) return null;
  return degreesOfFreedom <= critical.length ? critical[degreesOfFreedom - 1] : 1.96;
}

function linearFitInterval(fit, x) {
  if (!fit || fit.residualStandardError == null || fit.denominator <= 0) return null;
  const critical = tCritical95(fit.degreesOfFreedom);
  if (critical == null) return null;
  const fitted = fit.slope * x + fit.intercept;
  const margin = critical * fit.residualStandardError * Math.sqrt(1 / fit.samples.length + ((x - fit.meanX) ** 2) / fit.denominator);
  return { fitted, lower: fitted - margin, upper: fitted + margin };
}

function annualRunoffFormat(value, key) {
  const field = ANNUAL_RUNOFF_FIELDS[key] || {};
  return formatNumber(value, field.integer ? 0 : key === "total_runoff_acre_ft" ? 0 : 1);
}

function renderAnnualRunoffControls(rows) {
  const xKeys = Object.keys(ANNUAL_RUNOFF_FIELDS).filter((key) => rows.some((row) => annualFieldValue(row, key) != null));
  const yKeys = xKeys.filter((key) => key !== "water_year");
  if (!xKeys.includes(state.annualRunoffX)) state.annualRunoffX = "water_year";
  if (!yKeys.includes(state.annualRunoffY)) state.annualRunoffY = yKeys.includes("total_runoff_acre_ft") ? "total_runoff_acre_ft" : yKeys[0];
  const minimumYear = rows[0]?.year;
  const maximumYear = rows.at(-1)?.year;
  const range = state.annualRunoffRange || [minimumYear, maximumYear];
  state.annualRunoffRange = [Math.max(minimumYear, range[0]), Math.min(maximumYear, range[1])];
  const xOptions = xKeys.map((key) => '<option value="' + key + '"' + (key === state.annualRunoffX ? " selected" : "") + ">" + escapeHtml(ANNUAL_RUNOFF_FIELDS[key].label + " (" + ANNUAL_RUNOFF_FIELDS[key].unit + ")") + "</option>").join("");
  const yOptions = yKeys.map((key) => '<option value="' + key + '"' + (key === state.annualRunoffY ? " selected" : "") + ">" + escapeHtml(ANNUAL_RUNOFF_FIELDS[key].label + " (" + ANNUAL_RUNOFF_FIELDS[key].unit + ")") + "</option>").join("");
  $("#annual-runoff-controls").innerHTML = '<label>X axis<select id="annual-runoff-x">' + xOptions + '</select></label><label>Y axis<select id="annual-runoff-y">' + yOptions + '</select></label><span class="year-selection-summary"><strong>Selected Water Years</strong><output id="annual-runoff-range-label">WY ' + state.annualRunoffRange[0] + '–WY ' + state.annualRunoffRange[1] + '</output><small>Drag across the plot to change the range; double-click resets it.</small></span>';
  $("#annual-runoff-x").addEventListener("change", (event) => { state.annualRunoffX = event.target.value; state.annualRunoffValueRange = null; renderAnnualRunoff(); });
  $("#annual-runoff-y").addEventListener("change", (event) => { state.annualRunoffY = event.target.value; state.annualRunoffValueRange = null; renderAnnualRunoff(); });
}

function renderAnnualRunoff() {
  const target = $("#annual-runoff-chart");
  const rows = annualRunoffRows();
  if (!rows.length) {
    $("#annual-runoff-controls").innerHTML = "";
    $("#annual-runoff-note").textContent = "No annual runoff volume is available in the station package.";
    target.innerHTML = '<p class="chart-empty">No annual runoff series is available.</p>';
    return;
  }
  renderAnnualRunoffControls(rows);
  const xKey = state.annualRunoffX;
  const yKey = state.annualRunoffY;
  const plotted = rows.filter((row) => annualFieldValue(row, xKey) != null && annualFieldValue(row, yKey) != null);
  if (!plotted.length) { target.innerHTML = '<p class="chart-empty">The selected axis fields have no overlapping annual values.</p>'; return; }
  const firstYear = rows[0].year;
  const lastYear = rows.at(-1).year;
  const selectedRange = state.annualRunoffRange || [firstYear, lastYear];
  const valueRange = state.annualRunoffValueRange;
  const selected = plotted.filter((row) => row.year >= selectedRange[0] && row.year <= selectedRange[1] && (!valueRange || (annualFieldValue(row, yKey) >= valueRange[0] && annualFieldValue(row, yKey) <= valueRange[1])));
  const completeSelected = selected.filter((row) => row.complete);
  const width = 1120, height = 470, left = 92, right = 1065, top = 54, bottom = 350;
  const xScale = horizontalScale(plotted.map((row) => annualFieldValue(row, xKey)), left, right);
  const fit = linearFit(completeSelected, xKey, yKey);
  const fitSamples = fit ? fit.samples.flatMap((point) => { const interval = linearFitInterval(fit, point.x); return interval ? [interval.lower, interval.upper] : []; }) : [];
  const yScale = scaleFor([...plotted.map((row) => annualFieldValue(row, yKey)), ...fitSamples], top, bottom, false);
  const sx = (row) => xScale.scale(annualFieldValue(row, xKey));
  const sy = (row) => yScale.scale(annualFieldValue(row, yKey));
  const xTicks = xScale.ticks.map((value) => '<line x1="' + xScale.scale(value) + '" y1="' + top + '" x2="' + xScale.scale(value) + '" y2="' + bottom + '" class="chart-grid"/><text x="' + xScale.scale(value) + '" y="' + (bottom + 22) + '" text-anchor="middle" class="axis-label">' + escapeHtml(annualRunoffFormat(value, xKey)) + '</text>').join("");
  const yTicks = yScale.ticks.map((value) => '<line x1="' + left + '" y1="' + yScale.scale(value) + '" x2="' + right + '" y2="' + yScale.scale(value) + '" class="chart-grid"/><text x="' + (left - 10) + '" y="' + (yScale.scale(value) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(annualRunoffFormat(value, yKey)) + '</text>').join("");
  const selectionValues = selected.map((row) => annualFieldValue(row, xKey)).filter((value) => value != null);
  const selectionLeft = selectionValues.length ? xScale.scale(Math.min(...selectionValues)) : left;
  const selectionRight = selectionValues.length ? xScale.scale(Math.max(...selectionValues)) : right;
  const selectedYValues = selected.map((row) => annualFieldValue(row, yKey)).filter((value) => value != null);
  const selectionTop = valueRange && selectedYValues.length ? yScale.scale(Math.max(...selectedYValues)) : top;
  const selectionBottom = valueRange && selectedYValues.length ? yScale.scale(Math.min(...selectedYValues)) : bottom;
  const selection = '<rect x="' + selectionLeft.toFixed(1) + '" y="' + selectionTop.toFixed(1) + '" width="' + Math.max(0, selectionRight - selectionLeft).toFixed(1) + '" height="' + Math.max(0, selectionBottom - selectionTop).toFixed(1) + '" class="annual-selection"/>';
  const points = plotted.map((row) => '<circle cx="' + sx(row).toFixed(1) + '" cy="' + sy(row).toFixed(1) + '" r="' + (row.complete ? 4.2 : 4.8) + '" class="availability-point ' + (row.complete ? "complete" : "incomplete") + '"><title>WY ' + row.year + ' · ' + escapeHtml(ANNUAL_RUNOFF_FIELDS[yKey].label) + ': ' + escapeHtml(annualRunoffFormat(annualFieldValue(row, yKey), yKey) + ' ' + ANNUAL_RUNOFF_FIELDS[yKey].unit) + (row.complete ? " · complete" : " · incomplete") + '</title></circle>').join("");
  const fitCurve = fit ? (() => {
    const fitMin = Math.min(...fit.samples.map((point) => point.x));
    const fitMax = Math.max(...fit.samples.map((point) => point.x));
    const steps = Array.from({ length: 32 }, (_, index) => fitMin + (fitMax - fitMin) * index / 31);
    const path = steps.map((value, index) => (index ? "L" : "M") + xScale.scale(value).toFixed(1) + "," + yScale.scale(fit.slope * value + fit.intercept).toFixed(1)).join(" ");
    const intervals = steps.map((value) => linearFitInterval(fit, value)).filter(Boolean);
    const bandPath = intervals.length ? steps.map((value, index) => (index ? "L" : "M") + xScale.scale(value).toFixed(1) + "," + yScale.scale(intervals[index].upper).toFixed(1)).join(" ") + " " + steps.slice().reverse().map((_value, index) => "L" + xScale.scale(steps[steps.length - 1 - index]).toFixed(1) + "," + yScale.scale(intervals[intervals.length - 1 - index].lower).toFixed(1)).join(" ") + " Z" : "";
    return (bandPath ? '<path d="' + bandPath + '" class="availability-confidence-band"/>' : "") + '<path d="' + path + '" class="availability-trend"/>';
  })() : "";
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + selection + xTicks + yTicks + points + fitCurve + '<line id="annual-runoff-crosshair" x1="' + left + '" y1="' + top + '" x2="' + left + '" y2="' + bottom + '" class="crosshair-line" display="none"/><rect id="annual-runoff-brush-preview" x="' + left + '" y="' + top + '" width="0" height="0" class="annual-brush-preview" display="none"/><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="plot-hit"/><text x="' + (left + 10) + '" y="' + (top + 18) + '" class="legend-label">Complete Water Years</text><circle cx="' + (left + 4) + '" cy="' + (top + 14) + '" r="4" class="availability-point complete"/><text x="' + (left + 178) + '" y="' + (top + 18) + '" class="legend-label">Incomplete Water Year</text><circle cx="' + (left + 172) + '" cy="' + (top + 14) + '" r="4.5" class="availability-point incomplete"/>' + (fit ? '<rect x="' + (left + 326) + '" y="' + (top + 9) + '" width="28" height="10" class="availability-confidence-band"/><line x1="' + (left + 328) + '" y1="' + (top + 14) + '" x2="' + (left + 352) + '" y2="' + (top + 14) + '" class="availability-trend"/><text x="' + (left + 362) + '" y="' + (top + 18) + '" class="legend-label">Linear regression · 95% CI</text>' : "") + '<text x="' + ((left + right) / 2) + '" y="' + (height - 25) + '" text-anchor="middle" class="axis-label">' + escapeHtml(ANNUAL_RUNOFF_FIELDS[xKey].label + ' (' + ANNUAL_RUNOFF_FIELDS[xKey].unit + ')') + '</text><text x="22" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 22 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">' + escapeHtml(ANNUAL_RUNOFF_FIELDS[yKey].label + ' (' + ANNUAL_RUNOFF_FIELDS[yKey].unit + ')') + '</text>';
  const subtitle = "Annual values retained from the station hydrology package. Incomplete Water Years remain visible but are excluded from the trend fit.";
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Annual runoff and water availability", subtitle, body, width, height) + '</div>';
  const svg = target.querySelector("svg");
  const hit = svg.querySelector(".plot-hit");
  const xPosition = (event) => { const rect = svg.getBoundingClientRect(); return (event.clientX - rect.left) * width / rect.width; };
  const yPosition = (event) => { const rect = svg.getBoundingClientRect(); return (event.clientY - rect.top) * height / rect.height; };
  const clampX = (value) => Math.max(left, Math.min(right, value));
  const clampY = (value) => Math.max(top, Math.min(bottom, value));
  const yValueAt = (pixel) => yScale.minimum + (bottom - clampY(pixel)) / (bottom - top || 1) * (yScale.maximum - yScale.minimum);
  const nearest = (pixel) => plotted.reduce((best, row) => !best || Math.abs(sx(row) - pixel) < Math.abs(sx(best) - pixel) ? row : best, null);
  const removeTip = () => target.querySelector(".chart-tooltip")?.remove();
  let dragStart = null;
  let suppressClick = false;
  hit.addEventListener("mousemove", (event) => {
    const pixel = Math.max(left, Math.min(right, xPosition(event)));
    const row = nearest(pixel);
    if (!row) return;
    svg.querySelector("#annual-runoff-crosshair").setAttribute("x1", sx(row));
    svg.querySelector("#annual-runoff-crosshair").setAttribute("x2", sx(row));
    svg.querySelector("#annual-runoff-crosshair").setAttribute("display", "block");
    removeTip();
    const tip = document.createElement("div");
    tip.className = "chart-tooltip";
    tip.innerHTML = '<strong>WY ' + row.year + '</strong><span>' + escapeHtml(ANNUAL_RUNOFF_FIELDS[xKey].label) + ': ' + escapeHtml(annualRunoffFormat(annualFieldValue(row, xKey), xKey) + ' ' + ANNUAL_RUNOFF_FIELDS[xKey].unit) + '</span><span>' + escapeHtml(ANNUAL_RUNOFF_FIELDS[yKey].label) + ': ' + escapeHtml(annualRunoffFormat(annualFieldValue(row, yKey), yKey) + ' ' + ANNUAL_RUNOFF_FIELDS[yKey].unit) + '</span><span>' + (row.complete ? "Complete Water Year" : "Incomplete Water Year · excluded from trend") + '</span>';
    tip.style.left = Math.max(8, event.clientX - target.getBoundingClientRect().left - 100) + "px";
    tip.style.top = Math.max(8, event.clientY - target.getBoundingClientRect().top - 105) + "px";
    target.append(tip);
  });
  hit.addEventListener("mouseleave", () => { removeTip(); svg.querySelector("#annual-runoff-crosshair").setAttribute("display", "none"); });
  hit.addEventListener("pointerdown", (event) => { dragStart = { x: xPosition(event), y: yPosition(event) }; hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    const end = { x: xPosition(event), y: yPosition(event) };
    const preview = svg.querySelector("#annual-runoff-brush-preview");
    preview.setAttribute("x", Math.min(clampX(dragStart.x), clampX(end.x)));
    preview.setAttribute("y", Math.min(clampY(dragStart.y), clampY(end.y)));
    preview.setAttribute("width", Math.abs(clampX(end.x) - clampX(dragStart.x)));
    preview.setAttribute("height", Math.abs(clampY(end.y) - clampY(dragStart.y)));
    preview.setAttribute("display", "block");
  });
  hit.addEventListener("pointerup", (event) => {
    if (dragStart == null) return;
    const dragEnd = { x: xPosition(event), y: yPosition(event) };
    const horizontalDistance = Math.abs(dragEnd.x - dragStart.x);
    const verticalDistance = Math.abs(dragEnd.y - dragStart.y);
    if (horizontalDistance > 12 || verticalDistance > 12) {
      const startRow = nearest(Math.min(dragStart.x, dragEnd.x));
      const endRow = nearest(Math.max(dragStart.x, dragEnd.x));
      if (startRow && endRow) {
        state.annualRunoffRange = [Math.min(startRow.year, endRow.year), Math.max(startRow.year, endRow.year)];
        state.annualRunoffValueRange = verticalDistance > 12 ? [Math.min(yValueAt(dragEnd.y), yValueAt(dragStart.y)), Math.max(yValueAt(dragEnd.y), yValueAt(dragStart.y))] : null;
        suppressClick = true;
        renderAnnualRunoff();
      }
    }
    svg.querySelector("#annual-runoff-brush-preview").setAttribute("display", "none");
    dragStart = null;
  });
  hit.addEventListener("click", () => { if (suppressClick) suppressClick = false; });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.annualRunoffRange = null; state.annualRunoffValueRange = null; renderAnnualRunoff(); });
  const fitText = fit ? " Linear regression slope " + formatNumber(fit.slope, 2) + " per " + ANNUAL_RUNOFF_FIELDS[xKey].unit + ", R²=" + formatNumber(fit.rSquared, 3) + "; shaded band is the pointwise 95% CI for the mean fitted response." : " At least two complete Water Years are required for a linear regression.";
  const valueText = valueRange ? " Value range " + annualRunoffFormat(valueRange[0], yKey) + "–" + annualRunoffFormat(valueRange[1], yKey) + " " + ANNUAL_RUNOFF_FIELDS[yKey].unit + "." : "";
  $("#annual-runoff-note").textContent = "Selected WY " + selectedRange[0] + "–" + selectedRange[1] + ": " + selected.length.toLocaleString() + " annual rows, " + completeSelected.length.toLocaleString() + " complete rows used for the trend." + valueText + fitText + " Annual runoff is a volume derived from daily mean discharge and reported in acre-ft; it is a water-availability indicator, not a regulated allocation.";
  renderMonthlyAvailabilityChart();
}

function renderMonthlyAvailabilityChart() {
  const target = $("#monthly-availability-chart");
  const note = $("#monthly-availability-note");
  if (!target) return;
  const artifactRows = (state.hydrology?.monthly_hydrology || []).map((row) => ({
    month: safeNumber(row.wy_month),
    label: text(row.month_label),
    historicalCount: safeNumber(row.historical_water_year_count),
    historicalAf: safeNumber(row.historical_mean_runoff_acre_ft),
  })).filter((row) => row.month != null && row.month >= 1 && row.month <= 12 && row.historicalAf != null).sort((a, b) => a.month - b.month);
  if (!state.dailyPattern.length) {
    target.innerHTML = '<p class="chart-empty">Loading daily observations for Water-Year monthly runoff…</p>';
    if (note) note.textContent = "The selected-Water-Year bars will appear after the retained daily observations finish loading.";
    return;
  }
  const monthlyByYear = new Map();
  for (const point of state.dailyPattern) {
    const value = safeNumber(point.value);
    const year = safeNumber(point.wy);
    if (value == null || value < 0 || year == null || !point.date) continue;
    const month = waterYearMonth(point.date);
    const key = year + "-" + month;
    const row = monthlyByYear.get(key) || { year, month, runoff: 0, validDays: 0 };
    row.runoff += value * ACRE_FEET_PER_CFS_DAY;
    row.validDays += 1;
    monthlyByYear.set(key, row);
  }
  const monthlyRows = [...monthlyByYear.values()].sort((a, b) => a.year - b.year || a.month - b.month);
  const availableYears = [...new Set(monthlyRows.map((row) => row.year))].sort((a, b) => a - b);
  if (!availableYears.length) {
    target.innerHTML = '<p class="chart-empty">No valid daily observations are available for monthly runoff.</p>';
    if (note) note.textContent = "The retained daily discharge series has no valid Water-Year monthly values.";
    return;
  }
  const showBars = $("#monthly-runoff-toggle")?.checked !== false;
  const width = 1120, height = 360, left = 92, right = 1036, top = 48, bottom = 276;
  const months = artifactRows.length ? artifactRows : [...new Set(monthlyRows.map((row) => row.month))].sort((a, b) => a - b).map((month) => ({ month, label: ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"][month - 1] }));
  const annualByYear = new Map(annualRunoffRows().map((row) => [row.year, row]));
  const completeYears = new Set(availableYears.filter((year) => !annualByYear.has(year) || annualByYear.get(year).complete));
  const minimumYear = availableYears[0];
  const maximumYear = availableYears.at(-1);
  const requestedRange = state.annualRunoffRange || [minimumYear, maximumYear];
  const selectedRange = [Math.max(minimumYear, requestedRange[0]), Math.min(maximumYear, requestedRange[1])];
  const selectedValueRange = state.annualRunoffValueRange;
  const selectedYears = availableYears.filter((year) => {
    const annual = annualByYear.get(year);
    const value = annual ? annualFieldValue(annual, state.annualRunoffY) : null;
    const valueSelected = !selectedValueRange || (value != null && value >= selectedValueRange[0] && value <= selectedValueRange[1]);
    return year >= selectedRange[0] && year <= selectedRange[1] && completeYears.has(year) && valueSelected;
  });
  const meanForSelected = (month) => {
    const values = monthlyRows.filter((row) => row.month === month && selectedYears.includes(row.year)).map((row) => row.runoff);
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  };
  const selectedBars = months.map((month) => ({ ...month, runoff: meanForSelected(month.month) })).filter((row) => row.runoff != null);
  let selectedYear = safeNumber(state.monthlyRunoffYear);
  if (!availableYears.includes(selectedYear)) selectedYear = maximumYear;
  state.monthlyRunoffYear = selectedYear;
  const yearSelect = $("#monthly-runoff-year");
  if (yearSelect) {
    yearSelect.innerHTML = availableYears.slice().reverse().map((year) => '<option value="' + year + '">WY ' + year + (year === maximumYear ? " (current)" : "") + '</option>').join("");
    yearSelect.value = String(selectedYear);
    if (!yearSelect.dataset.bound) {
      yearSelect.addEventListener("change", (event) => { state.monthlyRunoffYear = safeNumber(event.target.value); renderMonthlyAvailabilityChart(); });
      yearSelect.dataset.bound = "true";
    }
  }
  const selectedYearRows = monthlyRows.filter((row) => row.year === selectedYear);
  const historicalRows = months.map((month) => {
    const artifact = artifactRows.find((row) => row.month === month.month);
    const fallback = monthlyRows.filter((row) => row.month === month.month && completeYears.has(row.year)).map((row) => row.runoff);
    return { ...month, runoff: artifact?.historicalAf ?? (fallback.length ? fallback.reduce((sum, value) => sum + value, 0) / fallback.length : null) };
  }).filter((row) => row.runoff != null);
  const runoffValues = historicalRows.map((row) => row.runoff).concat(selectedBars.map((row) => row.runoff), selectedYearRows.map((row) => row.runoff));
  const runoffMaximum = Math.max(...runoffValues, 1) * 1.08;
  const runoffScale = { minimum: 0, maximum: runoffMaximum, scale: (value) => bottom - value / runoffMaximum * (bottom - top), ticks: niceTicks(0, runoffMaximum, 5) };
  const x = (month) => left + ((month - 0.5) / 12) * (right - left);
  const barWidth = (right - left) / 12 * 0.62;
  const pathFor = (rows) => rows.map((row, index) => (index ? "L" : "M") + x(row.month).toFixed(1) + "," + runoffScale.scale(row.runoff).toFixed(1)).join(" ");
  const bars = showBars ? selectedBars.map((row) => '<rect x="' + (x(row.month) - barWidth / 2).toFixed(1) + '" y="' + runoffScale.scale(row.runoff).toFixed(1) + '" width="' + barWidth.toFixed(1) + '" height="' + Math.max(0, bottom - runoffScale.scale(row.runoff)).toFixed(1) + '" class="monthly-selected-bar"><title>' + escapeHtml(row.label) + ': mean monthly runoff ' + formatNumber(row.runoff, 0) + ' acre-ft across ' + formatNumber(selectedYears.length, 0) + ' selected complete Water Years</title></rect>').join("") : "";
  const yTicks = runoffScale.ticks.map((tick) => '<line x1="' + left + '" y1="' + runoffScale.scale(tick) + '" x2="' + right + '" y2="' + runoffScale.scale(tick) + '" class="chart-grid"/><text x="' + (left - 10) + '" y="' + (runoffScale.scale(tick) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(formatNumber(tick, 0)) + '</text>').join("");
  const monthLabels = months.map((row) => '<text x="' + x(row.month) + '" y="' + (bottom + 22) + '" text-anchor="middle" class="axis-label">' + escapeHtml(row.label) + '</text>').join("");
  const historicalPath = pathFor(historicalRows);
  const selectedYearPath = pathFor(selectedYearRows);
  const legend = (showBars ? '<rect x="' + (left + 8) + '" y="' + (top + 8) + '" width="16" height="10" class="monthly-selected-bar"/><text x="' + (left + 30) + '" y="' + (top + 18) + '" class="legend-label">Mean monthly runoff for selected WY</text>' : '') + '<line x1="' + (left + 310) + '" y1="' + (top + 14) + '" x2="' + (left + 338) + '" y2="' + (top + 14) + '" class="monthly-historical-runoff-line"/><text x="' + (left + 346) + '" y="' + (top + 18) + '" class="legend-label">Historical mean monthly runoff (entire historical data)</text>' + (selectedYearRows.length ? '<line x1="' + (left + 760) + '" y1="' + (top + 14) + '" x2="' + (left + 788) + '" y2="' + (top + 14) + '" class="monthly-selected-runoff-line"/><text x="' + (left + 796) + '" y="' + (top + 18) + '" class="legend-label">Month runoff of WY: ' + selectedYear + '</text>' : '');
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + yTicks + bars + (historicalPath ? '<path d="' + historicalPath + '" class="monthly-historical-runoff-line"/>' : '') + (selectedYearPath ? '<path d="' + selectedYearPath + '" class="monthly-selected-runoff-line"/>' : '') + monthLabels + legend + '<text x="' + ((left + right) / 2) + '" y="' + (height - 30) + '" text-anchor="middle" class="axis-label">Water-Year month (October–September)</text><text x="22" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 22 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">Monthly runoff (acre-ft)</text>';
  const subtitle = "All values are monthly runoff volumes in acre-ft. Bars average the complete Water Years selected in the annual runoff chart; the blue line is the fixed historical mean; the dashed line is the selected Water Year.";
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Monthly water availability profile", subtitle, body, width, height) + '</div>';
  if (note) note.textContent = "Mean monthly runoff for selected WY uses " + formatNumber(selectedYears.length, 0) + " complete Water Years in the annual selection WY " + selectedRange[0] + "–" + selectedRange[1] + ". Historical mean monthly runoff is fixed to the full retained historical data (" + formatNumber(artifactRows[0]?.historicalCount ?? completeYears.size, 0) + " complete Water Years). Month runoff of WY: " + selectedYear + " is shown as a dashed line." + (showBars ? "" : " Mean monthly runoff bars are hidden.");
}

function waterManagementClassColor(value) {
  const key = String(value || "").toLowerCase().replaceAll(" ", "_");
  return { normal: "#176c88", dry: "#c96f2f", very_dry: "#a04d42", extremely_dry: "#7e3f5d", wet: "#3b927e" }[key] || "#6a7772";
}

function renderWaterManagementSummaryChart() {
  const target = $("#water-management-chart");
  const note = $("#water-management-visual-note");
  if (!target) return;
  const summary = state.hydrology?.summary || {};
  const exceedance = (state.hydrology?.annual_runoff_exceedance || []).map((row) => ({
    year: safeNumber(row.water_year),
    runoff: safeNumber(row.annual_runoff_acre_ft),
    probability: safeNumber(row.exceedance_probability_pct),
    yearClass: row.hydrologic_year_class,
  })).filter((row) => row.year != null && row.runoff != null && row.probability != null).sort((a, b) => a.year - b.year);
  const lowFlow = (summary.low_flow_management || []).map((row) => ({
    duration: safeNumber(row.duration_days),
    probability: safeNumber(row.nonexceedance_probability_pct),
    value: safeNumber(row.annual_min_mean_discharge_cfs),
  })).filter((row) => row.duration != null && row.probability != null && row.value != null).sort((a, b) => a.duration - b.duration || a.probability - b.probability);
  const completeCount = safeNumber(summary.complete_water_year_count) ?? safeNumber(exceedance[0]?.n_complete_water_years) ?? exceedance.length;
  if (completeCount == null || completeCount < 10) {
    target.innerHTML = '<p class="chart-empty">Water-management visual not analyzed: fewer than 10 complete Water Years are available.</p>';
    if (note) note.textContent = "This station has " + formatNumber(completeCount, 0) + " complete Water Years; the static water_management_summary visual requires at least 10 complete Water Years.";
    return;
  }
  if (!exceedance.length || !lowFlow.length) {
    target.innerHTML = '<p class="chart-empty">The water-management summary cannot be drawn because one or more required artifacts are unavailable.</p>';
    if (note) note.textContent = "At least one annual runoff exceedance series and one low-flow management series are required.";
    return;
  }
  const width = 1120, height = 640;
  const panels = { annual: { x: 54, y: 38, w: 500, h: 242 }, probability: { x: 566, y: 38, w: 500, h: 242 }, low: { x: 54, y: 332, w: 1012, h: 240 } };
  const panelBody = (panel, title, subtitle) => '<rect x="' + panel.x + '" y="' + panel.y + '" width="' + panel.w + '" height="' + panel.h + '" class="management-panel"/><text x="' + (panel.x + 14) + '" y="' + (panel.y + 22) + '" class="panel-title">' + escapeHtml(title) + '</text><text x="' + (panel.x + 14) + '" y="' + (panel.y + 39) + '" class="legend-label">' + escapeHtml(subtitle) + '</text>';
  const compactTicks = (ticks, maximum = 5) => ticks.length <= maximum ? ticks : [...new Set(Array.from({ length: maximum }, (_, index) => ticks[Math.round(index * (ticks.length - 1) / (maximum - 1))]))];
  let body = "";
  const annualPlot = { left: panels.annual.x + 82, right: panels.annual.x + panels.annual.w - 18, top: panels.annual.y + 58, bottom: panels.annual.y + panels.annual.h - 44 };
  const years = exceedance.map((row) => row.year);
  const annualX = horizontalScale(years, annualPlot.left, annualPlot.right);
  const annualY = scaleFor(exceedance.map((row) => row.runoff), annualPlot.top, annualPlot.bottom, false);
  const annualTicks = annualX.ticks.map((tick) => '<line x1="' + annualX.scale(tick) + '" y1="' + annualPlot.top + '" x2="' + annualX.scale(tick) + '" y2="' + annualPlot.bottom + '" class="chart-grid"/><text x="' + annualX.scale(tick) + '" y="' + (annualPlot.bottom + 20) + '" text-anchor="middle" class="axis-label">' + escapeHtml(formatNumber(tick, 0)) + '</text>').join("");
  const annualYTicks = compactTicks(annualY.ticks).map((tick) => '<line x1="' + annualPlot.left + '" y1="' + annualY.scale(tick) + '" x2="' + annualPlot.right + '" y2="' + annualY.scale(tick) + '" class="chart-grid"/><text x="' + (annualPlot.left - 12) + '" y="' + annualY.scale(tick) + '" dominant-baseline="middle" text-anchor="end" class="axis-label management-y-tick">' + escapeHtml(formatNumber(tick, 0)) + '</text>').join("");
  const annualPath = exceedance.map((row, index) => (index ? "L" : "M") + annualX.scale(row.year).toFixed(1) + "," + annualY.scale(row.runoff).toFixed(1)).join(" ");
  const annualMarks = exceedance.map((row) => '<circle cx="' + annualX.scale(row.year).toFixed(1) + '" cy="' + annualY.scale(row.runoff).toFixed(1) + '" r="3.7" fill="' + waterManagementClassColor(row.yearClass) + '" class="water-management-point"><title>WY ' + row.year + ': ' + formatNumber(row.runoff, 0) + ' acre-ft · ' + escapeHtml(row.yearClass || "unclassified") + '</title></circle>').join("");
  body += panelBody(panels.annual, "Annual runoff by Water Year", "Complete Water Years (n=" + completeCount + ")") + '<rect x="' + annualPlot.left + '" y="' + annualPlot.top + '" width="' + (annualPlot.right - annualPlot.left) + '" height="' + (annualPlot.bottom - annualPlot.top) + '" class="chart-plot"/>' + annualTicks + annualYTicks + '<path d="' + annualPath + '" class="water-management-line"/>' + annualMarks + '<text x="' + ((annualPlot.left + annualPlot.right) / 2) + '" y="' + (panels.annual.y + panels.annual.h - 13) + '" text-anchor="middle" class="axis-label">Water Year</text><text x="' + (panels.annual.x + 17) + '" y="' + ((annualPlot.top + annualPlot.bottom) / 2) + '" transform="rotate(-90 ' + (panels.annual.x + 17) + ' ' + ((annualPlot.top + annualPlot.bottom) / 2) + ')" text-anchor="middle" class="axis-label">Annual runoff (acre-ft)</text>';
  const probabilityPlot = { left: panels.probability.x + 82, right: panels.probability.x + panels.probability.w - 18, top: panels.probability.y + 58, bottom: panels.probability.y + panels.probability.h - 44 };
  const probabilityX = { scale: (value) => probabilityPlot.left + value / 100 * (probabilityPlot.right - probabilityPlot.left), ticks: [0, 25, 50, 75, 100] };
  const probabilityY = scaleFor(exceedance.map((row) => row.runoff).concat((summary.water_management_baselines || []).map((row) => safeNumber(row.annual_runoff_acre_ft)).filter((value) => value != null)), probabilityPlot.top, probabilityPlot.bottom, false);
  const probabilityTicks = probabilityX.ticks.map((tick) => '<line x1="' + probabilityX.scale(tick) + '" y1="' + probabilityPlot.top + '" x2="' + probabilityX.scale(tick) + '" y2="' + probabilityPlot.bottom + '" class="chart-grid"/><text x="' + probabilityX.scale(tick) + '" y="' + (probabilityPlot.bottom + 20) + '" text-anchor="middle" class="axis-label">' + tick + '%</text>').join("");
  const probabilityYTicks = compactTicks(probabilityY.ticks).map((tick) => '<line x1="' + probabilityPlot.left + '" y1="' + probabilityY.scale(tick) + '" x2="' + probabilityPlot.right + '" y2="' + probabilityY.scale(tick) + '" class="chart-grid"/><text x="' + (probabilityPlot.left - 12) + '" y="' + probabilityY.scale(tick) + '" dominant-baseline="middle" text-anchor="end" class="axis-label management-y-tick">' + escapeHtml(formatNumber(tick, 0)) + '</text>').join("");
  const probabilityPath = exceedance.slice().sort((a, b) => a.probability - b.probability).map((row, index) => (index ? "L" : "M") + probabilityX.scale(row.probability).toFixed(1) + "," + probabilityY.scale(row.runoff).toFixed(1)).join(" ");
  const probabilityMarks = exceedance.map((row) => '<circle cx="' + probabilityX.scale(row.probability).toFixed(1) + '" cy="' + probabilityY.scale(row.runoff).toFixed(1) + '" r="3.2" fill="' + waterManagementClassColor(row.yearClass) + '" class="water-management-point"><title>WY ' + row.year + ' · exceedance ' + formatNumber(row.probability, 1) + '% · ' + formatNumber(row.runoff, 0) + ' acre-ft</title></circle>').join("");
  const baselineMarks = (summary.water_management_baselines || []).map((row) => { const probability = safeNumber(row.exceedance_probability_pct), runoff = safeNumber(row.annual_runoff_acre_ft); return probability == null || runoff == null ? "" : '<line x1="' + (probabilityX.scale(probability) - 5) + '" y1="' + (probabilityY.scale(runoff) - 5) + '" x2="' + (probabilityX.scale(probability) + 5) + '" y2="' + (probabilityY.scale(runoff) + 5) + '" class="management-baseline"/><line x1="' + (probabilityX.scale(probability) - 5) + '" y1="' + (probabilityY.scale(runoff) + 5) + '" x2="' + (probabilityX.scale(probability) + 5) + '" y2="' + (probabilityY.scale(runoff) - 5) + '" class="management-baseline"/><text x="' + (probabilityX.scale(probability) + 7) + '" y="' + (probabilityY.scale(runoff) - 6) + '" class="legend-label">P' + probability + '</text>'; }).join("");
  body += panelBody(panels.probability, "Annual runoff vs exceedance probability", "Empirical rank/(n+1); complete Water Years only") + '<rect x="' + probabilityPlot.left + '" y="' + probabilityPlot.top + '" width="' + (probabilityPlot.right - probabilityPlot.left) + '" height="' + (probabilityPlot.bottom - probabilityPlot.top) + '" class="chart-plot"/>' + probabilityTicks + probabilityYTicks + '<path d="' + probabilityPath + '" class="water-management-line"/>' + probabilityMarks + baselineMarks + '<text x="' + ((probabilityPlot.left + probabilityPlot.right) / 2) + '" y="' + (panels.probability.y + panels.probability.h - 13) + '" text-anchor="middle" class="axis-label">Exceedance probability (%)</text><text x="' + (panels.probability.x + 17) + '" y="' + ((probabilityPlot.top + probabilityPlot.bottom) / 2) + '" transform="rotate(-90 ' + (panels.probability.x + 17) + ' ' + ((probabilityPlot.top + probabilityPlot.bottom) / 2) + ')" text-anchor="middle" class="axis-label">Annual runoff (acre-ft)</text>';
  const lowPlot = { left: panels.low.x + 88, right: panels.low.x + panels.low.w - 18, top: panels.low.y + 58, bottom: panels.low.y + panels.low.h - 45 };
  const lowY = scaleFor(lowFlow.map((row) => row.value).concat([0]), lowPlot.top, lowPlot.bottom, false);
  const lowX = { scale: (value) => lowPlot.left + value / 55 * (lowPlot.right - lowPlot.left), ticks: [0, 5, 10, 25, 50] };
  const lowTicks = lowX.ticks.map((tick) => '<line x1="' + lowX.scale(tick) + '" y1="' + lowPlot.top + '" x2="' + lowX.scale(tick) + '" y2="' + lowPlot.bottom + '" class="chart-grid"/><text x="' + lowX.scale(tick) + '" y="' + (lowPlot.bottom + 20) + '" text-anchor="middle" class="axis-label">' + tick + '%</text>').join("");
  const lowYTicks = compactTicks(lowY.ticks).map((tick) => '<line x1="' + lowPlot.left + '" y1="' + lowY.scale(tick) + '" x2="' + lowPlot.right + '" y2="' + lowY.scale(tick) + '" class="chart-grid"/><text x="' + (lowPlot.left - 12) + '" y="' + lowY.scale(tick) + '" dominant-baseline="middle" text-anchor="end" class="axis-label management-y-tick">' + escapeHtml(formatNumber(tick, 0)) + '</text>').join("");
  const durationColors = { 7: COLORS.discharge, 14: COLORS.stage, 30: "#3b927e" };
  const durations = [...new Set(lowFlow.map((row) => row.duration))];
  const lowLines = durations.map((duration) => { const rows = lowFlow.filter((row) => row.duration === duration); const path = rows.map((row, index) => (index ? "L" : "M") + lowX.scale(row.probability).toFixed(1) + "," + lowY.scale(row.value).toFixed(1)).join(" "); const color = durationColors[duration] || COLORS.baseflow; return '<path d="' + path + '" class="water-management-line" stroke="' + color + '"/><g fill="' + color + '">' + rows.map((row) => '<circle cx="' + lowX.scale(row.probability).toFixed(1) + '" cy="' + lowY.scale(row.value).toFixed(1) + '" r="3"><title>' + duration + '-day minimum mean discharge at ' + row.probability + '% non-exceedance: ' + formatNumber(row.value, 1) + ' ft³/s</title></circle>').join("") + '</g>'; }).join("");
  const lowLegend = durations.map((duration, index) => { const color = durationColors[duration] || COLORS.baseflow; const offset = index * 100; return '<line x1="' + (lowPlot.left + offset) + '" y1="' + (panels.low.y + 53) + '" x2="' + (lowPlot.left + offset + 25) + '" y2="' + (panels.low.y + 53) + '" stroke="' + color + '" class="water-management-line"/><text x="' + (lowPlot.left + offset + 33) + '" y="' + (panels.low.y + 57) + '" class="legend-label">' + duration + '-day</text>'; }).join("");
  body += panelBody(panels.low, "Annual minimum k-day mean discharge", "Empirical low-flow screens; descriptive, not fitted kQ10 standards") + '<rect x="' + lowPlot.left + '" y="' + lowPlot.top + '" width="' + (lowPlot.right - lowPlot.left) + '" height="' + (lowPlot.bottom - lowPlot.top) + '" class="chart-plot"/>' + lowTicks + lowYTicks + lowLegend + lowLines + '<text x="' + ((lowPlot.left + lowPlot.right) / 2) + '" y="' + (panels.low.y + panels.low.h - 13) + '" text-anchor="middle" class="axis-label">Non-exceedance probability (%)</text><text x="' + (panels.low.x + 19) + '" y="' + ((lowPlot.top + lowPlot.bottom) / 2) + '" transform="rotate(-90 ' + (panels.low.x + 19) + ' ' + ((lowPlot.top + lowPlot.bottom) / 2) + ')" text-anchor="middle" class="axis-label">Annual minimum k-day mean Q (ft³/s)</text>';
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Water-management summary", "Complete Water Years (n=" + completeCount + "). Annual runoff and low-flow empirical planning views from the station hydrology package.", body, width, height) + '</div>';
  if (note) note.textContent = "n=" + formatNumber(completeCount, 0) + " complete Water Years. Annual runoff panels use annual_runoff_exceedance.csv; the low-flow panel uses low_flow_management.csv. These are empirical planning views, not a fitted recurrence standard.";
}

function renderWaterAvailability() {
  const summary = state.hydrology?.summary || {};
  const baselines = summary.water_management_baselines || [];
  const lowFlow = summary.low_flow_management || [];
  const exceedance = state.hydrology?.annual_runoff_exceedance || [];
  const p50 = baselines.find((row) => Number(row.exceedance_probability_pct) === 50);
  $("#availability-summary").innerHTML = [
    ["Complete Water Years", formatNumber(summary.complete_water_year_count, 0)],
    ["Annual runoff records", formatNumber(summary.annual_runoff_exceedance_count, 0)],
    ["P50 annual runoff", formatNumber(p50?.annual_runoff_acre_ft, 0) + " acre-ft"],
    ["Mean annual discharge", formatNumber(summary.interannual_mean_discharge_cfs, 1) + " ft³/s"],
  ].map((metric) => '<div class="summary-metric"><span>' + escapeHtml(metric[0]) + '</span><strong>' + escapeHtml(metric[1]) + '</strong></div>').join("");
  $("#availability-method-note").innerHTML = "<strong>Methods and interpretation</strong>" + escapeHtml(summary.water_management_interpretation || "Annual runoff baselines are empirical statistics from complete Water Years.") + " Annual exceedance uses descending rank/(n+1). Low-flow values are descriptive screens and should not be read as regulatory thresholds.";
  $("#water-management-rows").innerHTML = baselines.map((row) => '<tr><td>P' + escapeHtml(row.exceedance_probability_pct) + '</td><td>' + escapeHtml(String(row.year_type || "").replaceAll("_", " ")) + '</td><td>' + escapeHtml(formatNumber(row.annual_runoff_acre_ft, 0)) + ' acre-ft</td><td>' + escapeHtml(formatNumber(row.annual_mean_discharge_cfs, 1)) + ' ft³/s</td></tr>').join("") || '<tr><td colspan="4" class="muted">No water-management baselines are available.</td></tr>';
  $("#low-flow-rows").innerHTML = lowFlow.map((row) => '<tr><td>' + escapeHtml(row.duration_days) + ' day</td><td>' + escapeHtml(row.nonexceedance_probability_pct) + '%</td><td>' + escapeHtml(formatNumber(row.annual_min_mean_discharge_cfs, 1)) + ' ft³/s</td></tr>').join("") || '<tr><td colspan="3" class="muted">No low-flow management screen is available.</td></tr>';
  $("#annual-exceedance-rows").innerHTML = exceedance.map((row) => '<tr><td>WY ' + escapeHtml(row.water_year) + '</td><td>' + escapeHtml(formatNumber(row.annual_runoff_acre_ft, 0)) + ' acre-ft</td><td>' + escapeHtml(formatNumber(row.annual_mean_discharge_cfs, 1)) + ' ft³/s</td><td>' + escapeHtml(formatNumber(row.exceedance_probability_pct, 1)) + '%</td><td>' + escapeHtml(row.hydrologic_year_class) + '</td></tr>').join("") || '<tr><td colspan="5" class="muted">No annual runoff exceedance artifact is available.</td></tr>';
  renderMonthlyAvailabilityChart();
  renderWaterManagementSummaryChart();
}

function renderBaseflowAnnualChart(rows) {
  const target = $("#baseflow-annual-chart");
  const usable = rows.map((row) => ({ year: Number(row.water_year), value: safeNumber(row.mean_baseflow_cfs) })).filter((row) => Number.isFinite(row.year) && row.value != null && row.value >= 0).sort((a, b) => a.year - b.year);
  if (!usable.length) { target.innerHTML = '<p class="chart-empty">No annual baseflow series is available.</p>'; return; }
  const width = 720, height = 150, left = 54, right = 704, top = 22, bottom = 112;
  const scale = scaleFor(usable.map((row) => row.value), top, bottom, false);
  const x = (index) => left + (usable.length === 1 ? (right - left) / 2 : index / (usable.length - 1) * (right - left));
  const barWidth = Math.max(2, Math.min(12, (right - left) / usable.length * .7));
  const selected = (row) => (!state.baseflowAnnualRange || (row.year >= state.baseflowAnnualRange[0] && row.year <= state.baseflowAnnualRange[1])) && (!state.baseflowAnnualValueRange || (row.value >= state.baseflowAnnualValueRange[0] && row.value <= state.baseflowAnnualValueRange[1]));
  const bars = usable.map((row, index) => '<rect x="' + (x(index) - barWidth / 2).toFixed(1) + '" y="' + scale.scale(row.value).toFixed(1) + '" width="' + barWidth.toFixed(1) + '" height="' + Math.max(0, bottom - scale.scale(row.value)).toFixed(1) + '" class="baseflow-bar' + (selected(row) ? '' : ' baseflow-bar-muted') + '"><title>WY ' + row.year + ': ' + formatNumber(row.value, 1) + ' ft³/s mean baseflow</title></rect>').join("");
  const labels = usable.filter((_row, index) => index % Math.max(1, Math.ceil(usable.length / 7)) === 0 || index === usable.length - 1).map((row) => {
    const index = usable.findIndex((candidate) => candidate.year === row.year);
    return '<text x="' + x(index).toFixed(1) + '" y="' + (bottom + 20) + '" text-anchor="middle" class="axis-label">' + row.year + '</text>';
  }).join("");
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + scale.ticks.map((tick) => '<line x1="' + left + '" y1="' + scale.scale(tick) + '" x2="' + right + '" y2="' + scale.scale(tick) + '" class="chart-grid"/><text x="' + (left - 8) + '" y="' + (scale.scale(tick) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(formatNumber(tick, tick < 10 ? 1 : 0)) + '</text>').join("") + bars + labels + '<text x="' + ((left + right) / 2) + '" y="' + (height - 3) + '" text-anchor="middle" class="axis-label">Water Year</text><text x="18" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 18 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">Mean baseflow (ft³/s)</text><rect id="baseflow-brush-preview" x="' + left + '" y="' + top + '" width="0" height="0" class="annual-brush-preview" display="none"/><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="plot-hit"/>';
  const rangeText = state.baseflowAnnualRange ? " Selected WY " + state.baseflowAnnualRange[0] + "–" + state.baseflowAnnualRange[1] + "." : "";
  target.innerHTML = '<div class="chart-shell">' + svgFrame("Annual mean baseflow", "Mean retained Lyne–Hollick baseflow by Water Year. Drag a rectangle to select Water Years and baseflow values; double-click resets." + rangeText, body, width, height) + "</div>";
  const svg = target.querySelector("svg");
  const hit = svg.querySelector(".plot-hit");
  const preview = svg.querySelector("#baseflow-brush-preview");
  const pixelPosition = (event) => { const rect = svg.getBoundingClientRect(); return { x: (event.clientX - rect.left) * width / rect.width, y: (event.clientY - rect.top) * height / rect.height }; };
  const clampX = (value) => Math.max(left, Math.min(right, value));
  const clampY = (value) => Math.max(top, Math.min(bottom, value));
  const yearAt = (pixel) => { const fraction = (clampX(pixel) - left) / (right - left || 1); return usable[Math.round(fraction * (usable.length - 1))].year; };
  const valueAt = (pixel) => scale.minimum + (bottom - clampY(pixel)) / (bottom - top || 1) * (scale.maximum - scale.minimum);
  let dragStart = null;
  hit.addEventListener("pointerdown", (event) => { dragStart = pixelPosition(event); hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    const end = pixelPosition(event);
    const x1 = clampX(dragStart.x), x2 = clampX(end.x), y1 = clampY(dragStart.y), y2 = clampY(end.y);
    preview.setAttribute("x", Math.min(x1, x2)); preview.setAttribute("y", Math.min(y1, y2)); preview.setAttribute("width", Math.abs(x2 - x1)); preview.setAttribute("height", Math.abs(y2 - y1)); preview.setAttribute("display", "block");
  });
  hit.addEventListener("pointerup", (event) => {
    if (!dragStart) return;
    const end = pixelPosition(event);
    const verticalDistance = Math.abs(end.y - dragStart.y);
    if (Math.abs(end.x - dragStart.x) > 12 || verticalDistance > 12) {
      state.baseflowAnnualRange = [Math.min(yearAt(dragStart.x), yearAt(end.x)), Math.max(yearAt(dragStart.x), yearAt(end.x))];
      state.baseflowAnnualValueRange = verticalDistance > 12 ? [Math.min(valueAt(dragStart.y), valueAt(end.y)), Math.max(valueAt(dragStart.y), valueAt(end.y))] : null;
      renderBaseflowAnnualChart(rows);
    }
    preview.setAttribute("display", "none"); dragStart = null;
  });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.baseflowAnnualRange = null; state.baseflowAnnualValueRange = null; renderBaseflowAnnualChart(rows); });
}
function renderBaseflow() {
  const summary = state.hydrology?.baseflow_summary || {};
  $("#baseflow-description").textContent = text(summary.method) + ". BFI is a screening indicator, not a direct groundwater measurement.";
  $("#baseflow-summary").innerHTML = [
    ["Full-period BFI", formatNumber(summary.baseflow_index, 3)],
    ["Mean baseflow", formatNumber(summary.mean_baseflow_cfs, 1) + " ft³/s"],
    ["Valid days", formatNumber(summary.valid_days, 0)],
    ["Filter", "alpha " + text(summary.alpha)],
  ].map((metric) => "<div class=\"summary-metric\"><span>" + escapeHtml(metric[0]) + "</span><strong>" + escapeHtml(metric[1]) + "</strong></div>").join("");
  const rows = (state.hydrology?.baseflow_annual || []).slice().sort((a, b) => Number(b.water_year) - Number(a.water_year));
  renderBaseflowAnnualChart(rows);
  $("#baseflow-rows").innerHTML = rows.map((row) => "<tr><td>WY " + escapeHtml(row.water_year) + "</td><td>" + escapeHtml(formatNumber(row.mean_baseflow_cfs, 1)) + " ft³/s</td><td>" + escapeHtml(formatNumber(row.baseflow_index, 3)) + "</td><td>" + escapeHtml(formatNumber(row.valid_days, 0)) + "</td></tr>").join("") || '<tr><td colspan="4" class="muted">No baseflow annual table is available.</td></tr>';
}
function renderFfa() {
  const rows = state.hydrology?.flood_frequency || [];
  const summary = state.hydrology?.summary || {};
  const fit = rows.filter((row) => String(row.method).startsWith("b17c") && safeNumber(row.return_period_years) != null && safeNumber(row.estimate_discharge_cfs) != null).sort((a, b) => Number(a.return_period_years) - Number(b.return_period_years));
  const empiricalAll = rows.filter((row) => row.method === "empirical_ams_weibull_plotting_position" && safeNumber(row.return_period_years) != null && safeNumber(row.estimate_discharge_cfs) != null).sort((a, b) => Number(a.return_period_years) - Number(b.return_period_years));
  const fitMaximum = Math.max(...fit.map((row) => safeNumber(row.estimate_discharge_cfs)).filter((value) => value != null), 0);
  // The source package flags WY 1927 as historic, while the pre-record WY
  // 1912 25,000 ft³/s point is still tagged systematic. Keep both extreme
  // historic-context points out of the default systematic display, but let
  // reviewers reveal them explicitly with the checkbox.
  const historicEmpirical = empiricalAll.filter((row) => Number(row.water_year) < 1935 && safeNumber(row.estimate_discharge_cfs) > fitMaximum * 1.5);
  const empirical = empiricalAll.filter((row) => !historicEmpirical.includes(row));
  const historic = [...rows.filter((row) => row.method === "excluded_historical_peak" && safeNumber(row.estimate_discharge_cfs) != null), ...historicEmpirical];
  if (!fit.length && !empirical.length) { $("#ffa-chart").innerHTML = '<p class="chart-empty">No FFA curve output is available.</p>'; return; }
  const width = 1120, height = 470, left = 90, right = 1065, top = 42, bottom = 360;
  const periods = [...fit, ...empirical].map((row) => safeNumber(row.return_period_years)).filter((value) => value > 1);
  const maxPeriod = Math.max(100, ...periods);
  const periodDomain = state.ffaPeriodRange || [1, maxPeriod];
  const valueDomain = state.ffaValueRange;
  const rangeMode = $("#ffa-y-range")?.value || "auto";
  const showFit = rangeMode !== "observed";
  const showHistoric = $("#ffa-show-historical")?.checked === true;
  const showConfidenceLimits = rangeMode === "full";
  const rangeRows = rangeMode === "observed" ? empirical : [...fit, ...empirical, ...(showHistoric ? historic : [])];
  const rangeValues = rangeRows.flatMap((row) => showConfidenceLimits
    ? [row.estimate_discharge_cfs, row.lower_confidence_cfs, row.upper_confidence_cfs]
    : [row.estimate_discharge_cfs]).map(safeNumber).filter((value) => value > 0);
  const fallbackValues = [...fit, ...empirical, ...(showHistoric ? historic : [])].map((row) => safeNumber(row.estimate_discharge_cfs)).filter((value) => value > 0);
  const scale = scaleFor(valueDomain || (rangeValues.length ? rangeValues : fallbackValues), top, bottom, $("#ffa-y-scale")?.value === "log");
  const inPeriod = (row) => { const period = safeNumber(row.return_period_years); return period != null && period >= periodDomain[0] && period <= periodDomain[1]; };
  const plottedFit = fit.filter(inPeriod);
  const plottedEmpirical = empirical.filter(inPeriod);
  const plottedHistoric = historic.filter(inPeriod);
  const sx = (period) => left + (Math.log10(period) - Math.log10(periodDomain[0])) / (Math.log10(periodDomain[1]) - Math.log10(periodDomain[0]) || 1) * (right - left);
  const line = showFit ? plottedFit.map((row, index) => (index ? "L" : "M") + sx(Number(row.return_period_years)).toFixed(1) + "," + scale.scale(Number(row.estimate_discharge_cfs)).toFixed(1)).join(" ") : "";
  const lower = plottedFit.filter((row) => safeNumber(row.lower_confidence_cfs) != null && safeNumber(row.upper_confidence_cfs) != null);
  const band = showConfidenceLimits && lower.length ? lower.map((row, index) => (index ? "L" : "M") + sx(Number(row.return_period_years)).toFixed(1) + "," + scale.scale(Number(row.upper_confidence_cfs)).toFixed(1)).join(" ") + " " + lower.slice().reverse().map((row) => "L" + sx(Number(row.return_period_years)).toFixed(1) + "," + scale.scale(Number(row.lower_confidence_cfs)).toFixed(1)).join(" ") + " Z" : "";
  const xPeriods = [...new Set([periodDomain[0], 2, 5, 10, 25, 50, 100, 200, 500, periodDomain[1]])].filter((period) => period >= periodDomain[0] && period <= periodDomain[1]).sort((a, b) => a - b);
  const yAxisLabel = "Annual peak discharge (ft³/s" + ($("#ffa-y-scale")?.value === "log" ? "; log scale)" : ")");
  const legend = (showFit ? '<line x1="0" y1="0" x2="23" y2="0" class="ffa-line"/><text x="30" y="4" class="legend-label">B17C-style fitted screen</text>' : "") + '<circle cx="' + (showFit ? 180 : 0) + '" cy="0" r="4" class="ffa-observed"/><text x="' + (showFit ? 190 : 10) + '" y="4" class="legend-label">Empirical annual maxima</text>' + (showHistoric ? '<circle cx="380" cy="0" r="4" class="ffa-historic"/><text x="390" y="4" class="legend-label">Excluded historic</text>' : "") + (showConfidenceLimits ? '<rect x="540" y="-5" width="23" height="10" class="reference-band"/><text x="570" y="4" class="legend-label">Confidence limits</text>' : "");
  const body = '<rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + scale.ticks.map((tick) => '<line x1="' + left + '" y1="' + scale.scale(tick) + '" x2="' + right + '" y2="' + scale.scale(tick) + '" class="chart-grid"/><text x="' + (left - 9) + '" y="' + (scale.scale(tick) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(formatNumber(tick, tick < 10 ? 2 : 0)) + "</text>").join("") + xPeriods.map((period) => '<line x1="' + sx(period) + '" y1="' + top + '" x2="' + sx(period) + '" y2="' + bottom + '" class="chart-grid"/><text x="' + sx(period) + '" y="' + (bottom + 22) + '" text-anchor="middle" class="axis-label">' + formatNumber(period, period < 10 ? 1 : 0) + " yr</text>").join("") + (band ? '<path d="' + band + '" class="reference-band"/>' : "") + (line ? '<path d="' + line + '" class="ffa-line"/>' : "") + plottedEmpirical.map((row) => '<circle cx="' + sx(Number(row.return_period_years)) + '" cy="' + scale.scale(Number(row.estimate_discharge_cfs)) + '" r="3" class="ffa-observed" data-water-year="' + escapeHtml(row.water_year) + '"><title>WY ' + escapeHtml(row.water_year) + ": " + escapeHtml(formatNumber(row.estimate_discharge_cfs, 0)) + " ft³/s</title></circle>").join("") + (showHistoric ? plottedHistoric.map((row) => { const period = safeNumber(row.return_period_years); const x = period > 1 ? sx(period) : left + (right - left) * .02; return '<circle cx="' + x + '" cy="' + scale.scale(Number(row.estimate_discharge_cfs)) + '" r="4" class="ffa-historic"><title>Historic/excluded WY ' + escapeHtml(row.water_year) + ": " + escapeHtml(formatNumber(row.estimate_discharge_cfs, 0)) + " ft³/s</title></circle>"; }).join("") : "") + '<text x="' + ((left + right) / 2) + '" y="' + (height - 27) + '" text-anchor="middle" class="axis-label">Return period (years; log scale)</text><text x="23" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 23 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">' + yAxisLabel + '</text><g transform="translate(' + left + "," + (height - 7) + ')">' + legend + '</g><rect id="ffa-brush-preview" x="' + left + '" y="' + top + '" width="0" height="0" class="annual-brush-preview" display="none"/><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="plot-hit"/>';
  const rangeText = state.ffaPeriodRange ? " Selected return period " + formatNumber(periodDomain[0], 1) + "–" + formatNumber(periodDomain[1], 1) + " years." + (valueDomain ? " Selected discharge " + formatNumber(valueDomain[0], 0) + "–" + formatNumber(valueDomain[1], 0) + " ft³/s." : "") : "";
  $("#ffa-chart").innerHTML = '<div class="chart-shell">' + svgFrame("Flood Frequency Analysis", "Existing station-tool annual-peak FFA output; drag a rectangle to select return-period and discharge ranges. Double-click resets." + rangeText, body, width, height) + "</div>";
  $("#ffa-rows").innerHTML = fit.map((row) => '<tr data-ffa-period="' + escapeHtml(row.return_period_years) + '"><td>' + escapeHtml(formatNumber(row.return_period_years, 0)) + " yr</td><td>" + escapeHtml(safeNumber(row.exceedance_probability) != null ? (Number(row.exceedance_probability) * 100).toFixed(2) + "%" : "—") + "</td><td>" + escapeHtml(formatNumber(row.estimate_discharge_cfs, 0)) + "</td><td>" + escapeHtml(formatNumber(row.lower_confidence_cfs, 0)) + "</td><td>" + escapeHtml(formatNumber(row.upper_confidence_cfs, 0)) + "</td></tr>").join("") || '<tr><td colspan="5" class="muted">No fitted FFA table is available.</td></tr>';
  $("#ffa-summary").innerHTML = "<h3>FFA context</h3><div class=\"fact-list\">" + [
    ["Method", summary.flood_frequency_method],
    ["Source", summary.flood_frequency_source],
    ["Complete Water Years", summary.flood_frequency_complete_years],
    ["Systematic years", summary.flood_frequency_systematic_years],
    ["Excluded historic peaks", summary.flood_frequency_excluded_historic_peaks],
    ["Confidence level", summary.flood_frequency_confidence_level ? Number(summary.flood_frequency_confidence_level) * 100 + "%" : "—"],
  ].map((fact) => "<div class=\"fact-row\"><span>" + escapeHtml(fact[0]) + "</span><strong>" + escapeHtml(fact[1]) + "</strong></div>").join("") + "</div>";
  if (summary.flood_frequency_warning) { $("#ffa-warning").hidden = false; $("#ffa-warning").innerHTML = "<strong>Interpretation note</strong>" + escapeHtml(summary.flood_frequency_warning); }
  setPill("#ffa-status", fit.length ? "FFA ready" : "Empirical peaks only", fit.length ? "" : "warn");
  const svg = $("#ffa-chart svg");
  const hit = svg.querySelector(".plot-hit");
  const preview = svg.querySelector("#ffa-brush-preview");
  const pixelPosition = (event) => { const rect = svg.getBoundingClientRect(); return { x: (event.clientX - rect.left) * width / rect.width, y: (event.clientY - rect.top) * height / rect.height }; };
  const clampX = (value) => Math.max(left, Math.min(right, value));
  const clampY = (value) => Math.max(top, Math.min(bottom, value));
  const periodAt = (pixel) => Math.pow(10, Math.log10(periodDomain[0]) + (clampX(pixel) - left) / (right - left || 1) * (Math.log10(periodDomain[1]) - Math.log10(periodDomain[0])));
  const valueAt = (pixel) => { const fraction = (bottom - clampY(pixel)) / (bottom - top || 1); if (scale.logarithmic) return Math.pow(10, Math.log10(scale.minimum) + fraction * (Math.log10(scale.maximum) - Math.log10(scale.minimum))); return scale.minimum + fraction * (scale.maximum - scale.minimum); };
  let dragStart = null;
  hit.addEventListener("pointerdown", (event) => { dragStart = pixelPosition(event); hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => { if (!dragStart) return; const end = pixelPosition(event); const x1 = clampX(dragStart.x), x2 = clampX(end.x), y1 = clampY(dragStart.y), y2 = clampY(end.y); preview.setAttribute("x", Math.min(x1, x2)); preview.setAttribute("y", Math.min(y1, y2)); preview.setAttribute("width", Math.abs(x2 - x1)); preview.setAttribute("height", Math.abs(y2 - y1)); preview.setAttribute("display", "block"); });
  hit.addEventListener("pointerup", (event) => { if (!dragStart) return; const end = pixelPosition(event); if (Math.abs(end.x - dragStart.x) > 12 || Math.abs(end.y - dragStart.y) > 12) { state.ffaPeriodRange = [Math.min(periodAt(dragStart.x), periodAt(end.x)), Math.max(periodAt(dragStart.x), periodAt(end.x))]; state.ffaValueRange = [Math.min(valueAt(dragStart.y), valueAt(end.y)), Math.max(valueAt(dragStart.y), valueAt(end.y))]; renderFfa(); } preview.setAttribute("display", "none"); dragStart = null; });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.ffaPeriodRange = null; state.ffaValueRange = null; renderFfa(); });
}
function renderQuality() {
  const rows = state.analysis.quality_event_summary || [];
  $("#quality-event-rows").innerHTML = rows.map((row) => "<tr><td><strong>" + escapeHtml(row.rule_id) + "</strong></td><td>" + escapeHtml(row.quality_state) + "</td><td>" + escapeHtml(row.severity) + "</td><td>" + escapeHtml(row.review_state) + "</td><td>" + escapeHtml(formatNumber(row.event_count, 0)) + "</td><td>" + escapeHtml(row.last_detected_at_utc) + "</td></tr>").join("") || '<tr><td colspan="6" class="muted">No quality-event summary is available in this release.</td></tr>';
}
function renderProvenance() {
  const artifact = state.hydrology?.artifact_names || {};
  const manifest = state.hydrology?.discovery_manifest || {};
  $("#provenance-summary").innerHTML = [
    ["Station directory", artifact.station_root],
    ["Hydrology artifacts", artifact.hydrology],
    ["Watershed", artifact.watershed],
    ["Discovery retrieved", manifest.retrieved_at],
    ["Local snapshot", state.analysis?.station?.local_snapshot_id],
  ].map((item) => "<div class=\"provenance-item\"><span>" + escapeHtml(item[0]) + "</span><strong>" + escapeHtml(item[1]) + "</strong></div>").join("");
  $("#reproducibility-note").textContent = "The browser reads this named local station snapshot; it does not download a prebuilt basin release. Original provider values and quality codes are preserved; derived views are labeled with their period, sample size, and method.";
}
function bindDownloads() {
  $("#download-coverage")?.addEventListener("click", () => {
    const rows = coverageRows().visible.map((series) => ({
      variable: series.variable || series.variable_name || series.source_variable,
      frequency: frequencyLabel(series.frequency || series.data_type),
      provider_start: series.declared_start || series.observed_date_start,
      provider_end: series.declared_end || series.observed_date_end,
      local_status: series.local_status || "catalog_only",
      local_records: series.local_record_count || series.numeric_observation_count,
    }));
    downloadCsv("station_data_inventory.csv", rows);
  });
  $("#download-hydrograph")?.addEventListener("click", () => {
    const data = state.hydrographData;
    if (!data) return;
    const byDate = new Map();
    for (const point of data.discharge.points) byDate.set(point.date, { date: point.date, discharge_cfs: point.value });
    for (const point of data.stage.points) byDate.set(point.date, { ...(byDate.get(point.date) || { date: point.date }), gage_height_ft: point.value });
    for (const point of data.baseflow?.points || []) byDate.set(point.date, { ...(byDate.get(point.date) || { date: point.date }), baseflow_cfs: point.value });
    downloadCsv("hydrograph_daily.csv", [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date)));
  });
  $("#download-fdc")?.addEventListener("click", () => downloadCsv("flow_duration_curve.csv", state.fdc));
  $("#download-annual-runoff")?.addEventListener("click", () => {
    const rows = annualRunoffRows().map((row) => ({
      water_year: row.year,
      is_complete: row.complete,
      annual_runoff_acre_ft: annualFieldValue(row, "total_runoff_acre_ft"),
      mean_discharge_cfs: annualFieldValue(row, "mean_discharge_cfs"),
      max_daily_discharge_cfs: annualFieldValue(row, "max_daily_discharge_cfs"),
      min_daily_discharge_cfs: annualFieldValue(row, "min_daily_discharge_cfs"),
      valid_days: annualFieldValue(row, "valid_days"),
      hydrologic_year_class: row.hydrologic_year_class || "",
    }));
    downloadCsv("annual_runoff_water_availability.csv", rows);
  });
  $("#download-stage-discharge")?.addEventListener("click", () => downloadCsv("stage_discharge_pairs.csv", state.stageDischargePoints));
  $("#download-ffa")?.addEventListener("click", () => downloadCsv("flood_frequency_analysis.csv", state.hydrology?.flood_frequency || []));
}
function geometryBounds(geometry, bounds = new LngLatBounds()) {
  if (!geometry) return bounds;
  if (geometry.type === "FeatureCollection") {
    (geometry.features || []).forEach((feature) => geometryBounds(feature, bounds));
    return bounds;
  }
  if (geometry.type === "Feature") return geometryBounds(geometry.geometry, bounds);
  const visit = (value) => {
    if (Array.isArray(value) && value.length >= 2 && typeof value[0] === "number") bounds.extend([value[0], value[1]]);
    else if (Array.isArray(value)) value.forEach(visit);
  };
  visit(geometry.coordinates);
  return bounds;
}
const BASIN_GEOGRAPHY = {
  "1408": {
    name: "San Juan",
    description: "The San Juan region spans the high San Juan Mountains and transitions downstream toward high-desert plateaus and canyon country in the Four Corners area.",
  },
};
const STATE_NAMES = { AZ: "Arizona", CO: "Colorado", NM: "New Mexico", UT: "Utah" };
function basinContext(station) {
  const huc = String(station?.huc || state.hydrology?.station_metadata?.huc_code || "").replace(/\D/g, "");
  const properties = state.basinBoundary?.features?.find((feature) => feature?.properties)?.properties || {};
  const huc4 = String(properties.huc4 || huc.slice(0, 4) || "");
  const profile = BASIN_GEOGRAPHY[huc4] || {};
  return {
    huc,
    huc4,
    name: properties.name || profile.name || "regional watershed",
    states: properties.states || "",
    description: profile.description || "The release provides mapped watershed and hydrography context; a formal physiographic classification is not included in this station package.",
  };
}
function localSetting(elevation, area) {
  const elevationSetting = elevation == null
    ? "an elevation setting that requires additional terrain data to classify"
    : elevation >= 6500
      ? "a high-elevation mountain or upland setting"
      : elevation >= 4500
        ? "an intermediate-elevation upland or intermontane setting"
        : "a lower-elevation valley, plateau, or canyon setting";
  const basinSetting = area == null
    ? "a contributing area whose local scale is not reported in the available metadata"
    : area < 100
      ? "a small local or headwater catchment"
      : area < 1000
        ? "an intermediate tributary catchment"
        : "a larger river basin";
  return elevationSetting + " draining " + basinSetting + ".";
}
function stationGeographicDescription(station, scope) {
  const metadata = state.hydrology?.station_metadata || {};
  const context = basinContext(station);
  const siteName = prettyName(station.display_name || metadata.name || station.provider_station_id);
  const siteNameWithoutStateCode = siteName.replace(/,\s*[A-Z]{2}$/, "");
  const waterSource = prettyName(station.water_source || "");
  const stateName = prettyName(station.state_name || "");
  const latitude = safeNumber(station.latitude ?? metadata.latitude);
  const longitude = safeNumber(station.longitude ?? metadata.longitude);
  const area = safeNumber(metadata.drainage_area_sq_mi ?? station.drainage_area_sq_mi);
  const elevation = safeNumber(metadata.elevation_ft);
  const coordinates = latitude != null && longitude != null ? Number(latitude).toFixed(5) + ", " + Number(longitude).toFixed(5) : null;
  const waterClause = waterSource && waterSource !== "—" && !siteName.toLowerCase().includes(waterSource.toLowerCase()) ? " on " + waterSource : "";
  const position = "The station is " + siteNameWithoutStateCode + waterClause
    + (stateName && stateName !== "—" ? " in " + stateName : "")
    + (coordinates ? " at " + coordinates : "") + ".";
  const control = area != null
    ? "The reported contributing watershed is approximately " + formatNumber(area, 1) + " mi²"
    : "The station metadata does not report a contributing area";
  const elevationText = elevation != null
    ? " The site elevation is " + formatNumber(elevation, 1) + " ft" + (metadata.elevation_datum ? " (" + text(metadata.elevation_datum) + ")" : "") + "."
    : " Site elevation is not available in the station package.";
  const hucText = context.huc ? " It is within HUC " + context.huc + (context.huc4 ? " (HUC4 " + context.huc4 + ", " + prettyName(context.name) + ")" : "") + "." : "";
  const setting = " Screening interpretation: this is " + localSetting(elevation, area);
  const snowmelt = elevation != null && elevation >= 6000
    ? " At this elevation, seasonal snow accumulation and melt are plausible controls on the annual runoff pattern; use the hydrograph and Water-Year views to verify that interpretation."
    : " The page does not infer a runoff mechanism from geography alone; use the hydrograph and Water-Year views to assess seasonal behavior.";
  const localSource = state.watershed
    ? " Within the highlighted contributing watershed, the local flow network shows how runoff is routed toward the station control point. Nearby markers represent only station packages created on this computer. Source basis: USGS station metadata and the downloaded NLDI contributing boundary."
    : " A station-specific contributing watershed is not available in this local package.";
  const regionalSource = " The regional panel is intentionally limited to locally cached station packages and this station's contributing watershed; it does not rely on a prebuilt basin release.";
  const regionText = " It is part of the " + prettyName(context.name) + (context.huc4 ? " HUC4" : " watershed") + " within the Colorado River Basin."
    + (context.states ? " The regional reference spans " + context.states.split(",").map((code) => STATE_NAMES[code.trim()] || code.trim()).filter(Boolean).join(", ") + "." : "")
    + " " + context.description;
  return (scope === "local" ? "Local watershed. " : "Regional context. ")
    + position + " " + control + "." + elevationText + hucText + setting + snowmelt
    + regionText + (scope === "local" ? localSource : regionalSource);
}
function mapStyle() {
  return { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#e8f1ed" } }] };
}
function addMapReset(map, center, zoom) {
  map.addControl({ onAdd: (instance) => { const node = document.createElement("button"); node.className = "maplibregl-ctrl-icon map-reset-button"; node.type = "button"; node.title = "Reset map view"; node.textContent = "⌖"; node.addEventListener("click", () => instance.flyTo({ center, zoom })); return node; }, onRemove: () => {} }, "top-right");
}
function addMapHover(map, layerId, label) {
  map.on("mouseenter", layerId, () => { map.getCanvas().style.cursor = "pointer"; });
  map.on("mouseleave", layerId, () => { map.getCanvas().style.cursor = ""; });
  map.on("click", layerId, (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const properties = feature.properties || {};
    const basinName = properties.HU_6_Name || (properties.huc2 === "14" ? "Upper Colorado Region" : properties.huc2 === "15" ? "Lower Colorado Region" : "");
    const name = properties.display_name || properties.station_name || properties.gnis_name || properties.name || properties.DAM_NAME || properties.dam_name || basinName || label;
    const identifier = properties.location_key || properties.station_id || properties.permanent_identifier || properties.NIDID || properties.nidid || "";
    new Popup({ closeButton: true, offset: 10 }).setLngLat(event.lngLat).setHTML("<strong>" + escapeHtml(name) + "</strong>" + (identifier ? "<br><span>" + escapeHtml(identifier) + "</span>" : "")).addTo(map);
  });
}
async function initMaps() {
  const station = state.analysis.station;
  const latitude = safeNumber(station.latitude);
  const longitude = safeNumber(station.longitude);
  if (latitude == null || longitude == null) { $("#map-note").textContent = "Station coordinates are not available."; $("#local-map-note").textContent = "Station coordinates are not available."; return; }
  const getJson = async (path) => { try { return await api(path); } catch (_) { return null; } };
  state.watershed = await getJson("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/watershed");
  state.flowNetwork = await getJson("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/flow-network");
  const stationPayload = await getJson("/api/v1/map/stations?include_inactive=false&include_non_hydro=true");
  state.regionalStations = stationPayload || null;
  const regional = new MapLibreMap({ container: "regional-map", style: mapStyle(), center: [longitude, latitude], zoom: 7, attributionControl: true });
  const local = new MapLibreMap({ container: "local-map", style: mapStyle(), center: [longitude, latitude], zoom: 11, attributionControl: true });
  state.regionalMap = regional; state.localMap = local;
  regional.addControl(new NavigationControl(), "top-right"); local.addControl(new NavigationControl(), "top-right");
  regional.addControl(new ScaleControl({ maxWidth: 120, unit: "imperial" }), "bottom-left");
  local.addControl(new ScaleControl({ maxWidth: 120, unit: "imperial" }), "bottom-left");
  addMapReset(regional, [longitude, latitude], 7); addMapReset(local, [longitude, latitude], 11);
  const stationMarker = (className, title, popupHtml) => {
    const node = document.createElement("button");
    node.type = "button";
    node.className = "station-map-marker " + className;
    node.title = title;
    node.setAttribute("aria-label", title);
    return new Marker({ element: node, anchor: "center" }).setLngLat([longitude, latitude]).setPopup(new Popup({ offset: 12 }).setHTML(popupHtml));
  };
  stationMarker("regional-station-marker", "Selected USGS station", "<strong>USGS " + escapeHtml(station.provider_station_id) + "</strong><br>" + escapeHtml(prettyName(station.display_name))).addTo(regional);
  stationMarker("local-station-marker", "Station control point", "<strong>Station control point</strong><br>USGS " + escapeHtml(station.provider_station_id)).addTo(local);
  regional.on("load", () => {
    if (state.watershed) {
      regional.addSource("regional-station-watershed", { type: "geojson", data: state.watershed });
      regional.addLayer({ id: "regional-station-watershed-fill", type: "fill", source: "regional-station-watershed", paint: { "fill-color": "#8ecae6", "fill-opacity": .16 } });
      regional.addLayer({ id: "regional-station-watershed", type: "line", source: "regional-station-watershed", paint: { "line-color": COLORS.stage, "line-width": 3 } });
    }
    if (state.regionalStations?.features?.length) {
      regional.addSource("regional-stations", { type: "geojson", data: state.regionalStations });
      regional.addLayer({ id: "regional-stations", type: "circle", source: "regional-stations", paint: { "circle-radius": 3.5, "circle-color": COLORS.discharge, "circle-opacity": .45, "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      addMapHover(regional, "regional-stations", "Locally cached station");
    }
    const bounds = geometryBounds(state.watershed);
    if (!bounds.isEmpty()) regional.fitBounds(bounds, { padding: 36, maxZoom: 8 });
    $("#regional-map-layer-status").textContent = (state.regionalStations?.features?.length || 0).toLocaleString() + " locally cached stations · station watershed " + (state.watershed ? "loaded" : "unavailable");
  });
  local.on("load", () => {
    if (state.watershed) {
      local.addSource("local-watershed", { type: "geojson", data: state.watershed });
      local.addLayer({ id: "local-watershed-fill", type: "fill", source: "local-watershed", paint: { "fill-color": "#8ecae6", "fill-opacity": .2 } });
      local.addLayer({ id: "local-watershed-line", type: "line", source: "local-watershed", paint: { "line-color": COLORS.discharge, "line-width": 2.2 } });
    }
    if (state.flowNetwork) {
      local.addSource("local-flow-network", { type: "geojson", data: state.flowNetwork });
      local.addLayer({ id: "local-flow-network", type: "line", source: "local-flow-network", paint: { "line-color": "#176c88", "line-width": 2, "line-opacity": .8 } });
      addMapHover(local, "local-flow-network", "Local flowline");
    }
    if (state.regionalStations?.features?.length) {
      local.addSource("local-stations", { type: "geojson", data: state.regionalStations });
      local.addLayer({ id: "local-stations", type: "circle", source: "local-stations", paint: { "circle-radius": 3.5, "circle-color": COLORS.stage, "circle-opacity": .6, "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      addMapHover(local, "local-stations", "Locally cached station");
    }
    const bounds = state.watershed ? geometryBounds(state.watershed) : new LngLatBounds([longitude - .05, latitude - .05], [longitude + .05, latitude + .05]);
    if (!bounds.isEmpty()) local.fitBounds(bounds, { padding: 36, maxZoom: 13 });
    $("#local-map-layer-status").textContent = (state.flowNetwork ? "NHDPlus flow network loaded" : "Flow network unavailable") + " · " + (state.regionalStations?.features?.length || 0).toLocaleString() + " locally cached stations";
  });
  $("#map-note").textContent = stationGeographicDescription(station, "regional") + " This map intentionally shows only local station packages and the station's downloaded contributing watershed.";
  $("#local-map-note").textContent = stationGeographicDescription(station, "local") + (state.flowNetwork ? " The downloaded NHDPlus-derived network is a geographic reference display, not a directed operational topology." : " The station-specific flow network is not available in this local package.");
}
async function loadHydrograph() {
  const stageKey = $("#stage-series").value;
  const dischargeKey = $("#discharge-series").value;
  const start = $("#hydrograph-start").value;
  const end = $("#hydrograph-end").value;
  if (!start || !end || start > end) return;
  setPill("#hydrograph-status", "Loading");
  try {
    const dailyFor = async (seriesKey, sourceVariable, unit) => {
      if (!seriesKey) return { points: [], unit_canonical: unit };
      try {
        return await api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/local-daily?source_variable=" + sourceVariable + "&start=" + start + "&end=" + end);
      } catch (_) {
        return await api("/api/v1/series/" + encodeURIComponent(seriesKey) + "/daily?start=" + start + "&end=" + end + "&statistic=mean");
      }
    };
    const baseflowFor = async () => {
      try {
        return await api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/baseflow-daily?start=" + start + "&end=" + end);
      } catch (_) {
        return { points: [], unit_canonical: "ft³/s" };
      }
    };
    const [stage, discharge, baseflow] = await Promise.all([
      dailyFor(stageKey, "00065", "ft"),
      dailyFor(dischargeKey, "00060", "ft³/s"),
      baseflowFor(),
    ]);
    const stageMeta = state.analysis.series.find((series) => series.series_key === stageKey) || {};
    const dischargeMeta = state.analysis.series.find((series) => series.series_key === dischargeKey) || {};
    state.hydrographData = {
      stage: { points: dailyPoints(stage), unit: stage.unit_canonical || "ft", frequency: "daily", variable: stageMeta.variable_name || "Gage height", sourceStart: stage.observed_date_start || dailyPoints(stage)[0]?.date || stageMeta.observed_date_start, sourceEnd: stage.observed_date_end || dailyPoints(stage).at(-1)?.date || stageMeta.observed_date_end },
      discharge: { points: dailyPoints(discharge), unit: discharge.unit_canonical || "ft³/s", frequency: "daily", variable: dischargeMeta.variable_name || "Discharge" },
      baseflow: { points: dailyPoints(baseflow), unit: baseflow.unit_canonical || "ft³/s", frequency: "daily", variable: "Retained Lyne–Hollick baseflow" },
    };
    state.hydroZoom = null;
    state.hydroDischargeRange = null;
    state.hydroStageRange = null;
    renderHydrographToggles();
    renderHydrograph();
    const stageCoverage = state.hydrographData.stage.sourceStart && state.hydrographData.stage.sourceEnd ? " Stage source coverage: " + state.hydrographData.stage.sourceStart + " → " + state.hydrographData.stage.sourceEnd + "." : "";
    const stageContinuous = availabilitySeries("stage", "unit");
    const continuousNotice = stageContinuous?.declared_start && stageContinuous?.declared_end ? " Gage height is reduced from the locally retained continuous archive (" + stageContinuous.declared_start + " → " + stageContinuous.declared_end + ") to daily means." : "";
    $("#hydrograph-note").textContent = "Discharge: " + discharge.points.length.toLocaleString() + " daily mean values; gage height: " + stage.points.length.toLocaleString() + " daily mean values; baseflow: " + baseflow.points.length.toLocaleString() + " retained filter values." + stageCoverage + continuousNotice + " Display period " + start + " through " + end + ". Drag across the plot to zoom; double-click resets. Moving average is calculated only for display.";
    setPill("#hydrograph-status", "Chart updated");
  } catch (error) {
    $("#hydrograph").innerHTML = '<p class="chart-empty">' + escapeHtml(error.message) + "</p>";
    setPill("#hydrograph-status", "Unavailable", "error");
  }
}
async function loadDailyPattern() {
  const daily = stationSeries("discharge", "daily") || stationSeries("discharge");
  if (!daily?.series_key || !daily.observed_date_start || !daily.observed_date_end) return;
  try {
    const result = await api("/api/v1/series/" + encodeURIComponent(daily.series_key) + "/daily?start=" + daily.observed_date_start + "&end=" + daily.observed_date_end + "&statistic=mean");
    state.dailyPattern = normalizeDailyPattern(dailyPoints(result));
    renderWaterYearControls();
    renderWaterYearHeatmap();
    renderWaterYearComparison();
    renderMonthlyAvailabilityChart();
  } catch (error) {
    $("#water-year-heatmap").innerHTML = '<p class="chart-empty">' + escapeHtml(error.message) + "</p>";
  }
}
async function loadStageDischarge() {
  const stageKey = (state.analysis?.series || []).find((series) => seriesCategory(series) === "stage" && frequencyKey(series.frequency) === "points")?.series_key || $("#stage-series").value;
  const dischargeKey = (state.analysis?.series || []).find((series) => seriesCategory(series) === "discharge" && frequencyKey(series.frequency) === "points")?.series_key || $("#discharge-series").value;
  const start = $("#stage-discharge-start").value;
  const end = $("#stage-discharge-end").value;
  if (!stageKey || !dischargeKey || !start || !end) return;
  setPill("#stage-discharge-status", "Loading");
  $("#stage-discharge-scatter").innerHTML = '<p class="chart-empty">Loading field observations and timestamp-aligned pairs…</p>';
  try {
    // Field measurements are the stable reference layer. They must not be
    // clipped to the currently selected paired-observation interval.
    const fieldStart = "1900-01-01";
    const fieldEnd = "2999-12-31";
    const [pairedResult, fieldResult] = await Promise.all([
      api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/stage-discharge?start=" + start + "&end=" + end + "&grain=raw&stage_series=" + encodeURIComponent(stageKey) + "&discharge_series=" + encodeURIComponent(dischargeKey)),
      api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/field-stage-discharge?start=" + fieldStart + "&end=" + fieldEnd),
    ]);
    const paired = pairedResult.points || [];
    const field = fieldResult.points || [];
    state.stageDischargeMode = "paired+field";
    state.stageDischargePoints = [
      ...field.map((point) => ({ ...point, source_type: "USGS field observation" })),
      ...paired.map((point) => ({ ...point, source_type: "raw timestamp-aligned pair" })),
    ];
    renderScatter(paired, field, start, end);
    setPill("#stage-discharge-status", formatNumber(field.length, 0) + " field · " + formatNumber(paired.length, 0) + " paired");
  } catch (error) {
    $("#stage-discharge-scatter").innerHTML = '<p class="chart-empty">' + escapeHtml(error.message) + "</p>";
    setPill("#stage-discharge-status", "Unavailable", "error");
  }
}
function renderScatter(pairedPoints, fieldPoints, start, end) {
  const normalize = (point, source) => ({
    discharge: safeNumber(point.discharge_value),
    stage: safeNumber(point.stage_value),
    date: point.observed_date,
    timestamp: point.observed_at_utc || point.observed_at_original,
    source,
  });
  const field = fieldPoints.map((point) => normalize(point, "field")).filter((point) => point.discharge != null && point.stage != null && point.discharge >= 0 && point.stage >= 0);
  const paired = pairedPoints.map((point) => normalize(point, "paired")).filter((point) => point.discharge != null && point.stage != null && point.discharge >= 0 && point.stage >= 0);
  const usable = [...field, ...paired];
  if (!usable.length) { $("#stage-discharge-scatter").innerHTML = '<p class="chart-empty">No valid stage–discharge observations are available.</p>'; return; }

  const maxDisplayed = 12000;
  const step = Math.max(1, Math.ceil(paired.length / maxDisplayed));
  const displayedPaired = paired.filter((_point, index) => index % step === 0);
  const width = 1120, height = 470, left = 100, right = 1065, top = 60, bottom = 365;
  let dischargeMaximum = 1;
  for (const point of usable) dischargeMaximum = Math.max(dischargeMaximum, point.discharge);
  const stageScale = scaleFor(usable.map((point) => point.stage), top, bottom, false);
  const fullX = [0, dischargeMaximum * 1.04 || 1];
  const fullY = [stageScale.minimum, stageScale.maximum];
  const xDomain = state.stageDischargeXRange || fullX;
  const yDomain = state.stageDischargeYRange || fullY;
  const xScale = { minimum: xDomain[0], maximum: xDomain[1], scale: (value) => left + (value - xDomain[0]) / (xDomain[1] - xDomain[0] || 1) * (right - left), ticks: niceTicks(xDomain[0], xDomain[1], 6) };
  const yScale = { minimum: yDomain[0], maximum: yDomain[1], scale: (value) => bottom - (value - yDomain[0]) / (yDomain[1] - yDomain[0] || 1) * (bottom - top), ticks: niceTicks(yDomain[0], yDomain[1], 5) };
  const sx = (value) => xScale.scale(value);
  const sy = (value) => yScale.scale(value);
  const xDigits = axisDigits(xDomain[0], xDomain[1]);
  const yDigits = axisDigits(yDomain[0], yDomain[1]);
  const fieldMarks = field.map((point) => '<circle cx="' + sx(point.discharge).toFixed(1) + '" cy="' + sy(point.stage).toFixed(1) + '" r="3.5" class="scatter-field-point"><title>Field observation · ' + escapeHtml(point.date + ": " + formatNumber(point.discharge, 1) + " ft³/s, stage " + formatNumber(point.stage, 2) + " ft") + "</title></circle>").join("");
  const pairedMarks = displayedPaired.map((point) => '<circle cx="' + sx(point.discharge).toFixed(1) + '" cy="' + sy(point.stage).toFixed(1) + '" r="2" class="scatter-point"><title>Timestamp-aligned pair · ' + escapeHtml(point.timestamp || point.date) + ": " + formatNumber(point.discharge, 1) + " ft³/s, stage " + formatNumber(point.stage, 2) + " ft</title></circle>").join("");
  const body = '<defs><clipPath id="stage-discharge-clip"><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '"/></clipPath></defs><text x="' + ((left + right) / 2) + '" y="25" text-anchor="middle" class="panel-title">Stage vs. Discharge: ' + escapeHtml(state.stationKey?.replace(/^USGS:/, "") || "station") + '</text><text x="' + ((left + right) / 2) + '" y="45" text-anchor="middle" class="legend-label">Field observations remain visible; paired points follow the selected date range</text><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="chart-plot"/>' + xScale.ticks.map((tick) => '<line x1="' + sx(tick) + '" y1="' + top + '" x2="' + sx(tick) + '" y2="' + bottom + '" class="chart-grid"/><text x="' + sx(tick) + '" y="' + (bottom + 21) + '" text-anchor="middle" class="axis-label">' + escapeHtml(formatNumber(tick, xDigits)) + "</text>").join("") + yScale.ticks.map((tick) => '<line x1="' + left + '" y1="' + sy(tick) + '" x2="' + right + '" y2="' + sy(tick) + '" class="chart-grid"/><text x="' + (left - 10) + '" y="' + (sy(tick) + 4) + '" text-anchor="end" class="axis-label">' + escapeHtml(formatNumber(tick, yDigits)) + "</text>").join("") + '<g clip-path="url(#stage-discharge-clip)">' + pairedMarks + fieldMarks + '</g><text x="' + ((left + right) / 2) + '" y="' + (height - 31) + '" text-anchor="middle" class="axis-label">River discharge (ft³/s)</text><text x="23" y="' + ((top + bottom) / 2) + '" transform="rotate(-90 23 ' + ((top + bottom) / 2) + ')" text-anchor="middle" class="axis-label">River stage (ft)</text><g transform="translate(' + left + ',' + (height - 12) + ')"><circle cx="0" cy="0" r="3.5" class="scatter-field-point"/><text x="11" y="4" class="legend-label">Field observations · full archive</text><circle cx="226" cy="0" r="2" class="scatter-point"/><text x="236" y="4" class="legend-label">Paired observations · selected interval</text></g><rect id="stage-discharge-brush-preview" x="' + left + '" y="' + top + '" width="0" height="0" class="annual-brush-preview" display="none"/><rect x="' + left + '" y="' + top + '" width="' + (right - left) + '" height="' + (bottom - top) + '" class="plot-hit"/>';
  const rangeText = state.stageDischargeXRange ? " Selected discharge " + formatNumber(xDomain[0], 0) + "–" + formatNumber(xDomain[1], 0) + " ft³/s; stage " + formatNumber(yDomain[0], 2) + "–" + formatNumber(yDomain[1], 2) + " ft." : "";
  $("#stage-discharge-scatter").innerHTML = '<div class="chart-shell">' + svgFrame("Stage vs. Discharge: " + (state.stationKey?.replace(/^USGS:/, "") || "station"), field.length.toLocaleString() + " field observations retained across the archive; " + paired.length.toLocaleString() + " selected paired observations; " + displayedPaired.length.toLocaleString() + " paired points displayed for browser rendering. Drag a rectangle to select discharge and stage ranges; double-click resets." + rangeText, body, width, height) + "</div>";
  $("#stage-discharge-note").textContent = field.length.toLocaleString() + " field observations remain visible for the full field-measurement archive. " + paired.length.toLocaleString() + " raw timestamp-aligned points follow the draggable range " + start + " through " + end + "." + rangeText;
  const svg = $("#stage-discharge-scatter svg");
  const hit = svg.querySelector(".plot-hit");
  const preview = svg.querySelector("#stage-discharge-brush-preview");
  const pixelPosition = (event) => { const rect = svg.getBoundingClientRect(); return { x: (event.clientX - rect.left) * width / rect.width, y: (event.clientY - rect.top) * height / rect.height }; };
  const clampX = (value) => Math.max(left, Math.min(right, value));
  const clampY = (value) => Math.max(top, Math.min(bottom, value));
  const xValueAt = (pixel) => xDomain[0] + (clampX(pixel) - left) / (right - left || 1) * (xDomain[1] - xDomain[0]);
  const yValueAt = (pixel) => yDomain[0] + (bottom - clampY(pixel)) / (bottom - top || 1) * (yDomain[1] - yDomain[0]);
  let dragStart = null;
  hit.addEventListener("pointerdown", (event) => { dragStart = pixelPosition(event); hit.setPointerCapture?.(event.pointerId); });
  hit.addEventListener("pointermove", (event) => { if (!dragStart) return; const end = pixelPosition(event); const x1 = clampX(dragStart.x), x2 = clampX(end.x), y1 = clampY(dragStart.y), y2 = clampY(end.y); preview.setAttribute("x", Math.min(x1, x2)); preview.setAttribute("y", Math.min(y1, y2)); preview.setAttribute("width", Math.abs(x2 - x1)); preview.setAttribute("height", Math.abs(y2 - y1)); preview.setAttribute("display", "block"); });
  hit.addEventListener("pointerup", (event) => { if (!dragStart) return; const dragEnd = pixelPosition(event); if (Math.abs(dragEnd.x - dragStart.x) > 12 || Math.abs(dragEnd.y - dragStart.y) > 12) { state.stageDischargeXRange = [Math.min(xValueAt(dragStart.x), xValueAt(dragEnd.x)), Math.max(xValueAt(dragStart.x), xValueAt(dragEnd.x))]; state.stageDischargeYRange = [Math.min(yValueAt(dragStart.y), yValueAt(dragEnd.y)), Math.max(yValueAt(dragStart.y), yValueAt(dragEnd.y))]; renderScatter(pairedPoints, fieldPoints, start, end); } preview.setAttribute("display", "none"); dragStart = null; });
  hit.addEventListener("dblclick", (event) => { event.preventDefault(); state.stageDischargeXRange = null; state.stageDischargeYRange = null; renderScatter(pairedPoints, fieldPoints, start, end); });
}
function bindEvents() {
  $("#coverage-filter").addEventListener("change", (event) => { state.coverageFilter = event.target.value; renderCoverage(); });
  $("#stage-series").addEventListener("change", () => { loadHydrograph(); loadStageDischarge(); });
  $("#discharge-series").addEventListener("change", () => { loadHydrograph(); loadDailyPattern(); loadStageDischarge(); });
  $("#hydrograph-start").addEventListener("change", () => { $("#hydrograph-window").value = "custom"; loadHydrograph(); });
  $("#hydrograph-end").addEventListener("change", () => { $("#hydrograph-window").value = "custom"; loadHydrograph(); });
  $("#hydrograph-window").addEventListener("change", () => {
    const end = state.analysis.series.map((series) => series.observed_date_end).filter(Boolean).sort().at(-1);
    if (!end) return;
    if ($("#hydrograph-window").value === "recent10") $("#hydrograph-start").value = daysBefore(end, 3650);
    if ($("#hydrograph-window").value === "full") $("#hydrograph-start").value = state.analysis.series.filter((series) => seriesCategory(series) === "discharge" && !isWaterYearSeries(series)).map((series) => series.observed_date_start).filter(Boolean).sort()[0] || $("#hydrograph-start").value;
    $("#hydrograph-end").value = end;
    loadHydrograph();
  });
  $("#moving-average").addEventListener("change", renderHydrograph);
  $("#log-scale").addEventListener("change", renderHydrograph);
  $("#reset-chart-zoom").addEventListener("click", () => { state.hydroZoom = null; renderHydrograph(); });
  $("#hydrograph-variable-toggles").addEventListener("change", (event) => { const key = event.target.dataset.hydroVariable; if (key) { state.hydroVisibility[key] = event.target.checked; renderHydrograph(); } });
  $("#fdc-scale").addEventListener("change", renderFdc);
  $("#ffa-y-range").addEventListener("change", renderFfa);
  $("#ffa-y-scale").addEventListener("change", renderFfa);
  $("#ffa-show-historical").addEventListener("change", renderFfa);
  $("#ffa-reset-range").addEventListener("click", () => { $("#ffa-y-range").value = "auto"; renderFfa(); });
  $("#monthly-runoff-toggle")?.addEventListener("change", renderMonthlyAvailabilityChart);
  $("#stage-discharge-start-range").addEventListener("input", () => { syncStageDischargeRange("start"); scheduleStageDischargeLoad(); });
  $("#stage-discharge-end-range").addEventListener("input", () => { syncStageDischargeRange("end"); scheduleStageDischargeLoad(); });
  $("#stage-discharge-load").addEventListener("click", loadStageDischarge);
  bindDownloads();
}
async function boot() {
  if (window.location.protocol === "file:") {
    $("#station-title").textContent = "Open this station through the local console";
    $("#station-subtitle").textContent = "The station dashboard needs the local read-only API and release files.";
    setNotice("This HTML file is a view template, not a standalone data file. Start the local console, then open http://127.0.0.1:8765/station.html?station=USGS%3A09342500.");
    return;
  }
  state.stationKey = stationParameter();
  if (!state.stationKey) { $("#station-title").textContent = "Station not selected"; setNotice("No station key was supplied. Return to the basin control console and select a station."); return; }
  try {
    const analysis = await api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/analysis");
    state.analysis = analysis;
    try { state.hydrology = await api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/hydrology"); } catch (error) { state.hydrology = null; setNotice("Station analysis loaded, but the independent station hydrology package is unavailable: " + error.message); }
    const station = analysis.station;
    $("#station-title").textContent = prettyName(station.display_name);
    $("#station-subtitle").textContent = state.stationKey + " · " + text(station.source_name) + " · interactive station workspace";
    renderHeaderStatus(station);
    renderMetadata(station);
    renderExternalLinks(station);
    renderQuickSummary();
    renderHydrologicIntro();
    renderCoverage();
    populateSeries();
    renderQuality();
    if (state.hydrology) {
      state.fdc = await api("/api/v1/stations/" + encodeURIComponent(state.stationKey) + "/hydrology/fdc").catch(() => []);
      renderFdc();
      renderFdcTable();
      renderAnnualFlow();
      renderAnnualRunoff();
      renderWaterAvailability();
      renderBaseflow();
      renderFfa();
      renderProvenance();
      loadDailyPattern();
    }
    await initMaps();
    bindEvents();
    await loadHydrograph();
    await loadStageDischarge();
    if (window.location.hash) document.querySelector(window.location.hash)?.scrollIntoView({ behavior: "auto", block: "start" });
  } catch (error) {
    $("#station-title").textContent = "Station analysis unavailable";
    setNotice(error.message);
    setPill("#summary-status", "Unavailable", "error");
  }
}
window.addEventListener("DOMContentLoaded", () => { bindNavigation(); boot(); });
