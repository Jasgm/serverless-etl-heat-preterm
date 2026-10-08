"""Roll county-level natality records up to Texas DSHS regions.

Rules
- Sum COUNTS first, then recompute the rate. Never average county rates.
- Unidentified-county rows (FIPS ending 999) can't be placed in a region. They are
  reported as 'unassigned' so the statewide total still reconciles.
- If any county in a region-period had a missing cell, that region's rate is left blank.
"""
import csv
from collections import OrderedDict

from common.regions import counties_by_region, load_region_lookup, region_sort_key  # noqa: F401

TEXAS_STATE_FIPS = "48"

REGION_FIELDS = [
    "region_level", "region", "year", "month",
    "births_total", "births_preterm", "births_term", "births_unknown_ga",
    "missing_cells", "preterm_rate", "counties_in_region", "counties_reported",
]


class RegionLookupError(ValueError):
    pass


def rollup(county_records, lookup, level="hsr", period="year"):
    """Return (region_records, coverage)."""
    if period not in ("year", "month"):
        raise ValueError("period must be 'year' or 'month'")

    counties_per_region = counties_by_region(lookup)

    groups = {}
    unassigned = {"births_total": 0, "rows": 0}
    out_of_state = 0
    statewide_births = 0

    for r in county_records:
        fips = r["county_fips"]
        if not fips.startswith(TEXAS_STATE_FIPS):
            out_of_state += 1
            continue
        statewide_births += r["births_total"]
        if r["unidentified_county"]:
            unassigned["births_total"] += r["births_total"]
            unassigned["rows"] += 1
            continue
        if fips not in lookup:
            raise RegionLookupError(f"Texas FIPS {fips} not in region lookup")
        region = lookup[fips]
        key = (region, r["year"], r["month"] if period == "month" else None)
        g = groups.setdefault(key, {
            "region_level": level, "region": region, "year": r["year"],
            "month": r["month"] if period == "month" else None,
            "births_total": 0, "births_preterm": 0, "births_term": 0, "births_unknown_ga": 0,
            "missing_cells": 0, "_counties": set(),
        })
        for k in ("births_total", "births_preterm", "births_term", "births_unknown_ga", "missing_cells"):
            g[k] += r[k]
        g["_counties"].add(fips)

    records = []
    for key in sorted(groups, key=lambda k: (region_sort_key(k[0]), k[1], k[2] or 0)):
        g = groups[key]
        denom = g["births_preterm"] + g["births_term"]
        g["preterm_rate"] = round(g["births_preterm"] / denom, 4) if denom and not g["missing_cells"] else None
        g["counties_in_region"] = len(counties_per_region[g["region"]])
        g["counties_reported"] = len(g.pop("_counties"))
        records.append(g)

    coverage = OrderedDict([
        ("region_level", level),
        ("period", period),
        ("statewide_births", statewide_births),
        ("unassigned_births", unassigned["births_total"]),
        ("unassigned_share", round(unassigned["births_total"] / statewide_births, 4) if statewide_births else None),
        ("out_of_state_rows_excluded", out_of_state),
        ("counties_reported_by_region", OrderedDict(
            (region, f"{len({r['county_fips'] for r in county_records if lookup.get(r['county_fips']) == region})}"
                     f" of {len(fips_set)}")
            for region, fips_set in sorted(counties_per_region.items(), key=lambda kv: region_sort_key(kv[0]))
        )),
        ("note", "Unassigned = WONDER 'Unidentified Counties' (pop < 100k in 2010). "
                 "Region totals cover named counties only."),
    ])
    return records, coverage


def to_csv(records):
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=REGION_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in records:
        w.writerow({k: ("" if r.get(k) is None else r[k]) for k in REGION_FIELDS})
    return buf.getvalue()
