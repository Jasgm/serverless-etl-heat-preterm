"""Roll county-level extreme-heat days up to Texas DSHS regions.

Input: one record per county-month from the heat transform
    {county_fips, year, month, extreme_heat_days, complete}
    complete = False when the county-month has too many missing days to trust.

Output columns (all computed when their inputs exist, side by side for comparison):
    extreme_heat_days_mean                  simple mean over ALL counties in the region
    extreme_heat_days_mean_birth_counties   simple mean over counties named in the birth data
    extreme_heat_days_births_weighted       mean weighted by county births (needs DSHS data)

KNOWN WEAKNESS of the simple means (see docs/limitations.md, L2):
every county counts equally, so a county with a few hundred births moves the region
value as much as Travis County. If heat differs between rural and urban counties
(it does: urban heat islands, West Texas desert), the region exposure won't match the
population that produced the births. Births-weighted is the planned fix.
"""
import csv
import io

from common.regions import counties_by_region, region_sort_key

HEAT_REGION_FIELDS = [
    "region_level", "region", "year", "month",
    "extreme_heat_days_mean", "extreme_heat_days_mean_birth_counties",
    "extreme_heat_days_births_weighted",
    "counties_in_region", "counties_with_heat", "birth_counties_with_heat", "weighted_counties",
    "incomplete_county_periods_excluded",
]


def _county_periods(heat_records, period):
    """Return ({(fips, year, month|None): days}, incomplete_keys)."""
    if period == "month":
        good, bad = {}, set()
        for r in heat_records:
            key = (r["county_fips"], r["year"], r["month"])
            (good.__setitem__(key, r["extreme_heat_days"]) if r["complete"] else bad.add(key))
        return good, bad

    # period == "year": a county-year counts only if all 12 months are present and complete
    months, days, bad = {}, {}, set()
    for r in heat_records:
        key = (r["county_fips"], r["year"], None)
        if not r["complete"]:
            bad.add(key)
        months.setdefault(key, set()).add(r["month"])
        days[key] = days.get(key, 0) + r["extreme_heat_days"]
    for key, m in months.items():
        if m != set(range(1, 13)):
            bad.add(key)
    return {k: v for k, v in days.items() if k not in bad}, bad


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def rollup_heat(heat_records, lookup, level="hsr", period="year", birth_counties=None, weights=None):
    """
    birth_counties : set of FIPS named in the birth data (optional)
    weights        : {(fips, year, month|None): births} (optional, from DSHS all-county data)
    """
    if period not in ("year", "month"):
        raise ValueError("period must be 'year' or 'month'")
    county_days, incomplete = _county_periods(heat_records, period)
    region_sizes = {r: len(f) for r, f in counties_by_region(lookup).items()}

    groups = {}
    for (fips, year, month), days in county_days.items():
        if fips not in lookup:
            continue  # out-of-state or unknown; heat data should be pre-filtered to Texas
        region = lookup[fips]
        g = groups.setdefault((region, year, month), {"all": [], "birth": [], "w": []})
        g["all"].append(days)
        if birth_counties is not None and fips in birth_counties:
            g["birth"].append(days)
        if weights and weights.get((fips, year, month)):
            g["w"].append((days, weights[(fips, year, month)]))

    incomplete_by_group = {}
    for fips, year, month in incomplete:
        if fips in lookup:
            k = (lookup[fips], year, month)
            incomplete_by_group[k] = incomplete_by_group.get(k, 0) + 1

    records = []
    for key in sorted(groups, key=lambda k: (region_sort_key(k[0]), k[1], k[2] or 0)):
        region, year, month = key
        g = groups[key]
        total_w = sum(w for _, w in g["w"])
        records.append({
            "region_level": level, "region": region, "year": year, "month": month,
            "extreme_heat_days_mean": _mean(g["all"]),
            "extreme_heat_days_mean_birth_counties": _mean(g["birth"]) if birth_counties is not None else None,
            "extreme_heat_days_births_weighted":
                round(sum(d * w for d, w in g["w"]) / total_w, 2) if total_w else None,
            "counties_in_region": region_sizes[region],
            "counties_with_heat": len(g["all"]),
            "birth_counties_with_heat": len(g["birth"]) if birth_counties is not None else None,
            "weighted_counties": len(g["w"]) if weights else None,
            "incomplete_county_periods_excluded": incomplete_by_group.get(key, 0),
        })
    return records


def to_csv(records):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEAT_REGION_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in records:
        w.writerow({k: ("" if r.get(k) is None else r[k]) for k in HEAT_REGION_FIELDS})
    return buf.getvalue()
