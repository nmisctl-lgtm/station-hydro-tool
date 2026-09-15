# Station Hydro Tool

`station-hydro-tool` is a standalone station package for USGS hydrology. The
normal input is one station ID, for example `09342500`. The tool discovers the
station's metadata and provider-declared Available Data, downloads the default
core observations, retrieves the contributing watershed boundary, runs the
station quality checks, and generates the analysis figures used by the local
web application.

The project is intentionally separate from the San Juan Digital Twin. It is a
small station-level product today and exposes a stable HTTP seam for a future
whole-basin overview to open a station detail view.

## Quick start on a clean machine

Python 3.12 or 3.13 is required. `uv` is recommended because `uv.lock` records
the tested dependency resolution; ordinary `pip` remains supported.

```bash
git clone https://github.com/nmisctl-lgtm/station-hydro-tool.git
cd station-hydro-tool
uv sync --extra test
uv run pytest
uv run station-hydro run 09342500
uv run station-hydro serve
```

Open <http://127.0.0.1:8765/> and enter a station ID. The first run contacts
public USGS services and may take a few minutes because it retrieves the full
declared daily-discharge history and watershed context. Later runs reuse the
local package; use the **Refresh data** button or `--refresh` to request a new
provider snapshot.

Without `uv`:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/station-hydro run 09342500
.venv/bin/station-hydro serve
```

The default station workflow includes available continuous stage/discharge
observations because they support the hydrograph and stage-discharge views. If
you are doing a metadata-only or daily-only pass, skip that retrieval:

```bash
uv run station-hydro run 09342500 --skip-continuous
```

## What the station ID workflow returns

- station identity, coordinates, drainage area, elevation, HUC, datum, and
  provider source links;
- the complete provider-declared Available Data inventory, including coverage
  windows and whether the default profile downloaded a series;
- raw provider responses and retrieval manifests;
- normalized Parquet observations for the selected profile;
- a local GeoJSON contributing watershed boundary and flow-network context;
- non-destructive completeness, gap, duplicate, negative-value, quality-code,
  and robust outlier checks;
- English PNG/SVG figures for coverage, completeness, normalized monthly
  discharge, the recent hydrograph, stage-discharge evidence, and supporting
  station hydrology summaries.

The coverage and completeness figures focus on the core/supporting analysis
series; the Available Data table and JSON response retain the full provider
inventory, including catalog-only categories.

Water Year is used only as a derived analysis grouping: October 1 through
September 30, named for the ending calendar year. Provider catalog entries
labelled “Water Year” remain inventory metadata and are not treated as a
separate observation series.

## Commands

```text
station-hydro discover STATION_ID              # metadata and Available Data
station-hydro fetch STATION_ID                 # default observations
station-hydro fetch STATION_ID --with-continuous
station-hydro analyze STATION_ID               # quality and hydrology tables
station-hydro basin STATION_ID                 # contributing watershed
station-hydro plot STATION_ID                  # figures and figure manifest
station-hydro run STATION_ID                   # complete cached workflow
station-hydro serve                            # REST API and browser UI
```

Every station command accepts `--refresh`, `--data-dir`, and `--output-dir`.
The default profile downloads the core daily and continuous hydrology series,
field measurements, peaks, ratings, watershed boundary, quality tables, and
figures. Other provider categories remain visible in the complete inventory
and are not silently treated as core observations.

## Application boundary

The service layer in `src/station_hydro/service.py` is the shared contract for
the CLI, browser UI, and future basin-platform plugin. The API is served by
`src/station_hydro/webapp.py`:

```text
GET  /api/v1/health
GET  /api/v1/stations/{station_id}
POST /api/v1/stations/{station_id}/run?refresh=false&with_continuous=true
GET  /api/v1/stations/{station_id}/basin
GET  /api/v1/stations/{station_id}/figures/{file_name}
GET  /station/USGS/{station_id}
```

The future whole-basin overview only needs to pass a station ID and open
`/station/USGS/{station_id}` or call the JSON endpoint. It does not need to
know the local data layout or import the analysis modules.

## Data and chart synchronization

The repository follows a reproducible-build model rather than committing a
large station mirror:

| GitHub contains | Generated locally and ignored by Git |
| --- | --- |
| source code, tests, configuration, docs, `pyproject.toml`, `uv.lock` | `data/` raw responses, Parquet observations, GeoJSON boundaries |
| small test fixtures when needed | `outputs/` PNG/SVG figures and manifests |
| provider URLs and calculation definitions | HyRiver/HTTP caches, virtual environments, Matplotlib caches |

Each station package writes source manifests with retrieval time, source URL,
date window, profile, and file-level checksums. Therefore another machine can
rebuild the latest public snapshot from the same code and inspect exactly what
was used for a local analysis. Rebuilding the latest snapshot is not expected
to produce byte-identical observations if a provider revises its historical
records; the manifest makes that change visible.

For an exact frozen snapshot later, the intended extension is a versioned
GitHub Release asset or public object-storage archive referenced by a manifest.
Large raw data and generated figures do not belong in ordinary Git history.

## Project layout

```text
src/station_hydro/
  models.py              station and Available Data contracts
  discovery.py           HyRiver discovery plus narrow USGS adapters
  retrieval.py           daily, annual-peak, and optional continuous data
  basin.py               contributing watershed and network retrieval
  quality.py             non-destructive QA/QC summaries and flags
  hydrology.py           derived Water-Year and station statistics
  plots.py               required figures and PNG preflight
  service.py             application-facing station snapshot contract
  webapp.py              REST API and static browser application
  cli.py                 command-line entry point
web/                     no-build frontend served by the API
tests/                   provider-independent unit and web-contract tests
```

The implementation uses HyRiver where it provides a mature NWIS/NLDI seam and
keeps narrow direct-USGS adapters only for provider catalog details that are not
available through the high-level interface. No PDF or HTML report generator is
part of this standalone project.
