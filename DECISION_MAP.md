# Station Hydro Tool — Decision Map

This map is the canonical planning artifact for the independent station-analysis tool. It is intentionally separate from the San Juan Digital Twin's institution-scale decision map.

## #1: What is the smallest external interface?

Type: Discuss

### Question

Is a normalized station ID the only required user input, with date range, data profile, live/snapshot mode, and basin-boundary options treated as optional defaults?

### Recommended answer

Yes. The normal path should be `station_id -> complete station analysis result`; advanced options should be available without making them part of the normal user workflow.

### Answer

Accepted: the normal user workflow requires only a canonical station ID. Date range, data profile, live/snapshot mode, and basin-boundary options use defaults unless explicitly supplied.

## #2: Where is the retrieval seam?

Blocked by: #1
Type: Discuss

### Question

Should the tool use mature HyRiver packages for supported USGS retrievals and a small direct-USGS adapter only where the high-level package does not expose the required metadata, gage-height history, or quality fields?

### Recommended answer

Yes. Keep one deep station-discovery interface and hide HyRiver/direct-API details behind the USGS adapter. Do not recreate a general download framework.

### Answer

Open.

## #9: What does Stage–Discharge validate?

Blocked by: #2, #3, #5
Type: Discuss

### Question

Should the Stage–Discharge product treat field measurements as ground truth and
the public daily discharge as a product to be checked, rather than fitting a
generic regression to all daily points and calling that validation?

### Recommended answer

Yes. Show all paired daily observations as the operational relationship, but
keep field measurements as a separate, visually prominent validation layer.
Compare field-measured discharge with the provider-published discharge at the
matching date/time, report residuals and stage-range coverage, and inspect
changes over time for rating shifts. A fitted curve is diagnostic only and
must not replace the official discharge series automatically.

### Answer

Accepted for the Phase 1 design.

## #3: What does “available data” mean for automatic download?

Blocked by: #1, #2
Type: Discuss

### Question

Does the tool discover and report every USGS data category, while automatically downloading core hydrology data and only downloading large or structurally different categories when explicitly enabled?

### Recommended answer

Yes. Every category belongs in the inventory; the default download profile should prioritize discharge, gage height, field measurements, peaks, and statistical daily data. Discrete samples should be discoverable and opt-in.

### Answer

Open.

## #4: What is the station data package and storage contract?

Blocked by: #2, #3
Type: Discuss

### Question

Should station observations use Parquet, metadata and provenance use JSON/CSV, and the HyRiver cache remain a working cache rather than the only archival record?

### Recommended answer

Yes. Avoid a multi-station SQLite warehouse in the standalone tool. Keep a compact run manifest, retrieval timestamps, source URLs, and data-version labels.

### Answer

Open.

## #5: What is the minimum analysis and visualization contract?

Blocked by: #3, #4
Type: Discuss

### Question

Which coverage, completeness, quality, hydrograph, seasonal-distribution, and stage-discharge outputs are mandatory for the first station plugin?

### Recommended answer

Start with the existing validated Phase 1 figure requirements, but emit only PNG/SVG plus numeric evidence files. No static HTML or PDF report is required.

### Answer

Open.

## #6: What watershed boundary does the tool return?

Blocked by: #1, #2
Type: Research | Discuss

### Question

Should the default geometry be the NLDI contributing watershed associated with the USGS station, with source, CRS, geometry validity, and area comparison recorded?

### Recommended answer

Yes. Call it a contributing watershed boundary, not an unspecified basin boundary, and preserve both provider-reported drainage area and geometry-derived area.

### Answer

Open.

## #7: How does the standalone tool become a platform plugin?

Blocked by: #1, #4, #5, #6
Type: Discuss

### Question

Should the basin Overview pass a canonical provider/station ID to a new station-analysis window, while communicating through a small JSON/URL contract instead of importing the plugin's internal modules or database?

### Recommended answer

Yes. The first integration should be a new-window deep link. The contract should support both live analysis and release/snapshot-pinned analysis so the station view cannot silently disagree with the Overview's data version.

### Answer

Open.

## #8: What proves the first version is ready?

Blocked by: #1, #2, #3, #4, #5, #6, #7
Type: Discuss

### Question

What numerical comparisons, empty-data behavior, metadata checks, basin-geometry checks, and visual checks must pass for USGS 09342500 before adding another provider or station?

### Recommended answer

Use 09342500 as the golden station. Compare discovered metadata and coverage with the USGS page, compare downloaded counts and date bounds with the existing validated data, verify all mandatory figures, and test both fresh and cached runs.

### Answer

Open.
