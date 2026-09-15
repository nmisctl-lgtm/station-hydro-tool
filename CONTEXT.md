# Station Hydro Tool Context

This context defines the language for the independent station-analysis tool. It describes the domain only; implementation choices belong in the decision map and later code.

## Station and discovery

**Station**:
A provider-defined monitoring location identified by a canonical provider and station ID, such as `USGS:09342500`.

**Station request**:
The user's request to analyze one station. A canonical station ID is required; all other options have tool-defined defaults.

**Available data**:
The provider's declared inventory of data categories, variables, frequencies, units, and date ranges for a station, whether or not each category is downloaded by default.

**Coverage**:
The time interval and observation presence information for one available data series. It distinguishes provider-declared dates from dates actually retained locally.

**Location details**:
Descriptive metadata for a station, including coordinates, station type, administrative identifiers, elevation, drainage area, datum, and time-zone information.

## Hydrology and spatial context

**Core hydrology data**:
The default station observations needed for routine hydrologic review: discharge, gage height, field measurements, peak measurements, and relevant statistical daily data.

**Ground-truth field measurement**:
An independently measured discharge and/or gage height observation collected in
the field. It is retained as a separate validation layer and is not merged into
the public daily series.

**Stage–Discharge validation**:
The comparison of field measurements and provider-published discharge, together
with the all-daily-pair operational relationship. It is used to identify bias,
outliers, rating shifts, weak stage-range coverage, and extrapolation risk; it
does not silently replace the provider series.

**Contributing watershed boundary**:
The upstream watershed geometry associated with the station's monitoring location. It is the default spatial boundary returned by the station tool.

**Station analysis package**:
The complete result for one station: discovery metadata, observations, coverage, quality summary, figures, spatial boundary, and provenance.

## Operation and integration

**Live analysis**:
A station analysis refreshed from the provider or local cache at run time, with its retrieval time and data version shown.

**Snapshot analysis**:
A station analysis pinned to a named local data version or platform release so results remain consistent with the caller's view.

**Station-analysis plugin**:
The independently runnable station analysis module exposed through a small interface so a basin Overview can open or call it by station ID.
