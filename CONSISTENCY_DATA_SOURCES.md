# Consistency and representativeness evidence plan

The station tool treats a statistical discontinuity as a review candidate, not
as proof of a datum change, rating change, dam, or diversion. Causal labels are
added only when a source record supports them.

## Evidence acquisition

| Question | Primary source and acquisition | Local artifact / use |
| --- | --- | --- |
| What is the current site location, coordinate datum, elevation datum, drainage area, and instrument code? | USGS monitoring-location/site service, retrieved during `discover`. | `metadata/station_metadata.json` and raw `raw/site_info.rdb`; current-state metadata only. |
| What field methods, controls, measurement ratings, agencies, approval states, and vertical datums were recorded? | USGS OGC `field-measurements` collection, retrieved by station and parameter code. | `raw/field_measurements_*.json` and `observations/field_measurements.parquet`; method/control time spans are summarized in `hydrology/method_evidence_summary.csv`. |
| What annual peak values and qualification codes exist? | USGS OGC `peaks` collection, parameter code `00060`, retrieved by `monitoring_location_id`. | `raw/peak_annual_full.rdb` (legacy cache name retained for compatibility) and `observations/annual_peak_discharge.parquet`; quality codes remain attached to every observation. |
| Did the published rating change? | Query the USGS Water Data STAC `ratings` collection for the station, then retain the `base`, `corr`, and `exsa` rating assets and their item metadata. | `raw/ratings/` snapshots plus `metadata/rating_curves.csv` and `metadata/rating_version_summary.csv`; current effective/reference dates are parsed when present. Historical versions still require successive snapshots or station records. |
| Did a datum, instrument, or operating method change outside the field records? | Review station-page remarks, revision history, station manuscript/annual station analysis files, and USGS office documentation. Automate downloads where stable; retain a manually reviewed event register for narrative documents. | Planned `metadata/station_events.csv` with `event_type`, date or interval, description, source URL/file, evidence excerpt, reviewer, and confidence. |
| Was there a dam, diversion, channel, or other engineering intervention? | Search authoritative project-owner/state/federal records (for example BOR, USACE, state dam-safety records, and National Inventory of Dams) by station watershed and time; corroborate with USGS remarks. | Planned `metadata/engineering_events.csv`; geometry and date are required before an event is linked to a flow change. |
| Is there a statistical discontinuity even if no source event is found? | Compute robust change-point candidates from the local daily/annual series and compare distributions before and after each candidate. | `hydrology/change_point_candidates.csv`; every row is explicitly a candidate requiring source review. |

## Evidence rules

1. Current metadata is not historical metadata. A current datum field cannot be
   used to claim that the datum was unchanged throughout the record.
2. A field-method frequency change is a signal. It is not, by itself, proof of
   a rating-curve shift.
3. Annual-peak qualifiers such as `DATUMCHANGE`, `REGULATED`, `HISTORIC`,
   `ESTIMATED`, and `REVISED` are preserved and should be used as evidence
   flags, not silently filtered out.
4. A change-point result is descriptive until it is matched to a dated source
   event. The tool will show the before/after sample sizes, means, ratios, and
   standardized shift so that the review is reproducible.
5. A causal event table and the computed flow series must remain separate. An
   event can explain a candidate, but it must not rewrite the observed USGS
   series.

## Official references

- [USGS field-measurements schema](https://api.waterdata.usgs.gov/ogcapi/v0/collections/field-measurements/schema?f=html)
- [USGS modernized API migration guide](https://api.waterdata.usgs.gov/docs/ogcapi/migration/)
- [USGS Water Data STAC ratings collection documentation](https://api.waterdata.usgs.gov/docs/stac)
- [HyRiver PyNHD documentation](https://docs.hyriver.io/readme/pynhd.html)
