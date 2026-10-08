# Serverless ETL on AWS: Extreme Heat & Preterm Birth

The pipeline lands CDC WONDER Natality exports and county temperature data in S3. Lambda functions clean and join them into an analysis-ready county-month table.

## Research framing (decided)
- **Exposure:** extreme heat. A county's day counts as extreme heat when Tmax is above that county's 95th percentile, using a 1991–2020 baseline. A 1981–2010 baseline is the sensitivity check.
- **Outcome:** preterm birth (<37 weeks, OE gestational age) per county-month.
- **Analysis unit:** the 8 Texas DSHS administrative health regions (HSR: 1, 2/3, 4/5N, 6/5S, 7, 8, 9/10, 11) by year. These regions address small-county suppression. The pipeline stores county-month data and rolls up to regions in code.

## Architecture
```
raw/natality/*.txt ──S3 event──▶ λ natality_etl ──▶ processed/natality/*.csv        (county-month)
                                                  ├▶ processed/natality_region/*.csv (DSHS region-year)
                                                  └▶ processed/natality/*.manifest.json
raw/temperature/*  ──S3 event──▶ λ heat_etl      ──▶ processed/heat/*.csv          (next)
processed/*        ─────────────▶ λ join          ──▶ analytics/heat_preterm.csv     (later)
```
One bucket, separated by prefix. Each Lambda triggers only on its `raw/` prefix, so its output can't re-trigger it.

## Status
| Step | Component | State |
|---|---|---|
| 1 | `natality_etl`: parse WONDER export → county-month preterm counts/rate | ✅ coded + tested |
| 1b | Region roll-up (county → DSHS HSR/PHR) with coverage reporting | ✅ coded + tested (21 tests total) |
| 2a | Heat region roll-up: simple mean (all counties + birth counties); births-weighted ready but off until DSHS data arrives | ✅ coded + tested (28 tests total) |
| 2b | `heat_etl` transform: daily Tmax → county 95th-pct thresholds → monthly extreme-heat days | ⏳ next (pick NOAA source) |
| 3 | Join + output table | ⏳ |
| 4 | SAM template, IAM least-privilege, deploy | ⏳ |

## natality_etl: data rules
- WONDER query: Group By **County, Year, Month, OE Gestational Age Recode 10**. Export with "Export Results".
- Rows marked `Total` and the `---` footer are dropped.
- The preterm rate's denominator is births with **known** gestational age only.
- A suppressed or "Not Available" cell leaves the rate blank. It is never treated as zero.
- FIPS codes ending in `999` ("Unidentified Counties") are kept but flagged. They can't be linked to county temperature.
- An unrecognized gestational-age label or a missing column stops the run with an error. The code doesn't guess.

## Region roll-up rules
- The lookup is `src/common/tx_county_regions.csv` (shared by the natality and heat roll-ups): 254 counties with FIPS, PHR (11 regions) and HSR (8 admin regions). It's built by `scripts/build_region_lookup.py` from the DSHS table *Texas County Numbers and Public Health Regions*.
- The roll-up sums counts and then recomputes the rate. It never averages county rates.
- WONDER only names counties with a 2010 population of 100k or more. Smaller counties appear as "Unidentified Counties", which can't be assigned to a region. Those births are reported as **unassigned** in the manifest (count and statewide share), so totals still reconcile.
- Each region row includes `counties_reported` out of `counties_in_region`. **Region totals from WONDER = named (large) counties only.** This is a stated limitation until the DSHS county data arrives.
- Settings: `REGION_LEVEL` = `hsr` (default) or `phr`. `REGION_PERIOD` = `year` (default) or `month`.

## Study limitations
Tracked in [`docs/limitations.md`](docs/limitations.md): **L1** WONDER covers only large counties; **L2** region heat exposure is a simple average until births-weighting is possible.

## Data source swap plan
DSHS vital statistics include all 254 counties (see `reference/dshs_data_request.md`). When that data arrives, add a parser that outputs the same county-month record fields. The roll-up, join and analytics steps don't change.

## Run tests (no AWS account needed)
```bash
python3 -m unittest discover -s tests -v
```
