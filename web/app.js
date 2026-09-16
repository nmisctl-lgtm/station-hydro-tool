/* San Juan Basin Digital Twin local console.  Observation data never leave localhost. */
import * as maplibregl from "/static/vendor/maplibre-gl.mjs";

const state = {
  map: null,
  stations: [],
  features: { type: "FeatureCollection", features: [] },
  hydrography: null,
  context: null,
  basinBounds: null,
  localLayers: [],
  coverageMarkers: [],
  releaseId: null,
};
const $ = (selector) => document.querySelector(selector);
const BASEMAPS = {
  local: { label: "Local hydrography only", tiles: null, note: "Offline: stations and USGS reference layers are read from this Data Release." },
  topo: { label: "USGS topographic", tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"], note: "Online optional raster basemap from USGS." },
  imagery: { label: "USGS satellite + topo", tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"], note: "Online optional imagery/topographic raster basemap from USGS." },
  terrain: { label: "USGS shaded relief", tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}"], note: "Online optional shaded-relief raster basemap from USGS; local river and basin layers remain available offline." },
};

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error?.message || `Request failed (${response.status})`);
  return payload;
}

async function localJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} is not part of this Data Release.`);
  return response.json();
}

async function loadBasinContext() {
  const payload = await api("/api/v1/context/overview").catch(() => ({ data: { available: false, manifest: {}, paths: {} } }));
  const context = payload.data || payload;
  if (!context.available) return;
  const paths = context.paths || {};
  const entries = await Promise.all(Object.entries(paths).map(async ([key, path]) => [key, await localJson(path).catch(() => null)]));
  state.context = { ...context, layers: Object.fromEntries(entries) };
}

function text(value) { return value == null || value === "" ? "—" : String(value); }
function escapeHtml(value) { return text(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }
function query(filters = {}) { const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value)); return params.toString() ? `?${params}` : ""; }
function reviewCoordinate(status) { return status !== "valid"; }
function locationStatus(status) { return status === "valid" ? "Mapped" : "Needs review"; }
function prettyName(value) {
  const raw = String(value || "").trim();
  if (!raw) return "—";
  if (raw !== raw.toUpperCase()) return raw;
  const words = raw.toLowerCase().replace(/\b\w/g, (match) => match.toUpperCase()).replace(/\b(Nr|At|Of|The|And|On|To|In)\b/g, (match) => match.toLowerCase()).split(/(\s+|[-/])/);
  return words.map((word) => ({ usgs: "USGS", nm: "NM", co: "CO", dwr: "DWR", cdss: "CDSS", meas: "MEAS", gage: "Gage", huc: "HUC", navd88: "NAVD88" }[word.toLowerCase()] || word)).join("");
}
function sourceColor(source) { return source === "USGS" ? "#176c88" : source === "New Mexico OSE/ISC MEAS" ? "#c96f2f" : source === "Colorado DWR CDSS" ? "#6b5b95" : "#4d7c62"; }
function variableColor(category) { return ({ stage: "#c96f2f", discharge: "#176c88", water_quality: "#e6ab02", sediment: "#a6761d", other: "#8c6bb1" })[category] || "#666666"; }
function profileGroup(profile) {
  const value = String(profile || "");
  if (value === "stage_and_discharge") return "both";
  if (value.startsWith("stage")) return "stage";
  if (value.startsWith("discharge")) return "discharge";
  if (value.includes("water_quality")) return "water_quality";
  if (value.includes("sediment")) return "sediment";
  return "other";
}
function profileColor(profile) { return variableColor(profileGroup(profile)); }

function currentFilters() {
  return { source: $("#filter-source").value, coordinate_status: $("#filter-coordinate").value, search: $("#filter-search").value.trim(), include_inactive: $("#show-inactive").checked ? "true" : "", include_non_hydro: $("#show-non-hydro").checked ? "true" : "" };
}

function restoreUrlState() {
  const params = new URLSearchParams(window.location.search);
  for (const [field, selector] of [["source", "#filter-source"], ["coordinate_status", "#filter-coordinate"], ["search", "#filter-search"]]) {
    const value = params.get(field);
    if (value) $(selector).value = value;
  }
  $("#show-inactive").checked = params.get("include_inactive") === "true";
  $("#show-non-hydro").checked = params.get("include_non_hydro") === "true";
}

function syncUrl() {
  const params = new URLSearchParams();
  const filters = currentFilters();
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  if (state.releaseId) params.set("release", state.releaseId);
  const url = `${window.location.pathname}${params.size ? `?${params}` : ""}`;
  window.history.replaceState(null, "", url);
}

function renderMetrics(data) {
  $("#metric-stations").textContent = text(data.registered_location_count);
  $("#metric-mapped").textContent = text(data.mapped_location_count);
  $("#metric-coordinate-review").textContent = text(data.coordinate_review_count);
  $("#metric-daily").textContent = Number(data.daily_observation_count || 0).toLocaleString();
}

function latestValue(values) { return values.filter(Boolean).sort().at(-1) || "—"; }

function formatAge(seconds) { if (!Number.isFinite(seconds)) return "age unavailable"; const days = Math.floor(seconds / 86400); return days ? `${days} d` : `${Math.floor(seconds / 3600)} h`; }

function renderArchiveStatus(stations, runs, sourceStatuses) {
  const grouped = new Map();
  for (const station of stations) {
    const source = station.source_name || "Unknown source";
    const current = grouped.get(source) || { source, stationCount: 0, archived: 0, observed: [], retrieved: [], candidates: 0 };
    current.stationCount += 1; current.archived += station.archive_presence === "present" ? 1 : 0;
    current.observed.push(station.latest_observed_at); current.retrieved.push(station.latest_retrieved_at); current.candidates += Number(station.qa_candidate_series_count || 0);
    grouped.set(source, current);
  }
  const evidence = new Map((sourceStatuses || []).map((row) => [row.source_name, row]));
  $("#archive-status-rows").innerHTML = [...grouped.values()].sort((left, right) => left.source.localeCompare(right.source)).map((row) => {
    const source = evidence.get(row.source); const retrieved = source?.latest_retrieved_at_utc || latestValue(row.retrieved);
    const artifact = source?.latest_artifact_id == null ? "—" : `<a href="/api/v1/provenance/artifacts/${encodeURIComponent(source.latest_artifact_id)}" target="_blank" rel="noopener" title="${escapeHtml(source.latest_artifact_sha256)}">artifact ${escapeHtml(source.latest_artifact_id)}</a>`;
    return `<tr><td>${escapeHtml(row.source)}</td><td>${row.stationCount.toLocaleString()}</td><td>${row.archived.toLocaleString()}</td><td>${escapeHtml(latestValue(row.observed))}</td><td>${escapeHtml(retrieved)}<span class="station-key">${escapeHtml(formatAge(source?.response_age_seconds))}</span></td><td>${Number(source?.failed_request_evidence_count || 0).toLocaleString()}</td><td>${artifact}</td></tr>`;
  }).join("") || '<tr><td colspan="7" class="muted">No source status rows are available.</td></tr>';
  const latest = [...runs].sort((left, right) => String(left.completed_at_utc || "").localeCompare(String(right.completed_at_utc || ""))).at(-1);
  $("#update-run-note").textContent = latest ? `Latest normalized operation: ${latest.run_type} · ${latest.status} · ${latest.completed_at_utc || "time unavailable"}. “Failed evidence” counts only recorded HTTP >=400 coverage exceptions; provider catalogs may contain additional unnormalized detail.` : "No normalized operation history is available in this release.";
}

function renderHealthMatrix(matrix) {
  const months = matrix.months || []; const rows = matrix.rows || [];
  $("#health-matrix-head").innerHTML = `<tr><th>Station / variable</th>${months.map((month) => `<th>${escapeHtml(month.slice(2))}</th>`).join("")}</tr>`;
  $("#health-matrix-rows").innerHTML = rows.map((row) => {
    const label = `${row.display_name} · ${row.variable_name || row.source_variable} · ${row.unit_canonical || "unit pending"}`;
    const cells = months.map((month) => {
      const record = row.monthly?.[month]; const present = Number(record?.daily_record_count || 0) > 0; const candidate = Number(row.candidate_event_count || 0) > 0;
      const exact = record ? `${month}: ${record.daily_record_count} daily rows; ${record.numeric_observation_count} numeric source observations` : `${month}: no retained daily row`;
      return `<td class="health-cell ${present ? "record-present" : "record-absent"} ${candidate ? "candidate-cell" : ""}" title="${escapeHtml(exact)}">${present ? Number(record.daily_record_count).toLocaleString() : ""}</td>`;
    }).join("");
    return `<tr><td><span class="station-name">${escapeHtml(label)}</span><span class="station-key">${escapeHtml(row.location_key)}</span></td>${cells}</tr>`;
  }).join("") || `<tr><td colspan="${months.length + 1}" class="muted">No retained daily series are available for this window.</td></tr>`;
  $("#health-matrix-note").textContent = matrix.interpretation || "";
}

function fillSources(stations) {
  const select = $("#filter-source");
  const selected = select.value;
  const sources = [...new Set(stations.map((station) => station.source_name).filter(Boolean))].sort();
  select.replaceChildren(new Option("All sources", ""), ...sources.map((source) => new Option(source, source)));
  select.value = sources.includes(selected) ? selected : "";
}

function openStation(locationKey) {
  const params = new URLSearchParams({ station: locationKey });
  if (state.releaseId) params.set("release", state.releaseId);
  const [provider, stationId] = String(locationKey || "").split(":", 2);
  if (!provider || !stationId) return;
  window.open(`/station/${encodeURIComponent(provider)}/${encodeURIComponent(stationId)}?${params}`, "_blank", "noopener");
}

function renderRows(stations) {
  const tbody = $("#station-rows");
  tbody.replaceChildren();
  for (const station of stations) {
    const row = document.createElement("tr");
    row.tabIndex = 0;
    row.setAttribute("role", "link");
    row.setAttribute("aria-label", `Open analysis for ${prettyName(station.display_name)}`);
    row.innerHTML = `<td><span class="station-name">${escapeHtml(prettyName(station.display_name))}</span><span class="station-key">${escapeHtml(station.location_key)}</span></td><td>${escapeHtml(station.source_name)}</td><td>${escapeHtml(station.station_type)}</td><td><span class="profile-chip" style="--profile-color:${profileColor(station.variable_profile)}">${escapeHtml(station.variable_profile || station.archive_class || "—")}</span><span class="station-key">${escapeHtml(station.activity_status || "—")}</span></td><td><span class="status-dot ${reviewCoordinate(station.coordinate_status) ? "coordinate-review" : "coordinate-valid"}"></span>${escapeHtml(locationStatus(station.coordinate_status))}</td>`;
    row.addEventListener("click", () => openStation(station.location_key));
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openStation(station.location_key); } });
    tbody.append(row);
  }
  $("#filter-result").textContent = `${stations.length.toLocaleString()} matching station${stations.length === 1 ? "" : "s"} · select one to open its analysis workspace`;
}

function visibleFeatures(filters) {
  return { type: "FeatureCollection", features: state.features.features.filter((feature) => {
    const property = feature.properties || {};
    const normalized = String(filters.search || "").toLocaleLowerCase();
    const coordinateMatches = !filters.coordinate_status
      || (filters.coordinate_status === "review" ? reviewCoordinate(property.coordinate_status) : property.coordinate_status === filters.coordinate_status);
    return (!filters.source || property.source_name === filters.source)
      && coordinateMatches
      && (!filters.include_inactive || property.activity_status === "active" || filters.include_inactive === "true")
      && (!filters.include_non_hydro || property.hydro_numeric_observation_count > 0 || filters.include_non_hydro === "true")
      && (!normalized || [property.location_key, property.provider_station_id, property.display_name].some((value) => String(value || "").toLocaleLowerCase().includes(normalized)));
  }) };
}

function addBasemap(map) {
  map.addSource("optional-usgs-basemap", { type: "raster", tiles: BASEMAPS.terrain.tiles, tileSize: 256, attribution: "USGS The National Map" });
  map.addLayer(
    { id: "optional-usgs-basemap", type: "raster", source: "optional-usgs-basemap", layout: { visibility: "visible" }, paint: { "raster-opacity": 0.62 } },
  );
}

function setLayerOpacity(layerIds, value) {
  const opacity = Math.max(0, Math.min(1, Number(value)));
  for (const layerId of layerIds) {
    if (state.map?.getLayer(layerId)) state.map.setPaintProperty(layerId, "raster-opacity", opacity);
  }
  const output = $("#basemap-opacity-value");
  if (output) output.textContent = Math.round(opacity * 100) + "%";
}

function setBasemap(value) {
  const choice = BASEMAPS[value] || BASEMAPS.local;
  const map = state.map;
  if (!map || !map.getSource("optional-usgs-basemap")) return;
  const source = map.getSource("optional-usgs-basemap");
  if (choice.tiles) source.setTiles(choice.tiles);
  map.setLayoutProperty("optional-usgs-basemap", "visibility", choice.tiles ? "visible" : "none");
  $("#map-basemap-note").textContent = choice.note;
}

function clearCoverageMarkers() {
  for (const marker of state.coverageMarkers) marker.remove();
  state.coverageMarkers = [];
}

function renderCoverageMarkers(features) {
  clearCoverageMarkers();
  if (!$("#toggle-coverage-bars")?.checked || !state.map) return;
  const allDurations = features.flatMap((feature) => {
    try { return JSON.parse(feature.properties?.variable_coverage_json || "[]").map((item) => Number(item.duration_days || 0)); } catch (_) { return []; }
  });
  const maximum = Math.max(...allDurations, 1);
  for (const feature of features) {
    let coverage = [];
    try { coverage = JSON.parse(feature.properties?.variable_coverage_json || "[]"); } catch (_) { /* keep empty */ }
    if (!coverage.length) continue;
    const element = document.createElement("div"); element.className = "coverage-marker"; element.title = `${prettyName(feature.properties.display_name)} variable coverage`;
    for (const item of coverage) {
      const bar = document.createElement("span"); bar.className = "coverage-bar"; bar.style.width = `${Math.max(4, Math.min(58, Number(item.duration_days || 0) / maximum * 58))}px`; bar.style.background = variableColor(item.category); bar.title = `${item.variable_name || item.source_variable}: ${item.first_day || "—"} → ${item.last_day || "—"}`; element.append(bar);
    }
    state.coverageMarkers.push(new maplibregl.Marker({ element, anchor: "bottom-left" }).setLngLat(feature.geometry.coordinates).addTo(state.map));
  }
}

function layerVisibility(layerId, checked) {
  if (state.map?.getLayer(layerId)) state.map.setLayoutProperty(layerId, "visibility", checked ? "visible" : "none");
}

function addBasinContext(map) {
  const layers = state.context?.layers || {};
  if (layers.state_boundaries?.features?.length) {
    map.addSource("context-state-boundaries", { type: "geojson", data: layers.state_boundaries });
    map.addLayer({ id: "context-state-fill", type: "fill", source: "context-state-boundaries", paint: { "fill-color": "#e7ece9", "fill-opacity": 0.2 } });
    map.addLayer({ id: "context-state-lines", type: "line", source: "context-state-boundaries", paint: { "line-color": "#778680", "line-width": 1.1, "line-dasharray": [2, 2], "line-opacity": 0.82 } });
    map.addLayer({ id: "context-state-labels", type: "symbol", source: "context-state-boundaries", layout: { "text-field": ["get", "state_abbr"], "text-size": 17, "text-font": ["Open Sans Regular"], "text-allow-overlap": true }, paint: { "text-color": "#66716d", "text-opacity": 0.8, "text-halo-color": "#f6faf8", "text-halo-width": 1.5 } });
    $("#toggle-context-states").disabled = false;
  }
  if (layers.basin_boundary?.features?.length) {
    map.addSource("context-basin-boundary", { type: "geojson", data: layers.basin_boundary });
    map.addLayer({ id: "context-basin-fill", type: "fill", source: "context-basin-boundary", paint: { "fill-color": "#cfe5ee", "fill-opacity": 0.22 } });
    map.addLayer({ id: "context-basin-line", type: "line", source: "context-basin-boundary", paint: { "line-color": "#2873c9", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.9 } });
  }
  if (layers.major_rivers?.features?.length) {
    map.addSource("context-major-rivers", { type: "geojson", data: layers.major_rivers });
    map.addLayer({ id: "context-major-rivers", type: "line", source: "context-major-rivers", paint: { "line-color": "#1167dc", "line-width": ["interpolate", ["linear"], ["zoom"], 4, 1.8, 8, 3.5], "line-opacity": 0.95 } });
    map.addLayer({ id: "context-river-labels", type: "symbol", source: "context-major-rivers", layout: { "symbol-placement": "line", "text-field": ["coalesce", ["get", "river_name"], ["get", "name_at_outlet"]], "text-size": 12, "text-font": ["Open Sans Italic"], "text-allow-overlap": false }, paint: { "text-color": "#1167dc", "text-halo-color": "#f6faf8", "text-halo-width": 1.5 } });
    $("#toggle-context-rivers").disabled = false;
  }
  if (layers.major_towns?.features?.length) {
    map.addSource("context-major-towns", { type: "geojson", data: layers.major_towns });
    map.addLayer({ id: "context-town-points", type: "circle", source: "context-major-towns", paint: { "circle-radius": 2.5, "circle-color": "#8a5a31", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
    map.addLayer({ id: "context-town-labels", type: "symbol", source: "context-major-towns", layout: { "text-field": ["get", "town_name"], "text-size": 10, "text-offset": [0.6, 0], "text-anchor": "left", "text-allow-overlap": false }, paint: { "text-color": "#704822", "text-halo-color": "#f6faf8", "text-halo-width": 1.5 } });
  }
  if (layers.major_nid_facilities?.features?.length) {
    map.addSource("context-major-facilities", { type: "geojson", data: layers.major_nid_facilities });
    map.addLayer({ id: "context-major-facilities", type: "symbol", source: "context-major-facilities", layout: { "text-field": "▲", "text-size": 13, "text-allow-overlap": true }, paint: { "text-color": "#263238", "text-halo-color": "#fff", "text-halo-width": 1.2 } });
    map.addLayer({ id: "context-facility-labels", type: "symbol", source: "context-major-facilities", minzoom: 7, layout: { "text-field": ["get", "facility_name"], "text-size": 9, "text-offset": [0.8, 0], "text-anchor": "left", "text-allow-overlap": false }, paint: { "text-color": "#263238", "text-halo-color": "#fff", "text-halo-width": 1.2 } });
    $("#toggle-context-facilities").disabled = false;
  }
  const count = Number(state.context?.manifest?.counts?.analyzed_packages_mapped || 0);
  if (count) $("#hydrography-note").textContent = `Cartographic context loaded · ${count.toLocaleString()} analyzed station locations · local river, town, state and facility layers available.`;
}

function addHydrography(map) {
  if (!state.hydrography) return;
  const layers = state.hydrography.manifest.display_layers || {};
  if (state.hydrography.boundary?.features?.length) {
    map.addSource("basin-boundary", { type: "geojson", data: state.hydrography.boundary });
    map.addLayer({ id: "basin-boundary-fill", type: "fill", source: "basin-boundary", paint: { "fill-color": "#7bb3a5", "fill-opacity": 0.11 } });
    map.addLayer({ id: "basin-boundary-line", type: "line", source: "basin-boundary", paint: { "line-color": "#376f61", "line-width": 1.6, "line-dasharray": [2, 2] } });
    $("#toggle-boundary").disabled = false;
  }
  if (state.hydrography.flowlines?.features?.length) {
    map.addSource("nhdplus-flowlines", { type: "geojson", data: state.hydrography.flowlines });
    map.addLayer({
      id: "nhdplus-flowlines", type: "line", source: "nhdplus-flowlines",
      paint: { "line-color": ["case", [">=", ["coalesce", ["get", "streamorde"], 0], 5], "#227c9d", "#6caec2"], "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.55, 10, 2.2], "line-opacity": 0.75 },
    });
    $("#toggle-flowlines").disabled = false;
  }
  if (layers.network_flowlines) {
    $("#hydrography-note").textContent = `USGS NHDPlus HR reference · ${Number(layers.network_flowlines.feature_count || 0).toLocaleString()} network flowlines · run ${state.hydrography.manifest.run_id || "unknown"}`;
  }
}

function initMap() {
  const map = new maplibregl.Map({
    container: "map",
    style: { version: 8, glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf", sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#e8f1ee" } }] },
    center: [-107.8, 36.9], zoom: 6.3, attributionControl: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "imperial" }), "bottom-left");
  map.on("load", () => {
    addBasemap(map);
    addBasinContext(map);
    addHydrography(map);
    map.addSource("stations", { type: "geojson", data: state.features });
    map.addLayer({ id: "station-points", type: "circle", source: "stations", filter: ["!=", ["get", "station_kind"], "ditch_or_diversion"], paint: {
      "circle-radius": 6,
      "circle-color": ["match", ["get", "source_name"], "USGS", "#176c88", "New Mexico OSE/ISC MEAS", "#c96f2f", "Colorado DWR CDSS", "#6b5b95", "#4d7c62"],
      "circle-stroke-width": ["case", [">", ["coalesce", ["get", "qa_candidate_series_count"], 0], 0], 2.8, 1.2],
      "circle-stroke-color": ["match", ["get", "variable_profile"], ["stage_only", "stage_and_other", "stage_and_water_quality", "stage_and_sediment"], "#d95f02", ["discharge_only", "discharge_and_other", "discharge_and_water_quality", "discharge_and_sediment"], "#1b9e77", "stage_and_discharge", "#7570b3", "water_quality", "#e6ab02", "sediment", "#a6761d", "#666666"],
    } });
    map.addLayer({ id: "station-ditches", type: "symbol", source: "stations", filter: ["==", ["get", "station_kind"], "ditch_or_diversion"], layout: { "text-field": "◆", "text-size": 17, "text-allow-overlap": true }, paint: { "text-color": ["match", ["get", "source_name"], "USGS", "#176c88", "New Mexico OSE/ISC MEAS", "#c96f2f", "Colorado DWR CDSS", "#6b5b95", "#4d7c62"], "text-halo-color": ["match", ["get", "variable_profile"], ["stage_only", "stage_and_other", "stage_and_water_quality", "stage_and_sediment"], "#d95f02", ["discharge_only", "discharge_and_other", "discharge_and_water_quality", "discharge_and_sediment"], "#1b9e77", "stage_and_discharge", "#7570b3", "water_quality", "#e6ab02", "sediment", "#a6761d", "#666666"], "text-halo-width": 2 } });
    for (const layerId of ["station-points", "station-ditches"]) map.on("click", layerId, (event) => { const feature = event.features?.[0]; if (feature?.properties?.location_key) openStation(feature.properties.location_key); });
    map.on("mouseenter", "station-points", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "station-points", () => { map.getCanvas().style.cursor = ""; });
    map.on("mouseenter", "station-ditches", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "station-ditches", () => { map.getCanvas().style.cursor = ""; });
    updateMap(currentFilters());
    $("#map-mode").textContent = "Local MapLibre view";
  });
  map.on("error", (event) => {
    if (event.error?.message) $("#map-mode").textContent = "Map layer warning";
  });
  state.map = map;
}

function updateMap(filters) {
  const features = visibleFeatures(filters);
  if (!state.map || !state.map.isStyleLoaded()) return;
  state.map.getSource("stations")?.setData(features);
  renderCoverageMarkers(features.features);
  const narrowed = Boolean(filters.source || filters.coordinate_status || filters.search || filters.include_inactive === "true" || filters.include_non_hydro === "true");
  if (!narrowed && state.basinBounds) {
    state.map.fitBounds(state.basinBounds, { padding: 28, maxZoom: 9, duration: 450 });
    return;
  }
  if (features.features.length) {
    const coordinates = features.features.map((feature) => feature.geometry.coordinates);
    const west = Math.min(...coordinates.map(([longitude]) => longitude)); const east = Math.max(...coordinates.map(([longitude]) => longitude));
    const south = Math.min(...coordinates.map(([, latitude]) => latitude)); const north = Math.max(...coordinates.map(([, latitude]) => latitude));
    if (west !== east || south !== north) state.map.fitBounds([[west, south], [east, north]], { padding: 55, maxZoom: 11, duration: 450 });
  }
}

function styleForLocalFeature(features) {
  const types = new Set(features.map((feature) => feature?.geometry?.type));
  if (types.has("Polygon") || types.has("MultiPolygon")) return "fill";
  if (types.has("LineString") || types.has("MultiLineString")) return "line";
  return "circle";
}

function renderLocalLayerList() {
  const target = $("#local-layer-list");
  target.replaceChildren();
  for (const entry of state.localLayers) {
    const label = document.createElement("label");
    label.className = "layer-item";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = true;
    checkbox.addEventListener("change", () => entry.layerIds.forEach((id) => layerVisibility(id, checkbox.checked)));
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "text-button"; remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      entry.layerIds.forEach((id) => { if (state.map.getLayer(id)) state.map.removeLayer(id); });
      if (state.map.getSource(entry.sourceId)) state.map.removeSource(entry.sourceId);
      state.localLayers = state.localLayers.filter((item) => item !== entry); renderLocalLayerList();
    });
    const name = document.createElement("span"); name.textContent = `${entry.name} (${entry.featureCount.toLocaleString()})`;
    label.append(checkbox, name, remove); target.append(label);
  }
}

function addLocalGeoJson(name, data) {
  if (!state.map?.isStyleLoaded()) throw new Error("The map is still opening. Try the local layer again in a moment.");
  const features = data?.type === "FeatureCollection" && Array.isArray(data.features) ? data.features : null;
  if (!features) throw new Error("Local layer must be a GeoJSON FeatureCollection.");
  const token = `local-${Date.now()}-${state.localLayers.length}`;
  const layerType = styleForLocalFeature(features);
  const layerIds = [];
  state.map.addSource(token, { type: "geojson", data });
  if (layerType === "fill") {
    const fill = `${token}-fill`; const line = `${token}-line`;
    state.map.addLayer({ id: fill, type: "fill", source: token, paint: { "fill-color": "#8f6bb3", "fill-opacity": 0.18 } });
    state.map.addLayer({ id: line, type: "line", source: token, paint: { "line-color": "#69498b", "line-width": 1.8 } });
    layerIds.push(fill, line);
  } else if (layerType === "line") {
    const line = `${token}-line`; state.map.addLayer({ id: line, type: "line", source: token, paint: { "line-color": "#69498b", "line-width": 2.2 } }); layerIds.push(line);
  } else {
    const point = `${token}-point`; state.map.addLayer({ id: point, type: "circle", source: token, paint: { "circle-color": "#69498b", "circle-radius": 5, "circle-stroke-color": "#fff", "circle-stroke-width": 1 } }); layerIds.push(point);
  }
  state.localLayers.push({ sourceId: token, layerIds, name, featureCount: features.length }); renderLocalLayerList();
}

async function loadLocalGeoJson(event) {
  const [file] = event.target.files || [];
  if (!file) return;
  try {
    addLocalGeoJson(file.name, JSON.parse(await file.text()));
    $("#local-layer-status").textContent = `${file.name} is visible only in this browser session.`;
  } catch (error) {
    $("#local-layer-status").textContent = error.message;
  } finally {
    event.target.value = "";
  }
}

async function loadHydrography(releaseManifest) {
  const reference = releaseManifest.spatial_reference_layers;
  if (!reference?.path || !reference.display_layers) return;
  try {
    const manifest = await localJson(`/${reference.path}`);
    const boundaryPath = reference.display_layers.basin_boundary?.path;
    const flowlinePath = reference.display_layers.network_flowlines?.path;
    const [boundary, flowlines] = await Promise.all([boundaryPath ? localJson(`/${boundaryPath}`) : null, flowlinePath ? localJson(`/${flowlinePath}`) : null]);
    state.hydrography = { manifest, boundary, flowlines };
    const coordinates = [];
    const collect = (value) => { if (Array.isArray(value) && value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))) coordinates.push([Number(value[0]), Number(value[1])]); else if (Array.isArray(value)) value.forEach(collect); };
    for (const feature of boundary?.features || []) collect(feature?.geometry?.coordinates);
    if (coordinates.length) state.basinBounds = [[Math.min(...coordinates.map(([longitude]) => longitude)), Math.min(...coordinates.map(([, latitude]) => latitude))], [Math.max(...coordinates.map(([longitude]) => longitude)), Math.max(...coordinates.map(([, latitude]) => latitude))]];
  } catch (error) {
    $("#hydrography-note").textContent = `Hydrography reference was not loaded: ${error.message}`;
  }
}

async function refreshStations() {
  const filters = currentFilters();
  const [overview, stations, map] = await Promise.all([
    api(`/api/v1/overview${query(filters)}`),
    api(`/api/v1/stations${query({ ...filters, limit: 5000 })}`),
    api(`/api/v1/map/stations${query({ source: filters.source, coordinate_status: filters.coordinate_status, include_inactive: filters.include_inactive, include_non_hydro: filters.include_non_hydro })}`),
  ]);
  renderMetrics(overview.data); state.stations = stations.data; state.features = map.data; renderRows(state.stations); updateMap(filters); syncUrl();
}

async function boot() {
  try {
    const [release, overview, stationData, mapData, updateRuns, healthMatrix, sourceStatus] = await Promise.all([api("/api/v1/meta/release"), api("/api/v1/overview"), api("/api/v1/stations?limit=5000"), api("/api/v1/map/stations"), api("/api/v1/operations/runs"), api("/api/v1/qa/matrix?months=12"), api("/api/v1/operations/source-status")]);
    $("#release-line").textContent = `Release ${overview.release_id} · generated ${overview.generated_at_utc}`;
    state.releaseId = overview.release_id; renderMetrics(overview.data); state.stations = stationData.data; state.features = mapData.data; fillSources(state.stations); renderArchiveStatus(stationData.data, updateRuns.data, sourceStatus.data); renderHealthMatrix(healthMatrix.data); restoreUrlState(); await refreshStations();
    await Promise.all([loadHydrography(release.data), loadBasinContext()]);
    initMap();
  } catch (error) {
    $("#release-line").textContent = `Could not open local data release: ${error.message}`;
    $("#map-mode").textContent = "Release unavailable";
  }
}

for (const element of [$("#filter-source"), $("#filter-coordinate"), $("#show-inactive"), $("#show-non-hydro")]) element.addEventListener("change", () => refreshStations().catch((error) => { $("#filter-result").textContent = error.message; }));
$("#filter-search").addEventListener("input", () => refreshStations().catch((error) => { $("#filter-result").textContent = error.message; }));
$("#filter-reset").addEventListener("click", () => { $("#filter-source").value = ""; $("#filter-coordinate").value = ""; $("#filter-search").value = ""; $("#show-inactive").checked = false; $("#show-non-hydro").checked = false; refreshStations(); });
$("#map-basemap").addEventListener("change", (event) => setBasemap(event.target.value));
$("#basemap-opacity")?.addEventListener("input", (event) => setLayerOpacity(["optional-usgs-basemap"], event.target.value));
$("#toggle-flowlines").addEventListener("change", (event) => layerVisibility("nhdplus-flowlines", event.target.checked));
$("#toggle-boundary").addEventListener("change", (event) => { ["basin-boundary-fill", "basin-boundary-line", "context-basin-fill", "context-basin-line"].forEach((layerId) => layerVisibility(layerId, event.target.checked)); });
$("#toggle-context-states").addEventListener("change", (event) => { ["context-state-fill", "context-state-lines", "context-state-labels"].forEach((layerId) => layerVisibility(layerId, event.target.checked)); });
$("#toggle-context-rivers").addEventListener("change", (event) => { ["context-major-rivers", "context-river-labels"].forEach((layerId) => layerVisibility(layerId, event.target.checked)); });
$("#toggle-context-facilities").addEventListener("change", (event) => { ["context-major-facilities", "context-facility-labels"].forEach((layerId) => layerVisibility(layerId, event.target.checked)); });
$("#toggle-coverage-bars").addEventListener("change", () => updateMap(currentFilters()));
$("#local-layer-file").addEventListener("change", loadLocalGeoJson);
for (const button of document.querySelectorAll("[data-panel-toggle]")) button.addEventListener("click", () => { const panel = document.getElementById(button.dataset.panelToggle); if (!panel) return; const collapsed = panel.classList.toggle("panel-collapsed"); button.setAttribute("aria-expanded", String(!collapsed)); button.classList.toggle("active", !collapsed); });
window.addEventListener("DOMContentLoaded", boot);
