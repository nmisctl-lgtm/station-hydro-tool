/* Dynamic local Overview: no station data or rendered figures are bundled. */
import * as maplibregl from "/static/vendor/maplibre-gl.mjs";

const $ = (selector) => document.querySelector(selector);
const state = { map: null, stations: [], features: { type: "FeatureCollection", features: [] } };
const BASEMAPS = {
  local: { tiles: null, note: "Local station coordinates only." },
  topo: { tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"], note: "Online optional USGS topographic basemap." },
  imagery: { tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"], note: "Online optional USGS imagery and topographic basemap." },
  terrain: { tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}"], note: "Online optional USGS shaded-relief basemap." },
};

async function api(path, options) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || payload.error?.message || `Request failed (${response.status})`);
  return payload.data ?? payload;
}

function escapeHtml(value) {
  return String(value ?? "—").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function prettyName(value) {
  const raw = String(value || "").trim();
  if (!raw || raw !== raw.toUpperCase()) return raw || "—";
  return raw.toLowerCase().replace(/\b\w/g, (match) => match.toUpperCase()).replace(/\b(Nr|At|Of|The|And|On|To|In)\b/g, (match) => match.toLowerCase()).replace(/\bUsgs\b/g, "USGS").replace(/\bCo\b/g, "CO");
}

function currentFilters() {
  return {
    source: $("#filter-source").value,
    coordinate: $("#filter-coordinate").value,
    search: $("#filter-search").value.trim().toLowerCase(),
  };
}

function visibleStations() {
  const filters = currentFilters();
  return state.stations.filter((station) => {
    const matchesText = !filters.search || [station.location_key, station.provider_station_id, station.display_name]
      .some((value) => String(value || "").toLowerCase().includes(filters.search));
    return (!filters.source || station.source_name === filters.source)
      && (!filters.coordinate || station.coordinate_status === filters.coordinate)
      && matchesText;
  });
}

function renderMetrics(overview) {
  $("#metric-stations").textContent = Number(overview.registered_location_count || 0).toLocaleString();
  $("#metric-mapped").textContent = Number(overview.mapped_location_count || 0).toLocaleString();
  $("#metric-coordinate-review").textContent = Number(overview.coordinate_review_count || 0).toLocaleString();
  $("#metric-daily").textContent = "local only";
  $("#release-line").textContent = overview.registered_location_count
    ? `${overview.registered_location_count} locally cached station${overview.registered_location_count === 1 ? "" : "s"} · live public-data workflow`
    : "No station package is cached yet · enter a USGS ID to begin";
}

function openStation(locationKey) {
  const stationId = locationKey.split(":").at(-1);
  window.location.assign(`/station/USGS/${encodeURIComponent(stationId)}?station=${encodeURIComponent(locationKey)}`);
}

function renderRows() {
  const stations = visibleStations();
  $("#station-rows").innerHTML = stations.map((station) => `<tr tabindex="0" data-station-key="${escapeHtml(station.location_key)}"><td><span class="station-name">${escapeHtml(prettyName(station.display_name))}</span><span class="station-key">${escapeHtml(station.location_key)}</span></td><td>${escapeHtml(station.source_name)}</td><td>stream gage</td><td><span class="profile-chip">local package</span><span class="station-key">${escapeHtml(station.activity_status)}</span></td><td><span class="status-dot ${station.coordinate_status === "valid" ? "coordinate-valid" : "coordinate-review"}"></span>${station.coordinate_status === "valid" ? "Mapped" : "Needs review"}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">No matching local station package. Enter a USGS station ID above to create one.</td></tr>';
  for (const row of document.querySelectorAll("#station-rows [data-station-key]")) {
    const open = () => openStation(row.dataset.stationKey);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
  }
  $("#filter-result").textContent = `${stations.length.toLocaleString()} locally cached station${stations.length === 1 ? "" : "s"} shown`;
}

function visibleFeatures() {
  const allowed = new Set(visibleStations().map((station) => station.location_key));
  return { type: "FeatureCollection", features: state.features.features.filter((feature) => allowed.has(feature.properties?.location_key)) };
}

function updateMap() {
  const features = visibleFeatures();
  if (!state.map || !state.map.isStyleLoaded()) return;
  state.map.getSource("stations")?.setData(features);
  if (features.features.length === 1) state.map.flyTo({ center: features.features[0].geometry.coordinates, zoom: 9 });
}

function setBasemap(value) {
  const choice = BASEMAPS[value] || BASEMAPS.local;
  const source = state.map?.getSource("optional-usgs-basemap");
  if (source && choice.tiles) source.setTiles(choice.tiles);
  if (state.map?.getLayer("optional-usgs-basemap")) state.map.setLayoutProperty("optional-usgs-basemap", "visibility", choice.tiles ? "visible" : "none");
  $("#map-basemap-note").textContent = choice.note;
}

function initMap() {
  const map = new maplibregl.Map({
    container: "map",
    style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#e8f1ee" } }] },
    center: [-107.8, 36.9], zoom: 6.3, attributionControl: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "imperial" }), "bottom-left");
  map.on("load", () => {
    map.addSource("optional-usgs-basemap", { type: "raster", tiles: BASEMAPS.terrain.tiles, tileSize: 256, attribution: "USGS The National Map" });
    map.addLayer({ id: "optional-usgs-basemap", type: "raster", source: "optional-usgs-basemap", paint: { "raster-opacity": 0.62 } });
    map.addSource("stations", { type: "geojson", data: state.features });
    map.addLayer({ id: "station-points", type: "circle", source: "stations", paint: { "circle-radius": 6, "circle-color": "#176c88", "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.3 } });
    map.on("click", "station-points", (event) => { const key = event.features?.[0]?.properties?.location_key; if (key) openStation(key); });
    map.on("mouseenter", "station-points", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "station-points", () => { map.getCanvas().style.cursor = ""; });
    $("#map-mode").textContent = "Local dynamic map";
    updateMap();
  });
  state.map = map;
}

async function refreshOverview() {
  const [overview, stations, features] = await Promise.all([
    api("/api/v1/overview"),
    api("/api/v1/stations?limit=5000"),
    api("/api/v1/map/stations"),
  ]);
  state.stations = stations;
  state.features = features;
  const source = $("#filter-source");
  const selected = source.value;
  const sources = [...new Set(stations.map((station) => station.source_name).filter(Boolean))].sort();
  source.replaceChildren(new Option("All sources", ""), ...sources.map((item) => new Option(item, item)));
  source.value = sources.includes(selected) ? selected : "";
  renderMetrics(overview);
  renderRows();
  updateMap();
  $("#archive-status-rows").innerHTML = '<tr><td colspan="7" class="muted">No data release is bundled. Source status is retained inside each locally created station package.</td></tr>';
  $("#update-run-note").textContent = "Station retrieval and analysis occur only when you request a station.";
  $("#health-matrix-rows").innerHTML = '<tr><td class="muted">Open a station to inspect its coverage and QA/QC dynamically.</td></tr>';
}

function normalizedStationId(value) {
  const match = String(value || "").trim().match(/(?:USGS[-:\s]*)?(\d{8,15})$/i);
  return match?.[1] || null;
}

async function openRequestedStation(event) {
  event.preventDefault();
  const stationId = normalizedStationId($("#station-id").value);
  if (!stationId) { $("#station-open-status").textContent = "Enter a valid numeric USGS station ID."; return; }
  const withContinuous = $("#with-continuous").checked;
  $("#station-open-status").textContent = "Downloading public USGS data and building the local station package…";
  const button = $("#station-open-form button");
  button.disabled = true;
  try {
    await api(`/api/v1/stations/${encodeURIComponent(stationId)}/run?refresh=false&with_continuous=${withContinuous}`, { method: "POST" });
    openStation(`USGS:${stationId}`);
  } catch (error) {
    $("#station-open-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function bindControls() {
  $("#station-open-form").addEventListener("submit", openRequestedStation);
  for (const element of [$("#filter-source"), $("#filter-coordinate")]) element.addEventListener("change", () => { renderRows(); updateMap(); });
  $("#filter-search").addEventListener("input", () => { renderRows(); updateMap(); });
  $("#filter-reset").addEventListener("click", () => { $("#filter-source").value = ""; $("#filter-coordinate").value = ""; $("#filter-search").value = ""; renderRows(); updateMap(); });
  $("#map-basemap").addEventListener("change", (event) => setBasemap(event.target.value));
  $("#basemap-opacity").addEventListener("input", (event) => { if (state.map?.getLayer("optional-usgs-basemap")) state.map.setPaintProperty("optional-usgs-basemap", "raster-opacity", Number(event.target.value)); $("#basemap-opacity-value").textContent = `${Math.round(Number(event.target.value) * 100)}%`; });
  for (const button of document.querySelectorAll("[data-panel-toggle]")) button.addEventListener("click", () => { const panel = document.getElementById(button.dataset.panelToggle); if (!panel) return; const collapsed = panel.classList.toggle("panel-collapsed"); button.setAttribute("aria-expanded", String(!collapsed)); button.classList.toggle("active", !collapsed); });
}

window.addEventListener("DOMContentLoaded", async () => {
  bindControls();
  initMap();
  try { await refreshOverview(); } catch (error) { $("#release-line").textContent = `Could not read the local station cache: ${error.message}`; $("#map-mode").textContent = "Unavailable"; }
});
