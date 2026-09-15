const form = document.querySelector("#station-form");
const stationInput = document.querySelector("#station-id");
const refreshButton = document.querySelector("#refresh-button");
const statusNode = document.querySelector("#status");
const content = document.querySelector("#station-content");

function stationIdFromPath() {
  const match = window.location.pathname.match(/\/station\/USGS\/(\d+)/i);
  return match ? match[1] : null;
}

function setStatus(message, kind = "") {
  statusNode.textContent = message;
  statusNode.className = `status ${kind}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, { maximumFractionDigits: digits }) : escapeHtml(value);
}

function renderSummary(snapshot) {
  const station = snapshot.station;
  const available = snapshot.available_data || [];
  const core = available.filter((item) => item.analysis_role === "core").length;
  const downloaded = available.filter((item) => item.local_status === "downloaded" || item.downloaded).length;
  const firstCoverage = snapshot.coverage?.[0] || {};

  document.querySelector("#station-heading").innerHTML = `
    <p class="eyebrow">USGS ${escapeHtml(station.station_id)}</p>
    <h2>${escapeHtml(station.name || "Unnamed station")}</h2>
    <p class="muted">${escapeHtml(station.source_url || "")}</p>`;

  document.querySelector("#summary-cards").innerHTML = [
    ["Coordinates", `${formatNumber(station.latitude, 5)}, ${formatNumber(station.longitude, 5)}`],
    ["Drainage area", `${formatNumber(station.drainage_area_sq_mi, 1)} mi²`],
    ["Inventory series", `${formatNumber(available.length, 0)} (${core} core)`],
    ["Downloaded series", `${formatNumber(downloaded, 0)}`],
    ["Latest declared date", firstCoverage.declared_end || station.retrieved_at || "—"],
  ].map(([label, value]) => `<div class="card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");

  const locationFields = [
    ["Station ID", station.station_id],
    ["Site type", station.site_type || station.site_type_code],
    ["HUC", station.huc_code],
    ["County code", station.county_code],
    ["Elevation", `${formatNumber(station.elevation_ft, 1)} ft ${station.elevation_datum || ""}`],
    ["Coordinate datum", station.coordinate_datum],
    ["Timezone", station.timezone],
    ["Retrieved", station.retrieved_at],
  ];
  document.querySelector("#location-details").innerHTML = locationFields.map(([label, value]) =>
    `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value || "—")}</dd></div>`).join("");

  const files = snapshot.observations || [];
  const basin = snapshot.spatial?.contributing_watershed || {};
  document.querySelector("#observation-files").innerHTML = `
    <h3>Local observation files</h3>
    ${files.length ? `<ul>${files.map((file) => `<li>${escapeHtml(file.name)} <span class="muted">(${formatNumber(file.bytes, 0)} bytes)</span></li>`).join("")}</ul>` : `<p class="muted">No local observation files yet.</p>`}
  `;
  document.querySelector("#basin-details").innerHTML = `
    <h3>Contributing watershed</h3>
    <p>${basin.available ? `<a href="${basin.url}" target="_blank" rel="noreferrer">Open GeoJSON boundary</a>` : "Not downloaded yet."}</p>
  `;

  const columns = ["variable", "frequency", "parameter_code", "unit", "declared_start", "declared_end", "local_status"];
  document.querySelector("#availability-table").innerHTML = `
    <thead><tr>${columns.map((column) => `<th>${escapeHtml(column.replaceAll("_", " "))}</th>`).join("")}</tr></thead>
    <tbody>${available.map((item) => `<tr>${columns.map((column) => `<td>${escapeHtml(item[column] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody>
  `;

  const figures = snapshot.figures || {};
  document.querySelector("#figures").innerHTML = Object.entries(figures).map(([key, urls]) => {
    const title = key.replaceAll("_", " ");
    const image = urls.png || urls.svg;
    return `<figure><a href="${image}" target="_blank" rel="noreferrer"><img loading="lazy" src="${urls.png || urls.svg}" alt="${escapeHtml(title)}"></a><figcaption>${escapeHtml(title)}</figcaption></figure>`;
  }).join("") || `<p class="muted">No generated figures yet.</p>`;
}

async function loadStation({ run = true, refresh = false } = {}) {
  const stationId = stationInput.value.trim();
  if (!stationId) return;
  setStatus(run ? "Discovering metadata and building the station package…" : "Reading local station package…");
  refreshButton.disabled = true;
  try {
    const endpoint = run
      ? `/api/v1/stations/${encodeURIComponent(stationId)}/run?refresh=${refresh}&with_continuous=true`
      : `/api/v1/stations/${encodeURIComponent(stationId)}`;
    let response = await fetch(endpoint, { method: run ? "POST" : "GET" });
    if (!response.ok && !run && response.status === 404) {
      setStatus("No local package found; building it from public USGS data…");
      response = await fetch(
        `/api/v1/stations/${encodeURIComponent(stationId)}/run?refresh=false&with_continuous=true`,
        { method: "POST" },
      );
    }
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Station request failed");
    renderSummary(payload);
    content.hidden = false;
    window.history.replaceState({}, "", `/station/USGS/${stationId}`);
    setStatus("Station package ready.", "ok");
  } catch (error) {
    content.hidden = true;
    setStatus(error.message, "error");
  } finally {
    refreshButton.disabled = false;
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadStation({ run: true, refresh: false });
});

refreshButton.addEventListener("click", () => loadStation({ run: true, refresh: true }));

const initialStation = stationIdFromPath();
if (initialStation) {
  stationInput.value = initialStation;
  loadStation({ run: false });
}
