# San Juan Station Explorer

`station-hydro-tool` is a code-only, local-first application for exploring one
USGS hydrologic station at a time. Enter a station ID such as `09342500` in the
Overview, and the tool retrieves public data to the computer on which it is
running, then opens an interactive station page.

The Overview can run in either mode: it maps only the station packages created
on that computer, or it reads a local basin Overview display release when one
has been transferred into `data/overview/`. The latter supplies the basin
station register, GIS layers, and release evidence; it does not load the
parent project's large analytical SQLite database.

## Quick start on a clean machine

Python 3.12 or 3.13 is required. `uv` is recommended because `uv.lock` records
the tested dependency resolution.

```bash
git clone https://github.com/nmisctl-lgtm/station-hydro-tool.git
cd station-hydro-tool
uv sync --extra test
uv run pytest
uv run station-hydro serve
```

Open <http://127.0.0.1:8765/>, enter a USGS station ID, and select **Open
station**. The first request downloads that station's public metadata and
observations into the local `data/` directory. Subsequent visits use the local
package. Opening a station checks and refreshes its daily USGS data at most
once per 24 hours; the result is recorded in that package's
`metadata/daily_update_state.json`. `--refresh` requests a new provider
snapshot from the command line.

If a local transfer bundle includes a basin Overview, extract it at the project
root. The server automatically selects the newest directory under
`data/overview/`; it can also be selected explicitly:

```bash
uv run station-hydro serve \
  --overview-dir data/overview/basin-overview-20260827-huc4-1408
```

For a terminal-only workflow:

```bash
uv run station-hydro run 09342500
uv run station-hydro run 09342500 --skip-continuous
```

The normal browser path does not generate image files. It reads the local
station package through JSON endpoints and renders interactive SVG charts in
the browser. Continuous stage/discharge retrieval is optional in the Overview;
choose it when stage and stage-discharge analysis are needed.

## What a station package contains locally

- station identity, coordinates, drainage area, elevation, HUC, datum, source
  links, and the provider-declared Available Data inventory;
- raw public responses, retrieval manifests, normalized observations, and the
  contributing watershed boundary;
- coverage, completeness, QA/QC and derived hydrology summaries; and
- data served to the browser for the station's hydrograph, flow-duration curve,
  annual and seasonal summaries, baseflow, stage-discharge, and related views.

Water Year is only a derived grouping: October 1 through September 30, named
for the ending calendar year. Provider catalog entries labelled “Water Year”
remain metadata; they are not treated as an observation series.

## Browser/API boundary

The no-build frontend lives in `web/`. It contains an Overview and a dynamic
single-station page:

```text
GET  /                                  local Overview
POST /api/v1/stations/{station_id}/run  create or update one local package
GET  /station/USGS/{station_id}         dynamic station page
GET  /api/v1/overview                   local catalog summary
GET  /api/v1/stations                   locally cached station register
GET  /api/v1/map/stations               local station GeoJSON
GET  /api/v1/meta/release               local Overview release metadata
GET  /api/v1/context/overview            local basin GIS context index
GET  /api/v1/stations/{location_key}/analysis
GET  /api/v1/stations/{location_key}/hydrology
GET  /api/v1/stations/{location_key}/local-daily
```

The station page never reads pre-rendered analysis PNG/SVG files. `web/station.js`
requests its data from these endpoints and draws the charts directly in the
browser. The same URL/API seam can later be opened by a full-basin application.

## Commands

```text
station-hydro discover STATION_ID              # metadata and Available Data
station-hydro fetch STATION_ID                 # default observations
station-hydro fetch STATION_ID --with-continuous
station-hydro analyze STATION_ID               # QA/QC and hydrology tables
station-hydro basin STATION_ID                 # contributing watershed
station-hydro run STATION_ID                   # build/update local package
station-hydro serve                            # local API and browser UI
station-hydro plot STATION_ID                  # optional offline image export
```

`plot` is an explicit archival/export command only; the normal `run` and
browser workflows do not call it.

## Data policy

| Kept in GitHub | Created locally and ignored by Git |
| --- | --- |
| Source code, tests, documentation, dependency lockfile, frontend scripts and MapLibre runtime | `data/` raw responses, Parquet observations, provider metadata and GeoJSON boundaries |
| Calculation definitions and provider URLs | `outputs/` optional exported PNG/SVG charts and manifests |
| Small synthetic test fixtures only | HTTP/HyRiver caches, virtual environments, Matplotlib caches |

No station data, basin data, downloaded boundaries, cached API payloads, or
generated visualizations are uploaded to this repository. Each local package
records retrieval time, source URL, date window, profile, and file checksums so
its locally derived analysis remains traceable.

## Project layout

```text
src/station_hydro/
  discovery.py           metadata and Available Data discovery
  retrieval.py           daily and optional continuous retrieval
  basin.py               watershed and network retrieval
  quality.py             non-destructive QA/QC summaries
  hydrology.py           derived Water-Year and station statistics
  package_reader.py      local package-to-JSON data adapter
  basin_overview.py       read-only adapter for the Overview display release
  presentation.py         Overview and station-page view models
  webapp.py              local REST API and static-file server
  cli.py                 command-line entry point
web/
  index.html, app.js     local Overview
  station.html, station.js dynamic station experience
tests/                   provider-independent unit and web-contract tests
```

The implementation uses HyRiver where it provides a mature NWIS/NLDI seam and
uses narrow direct-USGS adapters only for provider catalog details outside that
interface. There is no PDF or static HTML reporting workflow in the normal
product path.
