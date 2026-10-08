"""Count extreme-heat days per county-month: days with Tmax strictly above the county threshold."""

MIN_MONTH_COVERAGE = 0.90   # a county-month needs >= 90% of its days observed to be 'complete'

MONTHLY_FIELDS = ["county_fips", "year", "month", "extreme_heat_days", "days_observed",
                  "days_in_month", "complete", "tmax_threshold_c", "baseline"]


def count_extreme_days(year, month, county_days, thresholds, baseline):
    records, no_threshold = [], []
    for fips in sorted(county_days):
        if fips not in thresholds:
            no_threshold.append(fips)
            continue
        days = county_days[fips]
        observed = [v for v in days if v is not None]
        t = thresholds[fips]
        records.append({
            "county_fips": fips, "year": year, "month": month,
            "extreme_heat_days": sum(1 for v in observed if v > t),
            "days_observed": len(observed), "days_in_month": len(days),
            "complete": len(observed) / len(days) >= MIN_MONTH_COVERAGE,
            "tmax_threshold_c": t, "baseline": baseline,
        })
    return records, no_threshold
