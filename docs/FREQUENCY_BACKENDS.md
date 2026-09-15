# Flood-frequency backend policy

The station package keeps the default flood-frequency result reproducible and
local. It currently produces a systematic-only, at-site Log-Pearson III
screening curve with a seeded parametric bootstrap interval. The result is
explicitly not an official design-flood publication.

## AquaScope

`station_hydro.frequency_backends.run_aquascope_lp3` is a lazy optional adapter.
The core package does not require AquaScope, and it does not silently fall
back to another implementation when AquaScope is unavailable. Install and
validate the optional package in a separate environment before using it:

```bash
python -m pip install aquascope
```

AquaScope is useful as a Python-native comparison backend for LP3/GEV and
other hydrologic analyses. Its documented convenience API accepts a discharge
`Series` with a `DatetimeIndex` and returns return-period estimates. The
adapter preserves missing LP3 confidence intervals as missing values; it does
not manufacture uncertainty that the backend did not return.

Before making AquaScope the default, compare a fixed annual-peak fixture across
the station-tool implementation, AquaScope, and an official/reference result.
The comparison must record: log transform, skew treatment, historical or
censored peaks, low-outlier rules, confidence-interval procedure, and the
exact package versions.

## MOVE3

MOVE3 solves a different problem. It implements MOVE.1/MOVE.3 record extension
for a short record using a correlated long-record index station. It should be
introduced later as an explicit `record_extension` step, only when the user
provides a defensible index-station pair and correlation diagnostics. It is not
a replacement for the current single-station annual-maximum frequency fit.

## Reference boundary

Official USGS PeakFQ/HEC-SSP-style procedures remain the acceptance reference
for any result used as a design value. A third-party Python result is a
screening or comparison result until it reproduces the reference fixture and
its assumptions are reviewed.

## Current decision

The default pipeline remains independent of both optional packages. This keeps
metadata discovery, raw-data retention, QA/QC, and the core figures usable on a
clean installation while leaving a clear seam for later backend comparison.
