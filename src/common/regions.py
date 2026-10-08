"""Shared Texas county -> DSHS region lookup (used by natality and heat roll-ups)."""
import csv
import os

LOOKUP_PATH = os.path.join(os.path.dirname(__file__), "tx_county_regions.csv")


def load_region_lookup(level="hsr", path=LOOKUP_PATH):
    """Return {county_fips: region}. level='hsr' (8 admin regions) or 'phr' (11 regions)."""
    if level not in ("hsr", "phr"):
        raise ValueError("level must be 'hsr' or 'phr'")
    with open(path, newline="") as f:
        return {row["county_fips"]: row[level] for row in csv.DictReader(f)}


def counties_by_region(lookup):
    out = {}
    for fips, region in lookup.items():
        out.setdefault(region, set()).add(fips)
    return out


def region_sort_key(region):
    return int(region.split("/")[0])
