"""County extreme-heat thresholds: the Pth percentile of daily Tmax over a baseline period.

Primary baseline 1991-2020; sensitivity baseline 1981-2010. Thresholds are computed once
and stored as a reference table (processed/heat/thresholds_<start>_<end>.csv).
Percentile method: linear interpolation between order statistics (same as numpy's default
and Excel's PERCENTILE.INC), so results can be checked by hand.
"""
import csv
import io

MIN_BASELINE_COVERAGE = 0.90   # a county needs >= 90% of baseline days observed


def percentile(values, p):
    xs = sorted(values)
    if not xs:
        raise ValueError("no values")
    h = (len(xs) - 1) * p / 100.0
    lo = int(h)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (h - lo) * (xs[hi] - xs[lo])


class ThresholdBuilder:
    """Feed it one parsed month at a time so the whole baseline never sits in one file."""

    def __init__(self, start_year, end_year, pct=95.0):
        self.start, self.end, self.pct = start_year, end_year, pct
        self.values, self.days_expected, self.months_seen = {}, 0, set()

    def add_month(self, year, month, county_days):
        if not self.start <= year <= self.end:
            return
        if (year, month) in self.months_seen:
            raise ValueError(f"{year}-{month:02d} added twice")
        self.months_seen.add((year, month))
        self.days_expected += len(next(iter(county_days.values()), []))
        for fips, days in county_days.items():
            self.values.setdefault(fips, []).extend(v for v in days if v is not None)

    def build(self):
        expected_months = (self.end - self.start + 1) * 12
        if len(self.months_seen) != expected_months:
            raise ValueError(f"baseline {self.start}-{self.end} has {len(self.months_seen)} of "
                             f"{expected_months} months; download the missing files first")
        rows = []
        for fips in sorted(self.values):
            vals = self.values[fips]
            coverage = len(vals) / self.days_expected
            rows.append({
                "county_fips": fips, "baseline": f"{self.start}-{self.end}", "percentile": self.pct,
                "tmax_threshold_c": round(percentile(vals, self.pct), 2) if coverage >= MIN_BASELINE_COVERAGE else None,
                "days_observed": len(vals), "coverage": round(coverage, 4),
            })
        return rows


THRESHOLD_FIELDS = ["county_fips", "baseline", "percentile", "tmax_threshold_c", "days_observed", "coverage"]


def to_csv(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=THRESHOLD_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r[k] is None else r[k]) for k in THRESHOLD_FIELDS})
    return buf.getvalue()


def from_csv(text):
    """Return {fips: threshold_c}; counties with too little baseline data are omitted."""
    return {r["county_fips"]: float(r["tmax_threshold_c"])
            for r in csv.DictReader(io.StringIO(text)) if r["tmax_threshold_c"]}
