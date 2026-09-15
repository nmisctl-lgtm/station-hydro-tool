# Hydrology interpretation guide

This document defines the station-level statistics produced by Phase 1. The
outputs are descriptive and screening evidence. They do not revise provider
observations or create a regulatory allocation, ecological-flow standard, or
design-flood value without an explicit method and review.

## Dry, normal, and wet Water Years

The annual classification uses complete Water Years only. A Water Year is
October 1 through September 30 and is named for the year in which it ends.
For each complete Water Year, the tool calculates the annual mean daily
discharge. It then calculates the station's empirical quartiles:

- `dry`: annual mean discharge at or below the complete-year Q25;
- `normal`: annual mean discharge above Q25 and below Q75;
- `wet`: annual mean discharge at or above the complete-year Q75.

The labels are relative to this station's observed record. They are not the
U.S. Drought Monitor categories, a legal water-rights definition, or a claim
that a year is hydrologically normal outside the record. The exact thresholds
are written into `hydrology_summary.json` and the hydrology figure legend.

The same rule is used in the hydrology summary and in the annual runoff
planning table. A partial Water Year is not used to establish the thresholds
and is not classified as a complete-year result.

## Consistency evidence and change-point candidates

The consistency figure combines provider method/rating evidence with annual
mean discharge. Vertical dashed lines mark candidate annual mean shifts found
by the screening change-point procedure. They are review prompts, not proof of
a dam, datum, instrument, rating-curve, or operational change. The method
does not infer causation from a statistical break.

The supporting evidence comes from locally retained provider metadata,
rating-curve snapshots, field measurements, and method records when those
records are available. Missing evidence is shown as missing rather than
invented. A candidate should be investigated against the source record for
gage datum, instrumentation, observation method, rating changes, regulation,
diversions, and channel works before it is treated as a hydrologic regime
change.

## Correlation selection

The correlation table is deliberately small and hypothesis-driven:

- discharge lag 1, 7, and 30 calendar days measure short-, weekly-, and
  monthly-scale persistence without inventing values across gaps;
- daily stage–discharge pairs are evaluated on linear and positive log10
  scales because the rating relation is often nonlinear and multiplicative;
- field-measurement published-Q relative error versus time checks whether the
  difference between field observations and published discharge changes over
  the record.

These are screening associations, not causal models. The summary records the
selection rule so that later work can add rainfall, temperature, snowpack,
reservoir operations, or neighboring stations only when those data are
available and a specific management question justifies them.

## Flood-frequency result and the 1911 peak

For the current station, one provider record is flagged `HISTORIC` and is
excluded from the systematic-only screening fit. The remaining sample has 95
complete systematic annual peaks. The 1911-10-05 peak of 25,000 ft³/s is the
largest systematic observation. Under the empirical Weibull plotting position

\[
T = (n+1)/m = (95+1)/1 = 96\text{ years},
\]

so `96 yr` is correct as an empirical rank-based plotting position for this
screen. It is not evidence that the true recurrence interval is exactly 96
years, and it is not an official Bulletin 17C design flood. The fitted LP3
curve and bootstrap interval are labeled as screening outputs for the same
reason. The station output also stores the fitted-line inverse at the observed
peak. For this station that model-implied value is approximately 27,383 years;
it is a far-tail extrapolation and should not be confused with the empirical
96-year plotting position. Official use would require the full
historical/censored-peaks,
low-outlier, EMA, regional-skew, and uncertainty workflow and a reference
comparison.

## Ten-Water-Year hydrograph reference lines

The discharge panel shows daily discharge for the recent ten-Water-Year
window. The horizontal black dashed line is the mean of all valid daily
discharge values in the full daily record. The vermillion dotted line is the
mean of annual peak discharge values in the retained annual-peak record. They
are reference levels with different meanings: the first describes the full
record's typical daily scale; the second describes the average annual maximum
scale. Neither is a forecast or threshold.

## Baseflow and ecological-flow screen

The baseflow output uses a three-pass Lyne–Hollick recursive digital filter
with `alpha = 0.925`. Gaps remain missing and separated baseflow is clipped
to the observed nonnegative discharge. The annual baseflow index (BFI) is

\[
\mathrm{BFI}=\frac{\sum Q_{baseflow}}{\sum Q_{daily}}.
\]

This is a transparent hydrograph-separation indicator of delayed-flow
contribution, not a measurement of groundwater, a legally protected flow, or
an ecological-flow requirement. An ecological-flow assessment should use
seasonal habitat needs, species/life-stage requirements, connectivity,
temperature, water quality, and applicable tribal/state/federal requirements.
The current low-tail baseflow values are screening statistics only.

The method description is stored locally in this guide and the station
narrative. Its external reference basis is the USGS recursive-filter
literature: <https://www.usgs.gov/publications/optimal-hydrograph-separation-using-a-recursive-digital-filter-constrained-chemical>.

## Flow-Duration Curve

The daily FDC sorts all valid daily mean discharges and reports the discharge
that was equaled or exceeded on approximately `p%` of observed days. For
example, D90 is a lower, frequently available flow than D50. The curve is an
empirical description of the retained record; it does not preserve event
chronology and does not adjust for future climate, regulation, diversions, or
measurement uncertainty.

The definition and wording are stored locally in this guide and in each
station's generated `hydrology_interpretation.md`. The external reference
basis is the USGS flow-duration-curve publication:
<https://www.usgs.gov/publications/flow-duration-curves>.

## Annual runoff planning baselines

The planning table uses complete-Water-Year annual runoff and empirical
quantiles. P50 is the median annual runoff; P75, P90, and P95 are progressively
lower annual-runoff levels that are exceeded in approximately 75%, 90%, and
95% of complete Water Years in the observed sample. They are useful as
screening baselines for allocation scenarios, drought stress tests, and
reservoir planning, but are not guaranteed future supply and do not include
legal demand, conveyance loss, evaporation, storage, or return-flow accounting.

The companion `annual_runoff_exceedance.csv` preserves every complete
Water-Year value, its descending rank, its exceedance probability, and its
hydrologic-year class. The water-management figure presents both the
Water-Year time sequence and the ranked probability curve so that a planning
point can be traced back to the years that produced the distribution.

The companion low-flow panel reports empirical percentiles of each complete
Water Year's minimum 7-, 14-, and 30-day mean discharge. Longer windows
describe more persistent low-flow conditions and are less sensitive to a
single short dip. They are intentionally not labeled `7Q10`, `14Q10`, or
`30Q10`: a formal low-flow frequency analysis requires a fitted method and the
appropriate record and station checks.

## Further management extensions

The next useful additions should be selected by a management question and
added with explicit provenance:

1. seasonal allocation accounting using monthly volume and current-Water-Year
   departure from the historical distribution;
2. formal low-flow frequency statistics such as 7Q10 or 30Q10 where the
   official method and regulatory context are known;
3. reservoir-operation and diversion overlays, with pre/post-regulation
   comparisons when event dates and operating records are available;
4. environmental-flow scenario curves that keep ecological targets separate
   from the hydrologic BFI/FDC screens;
5. trend and nonstationarity diagnostics, including sensitivity of planning
   baselines to the chosen period and to candidate change points; and
6. drought triggers based on multi-month deficits, FDC thresholds, storage,
   and demand rather than on one statistic alone; and
7. a deferred station-specific attribution study for USGS 09367500 that
   combines the 09366500/09367500 annual-flow comparison, precipitation and
   snowpack indicators, Colorado diversion/reservoir accounting, and station
   history to separate climate drought, reach losses, and water withdrawals.

Each extension should retain the raw source, method parameters, valid sample,
and a short interpretation alongside the derived table and figure.
