# Study limitations log

Each entry records what the limitation is, how it affects the results, how the pipeline makes it visible, and the planned fix. Update the status when a fix ships.

## L1. WONDER birth data covers only large counties. Status: OPEN
- **What:** CDC WONDER Natality names only counties with a 2010 population of 100k or more. Smaller counties are pooled as "Unidentified Counties, TX" (FIPS 48999) and can't be assigned to a DSHS region.
- **Effect:** region birth totals and preterm rates describe the region's **large counties only**. Coverage varies a lot by region: it's high in Region 7 and Region 6/5S and low in the Panhandle and West Texas. Rural births are under-represented.
- **Visible in:** the `counties_reported` / `counties_in_region` columns of the region file, and `region_coverage.unassigned_births` / `unassigned_share` in the manifest.
- **Fix:** all-county data from DSHS vital statistics (request drafted in `reference/dshs_data_request.md`).

## L2. Region heat exposure is a simple (unweighted) county average. Status: OPEN
- **What:** the region's extreme-heat-days value is the plain mean of its counties' values. Every county counts equally, whatever its number of births.
- **Effect:** exposure is mis-assigned whenever heat differs between low-birth and high-birth counties. Example: in Region 7, Travis (about 10 extreme-heat days, ~1,000 births) and Blanco (about 2 days, ~10 births) average to **6.0 days**. Weighted by births it's **9.9 days**. In regions with many small rural counties and one or two cities, the simple mean mostly reflects rural land area, not where births happened. This misclassification usually pulls an estimated heat–preterm association toward zero, but its direction isn't guaranteed.
- **Partial mitigation (now):** a second column, `extreme_heat_days_mean_birth_counties`, averages only the counties named in the birth data. That matches the exposure to the same counties as the outcome (see L1). Report both columns. A large gap between them is itself evidence that the weighting matters.
- **Visible in:** the `extreme_heat_days_mean` and `extreme_heat_days_mean_birth_counties` columns of the heat region file. `extreme_heat_days_births_weighted` stays blank until the fix.
- **Fix:** once DSHS all-county births arrive, pass `weights={(fips, year, month|None): births}` to `heat_etl.region_rollup.rollup_heat`. The births-weighted column fills in with no other code changes. Keep the simple means as a sensitivity analysis.
- **Note:** births-weighting with WONDER data alone isn't a full fix. Weights would exist only for the large counties, so it would reproduce L1.
