# Serverless ETL on AWS: Concepts Learned & Q&A

*Study guide for the extreme heat × preterm birth pipeline. Last updated 2026-10-08.*

---

## 1. Where we are in the software development lifecycle (SDLC)

| Phase | Status | What it looks like in this project |
|---|---|---|
| Requirements | Mostly done | Research question; exposure and outcome definitions; regions as the analysis unit; birth years and exposure window still open |
| System design | Done for the core | S3 zones, Lambda per source, region roll-ups, limitations log |
| Implementation | In progress | Natality ETL, region roll-ups, NOAA parser, thresholds, monthly heat counts |
| Testing | Ongoing | 46 unit tests, including a real NOAA data fixture |
| Deployment | Not started | SAM template, IAM, budget alert, log retention. Code is in Git with CI running the tests |

---

## 2. Core concepts

### Pipeline architecture
- **Zones in one S3 bucket:** `raw/` (exact copies of source files, never edited) → `processed/` (cleaned, standardized) → `analytics/` (joined, analysis-ready). If code has a bug, fix it and rerun from raw.
- **One Lambda per source:** each Lambda triggers only on its own `raw/` prefix. Ingestion (getting files in) is separate from transformation (cleaning them). The heat Lambda doesn't care whether a file was uploaded by hand or pulled by a schedule.
- **Trigger loops:** if a Lambda writes into the folder that triggers it, it re-triggers itself and can run up a bill. Prevent this by writing to a different prefix and checking the prefix in code.
- **URL-encoded keys:** S3 events encode file names, so a space arrives as `+`. Decode with `unquote_plus`, or the function looks for a file that doesn't exist.
- **Idempotency:** running a step twice with the same input gives the same output. Overwrite outputs; never append duplicates.
- **Pagination:** S3 lists at most 1,000 objects per call. Code that lists files must loop through pages.

### Data quality
- **Fail loudly:** an unknown category label, a missing column or an unknown county code stops the run. Silent guessing produces wrong numbers that look right.
- **Manifests:** each run writes a JSON summary (rows in, rows out, flags, coverage). It's both your data-quality log and your audit trail.
- **Lineage:** every output row records its `source_file`, so any number can be traced back to the raw file it came from.
- **Missing ≠ zero:** suppressed or "Not Available" values leave a rate blank. Treating them as zero quietly biases rates down.

### Grain and aggregation
- **Grain** is what one row represents. Both sources must reach the same grain (county + year + month) before a join. Joining daily rows to monthly rows repeats the monthly values about 30 times and inflates every total.
- **Aggregate up, never down:** store the finest grain both sources support (county-month). Region-year can always be computed from it, but not the reverse.
- **Sum counts, then recompute rates.** Averaging county rates gives a small county the same weight as a big one. Example: Travis at 10% on 1,000 births and Williamson at 20% on 100 births average to 15%, but the true combined rate is 10.9%.
- **Denominators:** the preterm rate uses births with *known* gestational age as the denominator.

### CDC WONDER Natality
- WONDER only names counties with a **2010 population of 100,000 or more**. All smaller Texas counties are merged into "Unidentified Counties, TX" (code 48999).
- Selecting several counties in the WONDER interface doesn't fix this, because small counties aren't selectable at all.
- Region totals from WONDER = the **large counties only**. This is recorded as limitation L1, with coverage (`counties_reported` out of `counties_in_region`, and the unassigned share) reported in the manifest.
- Planned fix: DSHS all-county vital statistics. The pipeline is designed so only the parser changes when the source changes.

### Texas DSHS regions
- 11 **Public Health Regions (PHR)**, administered by 8 regional offices (**Health Service Regions, HSR**): 1, 2/3, 4/5N, 6/5S, 7, 8, 9/10, 11.
- Region 5 is split: Hardin, Jefferson and Orange go to 6/5S; the rest to 4/5N.
- The lookup table is code, not manual GUI selections: it's reproducible, versioned and testable. Its build script caught a missing county (Ward) that would have shifted every later county into the wrong region.

### Heat exposure
- **Baseline:** the reference period that defines "normal" for each county. 1991–2020 is the current official 30-year climate normal.
- **Threshold:** each county's 95th percentile of daily max temperature over the baseline. A day is "extreme" if it's **strictly above** that value.
- **Sensitivity analysis:** rerun with a 1981–2010 baseline. Texas has warmed, so older thresholds are lower and recent years show more extreme days. If results hold under both baselines, the finding is robust.
- **Whole-year vs. seasonal percentile:** a whole-year 95th percentile mostly selects summer days. That's the standard choice for "dangerously hot days."
- **Completeness rules:** a county-month needs at least 90% of its days observed, and a county's baseline needs at least 90% coverage. Incomplete data is excluded and counted, never silently used.

### Region heat weighting (limitation L2)
- **Simple mean (current):** every county counts equally. Blanco (~10 births) moves the region value as much as Travis (~1,000).
- **Births-weighted (planned):** weight each county by its births so exposure reflects where births happened. In the Travis + Blanco example, the simple mean is 6.0 days and the births-weighted mean is 9.9 days.
- **Partial mitigation now:** a second column averages only the counties named in the birth data, matching exposure to the outcome population.
- Misclassified exposure usually pulls an estimated association toward zero, but not always.

### NOAA nClimGrid-Daily
- File needed: `tmax-YYYYMM-cty-scaled.csv(.gz)` (daily max temperature, county averages, final values).
- No header row; 37 fields; values in °C; non-existent days (Feb 30) = **-999.99**.
- **County IDs use NCEI state numbering, not FIPS. Texas is 41, not 48.** The 3-digit county part matches FIPS (41453 → 48453, Travis County).
- **Scaled vs. prelim:** prelim values are early and get replaced. The pipeline accepts only scaled files.
- The archive tarballs (~160 MB/month) contain 78 files; only one (~260 KB) is needed.

### Ingestion patterns
- **Manual upload:** fine for sources without an API (WONDER natality, DSHS files).
- **Scheduled pull:** EventBridge triggers an ingest Lambda that fetches new months from NOAA's public bucket over HTTPS. No gcloud is needed inside Lambda.
- **Always keep your own raw copy**, even when the source is online. Sources revise and move files, and your results must be reproducible.
- **Backfill vs. incremental:** a one-time bulk download (1981 to today), then a small monthly job.
- Mixed manual and automated ingestion is normal in real pipelines, and a good point to discuss in interviews.

### Cost awareness
- Storage is cheap: about 150 MB of temperature files costs well under a cent per month. Repeated transfer and compute are where costs add up.
- The real traps on small projects: **NAT gateways** (~$30+/month), runaway trigger loops, storing data you don't need, CloudWatch logs kept forever, and S3 versioning piling up copies.
- First protection: an **AWS Budgets alert** (e.g. $5) before deploying anything.

### VPC (Virtual Private Cloud)
- Your own private network inside AWS: subnets (public/private), internet gateway, NAT gateway, security groups, VPC endpoints.
- **Needed for:** databases (RDS, Redshift), regulated data (HIPAA/PHI), connecting to a company network, layered security.
- **Not needed here:** S3, Lambda and EventBridge are secured by IAM permissions. A VPC would add cost and complexity with no security gain.
- Interview angle: *"No VPC because the data is public and aggregate. With PHI, I'd add private subnets, VPC endpoints and flow logs."*

### Query and storage services
| | Athena | RDS | Redshift |
|---|---|---|---|
| What it is | Query engine over S3 files | Managed RDBMS | Data warehouse |
| Built for | Ad-hoc analysis of files | App transactions (OLTP) | Large-scale analytics (OLAP) |
| Cost model | Per query (data scanned) | Per hour, always on | Per hour or per compute used |
| Needs a VPC | No | Yes | Yes |
| Enforced keys | No | Yes | No (declared, not enforced) |

- **OLTP** (online transaction processing): many small reads and writes with strict integrity, e.g. an EHR.
- **OLAP** (online analytical processing): big scans and aggregates, e.g. reporting and research.
- **This project:** Athena, with the Glue Data Catalog holding table definitions over `processed/` and `analytics/`.

### Testing approach
- Pure functions (parse, transform, roll up) are tested separately from AWS. A **fake S3 client** lets tests run on a laptop with no AWS account.
- A **real-data fixture** (actual NOAA rows) catches format surprises that synthetic data misses.
- Edge cases worth testing: leap years, padded days, URL-encoded keys, pagination, missing thresholds, out-of-state rows, unknown labels.

### Version control and a public portfolio repo
- **Commit history is part of the portfolio:** small commits with messages that explain *why*, each one leaving the tests passing.
- **CI (continuous integration):** a GitHub Actions workflow runs the unit tests on every push, on Python 3.10 and 3.12 (3.12 matches the Lambda runtime).
- **Keep out of a public repo:** data files (`data/` is in `.gitignore`), raw CDC WONDER exports, secrets (`.env`, AWS keys), and personal email (use GitHub's private noreply address).
- **License:** MIT, so others may reuse the code with attribution.
- **GitHub Flow (branch per feature):** `main` always works; each piece of work gets a short-lived branch (`feature/...`, `infra/...`, `docs/...`) merged into `main` through a pull request after CI passes; the branch is deleted after merging. Branch per *feature*, not per development phase, because testing happens on every branch.
- **Stacked branches:** a branch built on another unmerged branch (here `feature/heat-etl` builds on `feature/natality-etl` because it uses the shared region lookup). Merge them in order, with merge commits rather than squash merges.
- **Lock files:** git briefly creates files like `.git/index.lock` to stop two git commands from changing the repo at once, then deletes them. If a command crashes and leaves one behind, the next command fails with "File exists" until the stale lock is removed.

---

## 3. Q&A from our sessions

**Q: What steps are recommended for the ETL pipeline itself?**
Ingest raw (unchanged) → validate → clean and standardize → transform each source to a shared grain → join → load in an analysis format (Parquet + Athena) → run idempotently with failure handling → observe (manifests, logs, alarms, lineage) → test at three levels (unit, integration, reconciliation) → deploy as code with least-privilege IAM.

**Q: What does "transform each source to a shared grain" mean?**
Reshape every source so one row means the same thing (county + year + month) before joining. Births collapse gestational-age rows; heat goes from station/day to county-month extreme-heat days. Otherwise joins duplicate rows and inflate totals.

**Q: County data gets suppressed. Can we group CDC data by health region instead?**
Not reliably from WONDER. Small counties are merged statewide, not region by region, so they can't be assigned. Decision: keep the pipeline at county level, roll up to regions in code, record coverage in the manifest, and request DSHS all-county data to replace WONDER later.

**Q: Could I select multiple counties in the WONDER GUI as an aggregate?**
It works mechanically, but it only covers counties with a population of 100k or more, and it's less reproducible than exporting by county and grouping in code. The one advantage: a pooled GUI query can include cells that would be suppressed individually.

**Q: How should heat be rolled up to regions?**
Start with a simple county mean, document the weakness (L2), and switch to births-weighted means when DSHS data arrives. All three columns are produced side by side for comparison.

**Q: What do "1991–2020 baseline" and "1981–2010 sensitivity check" mean?**
The baseline defines each county's normal heat; its 95th percentile is the extreme-heat threshold. The sensitivity check repeats the analysis with an older baseline to see whether conclusions depend on that choice.

**Q: Which NOAA files should I download?**
Only `tmax-YYYYMM-cty-scaled.csv(.gz)`, from January 1981 through the end of the study years. Skip the other variables, area types and the `.nc` grid files.

**Q: What was wrong with my gcloud command?**
It started at 2001 (the baselines need 1981–2020), and `--recursive` copied all 36 files per month instead of the one tmax county file. The corrected version loops over 1981–2026 and copies only `tmax-*-cty-scaled.csv*`.

**Q: Could the pipeline pull NOAA data directly instead of me downloading it?**
Yes. A scheduled ingest Lambda copies new months into `raw/`. Keep a raw copy (for reproducibility), take scaled files only, make it idempotent, keep the Lambda outside a VPC, and treat backfill and incremental as separate jobs.

**Q: Can I save money by not storing the temperature data?**
Not meaningfully. Storage is a fraction of a cent per month. The real cost risks are NAT gateways, trigger loops, storing unneeded files, log retention and versioning. Set a budget alert first.

**Q: What is a VPC, and what are its use cases, pros and cons?**
A private network in AWS. Use it for databases, regulated data, hybrid networks and layered security. Pros: isolation and traffic control. Cons: complexity and NAT cost, and a Lambda inside one loses internet access unless you add NAT. Not needed for this project.

**Q: What are Athena, Redshift and RDS?**
Athena runs SQL on S3 files, billed per query. RDS is a managed traditional database for apps. Redshift is a data warehouse for large-scale analytics. This project uses Athena.

**Q: Are they all RDBMSs?**
Only RDS. Redshift is relational but analytics-tuned, with keys that aren't enforced. Athena is a query engine with no storage of its own: the pipeline is the only data-quality safeguard.

**Q: Is it worthwhile to commit to my public repo as we progress?**
Yes. A history of small, tested, well-described commits shows how you work. Run tests in CI, keep data and secrets out, be transparent about AI assistance, and be ready to explain every line.

**Q: Should I create a branch for each step?**
Yes, per feature rather than per development phase. The existing work was split into `feature/natality-etl` (with CI), `feature/heat-etl` (stacked on natality) and `docs/project-docs`, each merged into `main` by pull request.

**Q: What are temporary lock files?**
Short-lived files git creates (such as `.git/index.lock`) so that only one git command changes the repo at a time. Git deletes them when it finishes; a leftover lock from a crashed command blocks the next one until it's removed.

---

## 4. Interview talking points
1. **Fail loudly on data quality.** Unknown labels or codes stop the run; manifests record every exclusion.
2. **Grain discipline.** "I stored county-month so I could aggregate to any level, and I summed counts before recomputing rates."
3. **Documented limitations.** Coverage gaps and exposure weighting are logged with their effect, how they show up in the outputs and the planned fix.
4. **Source-swappable design.** Replacing WONDER with DSHS data only changes the parser.
5. **Cost and security reasoning.** No VPC (public aggregate data), a budget alert, Athena instead of an always-on database, and the changes I'd make for PHI.
6. **Real-data testing.** A real NOAA fixture caught the NCEI-vs-FIPS state code issue.

---

## 5. Open decisions
- Birth years for the study
- Exposure window (month of birth vs. late pregnancy)
- Fill in the years in the DSHS data request and send it
- Confirm WONDER export headers and labels against a real export
