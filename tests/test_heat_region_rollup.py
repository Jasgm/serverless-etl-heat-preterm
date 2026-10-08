import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from common.regions import load_region_lookup  # noqa: E402
from heat_etl import region_rollup as hr  # noqa: E402

TRAVIS, WILLIAMSON, BLANCO = "48453", "48491", "48031"   # all Region 7 (30 counties)


def heat(fips, month, days, complete=True, year=2020):
    return {"county_fips": fips, "year": year, "month": month,
            "extreme_heat_days": days, "complete": complete}


def full_year(fips, days_per_month, year=2020):
    return [heat(fips, m, days_per_month, year=year) for m in range(1, 13)]


class HeatRollupTests(unittest.TestCase):
    def setUp(self):
        self.lookup = load_region_lookup("hsr")

    def test_simple_mean_weights_counties_equally(self):
        recs = hr.rollup_heat([heat(TRAVIS, 7, 10), heat(BLANCO, 7, 2)], self.lookup, period="month")
        self.assertEqual(recs[0]["extreme_heat_days_mean"], 6.0)
        self.assertEqual((recs[0]["counties_with_heat"], recs[0]["counties_in_region"]), (2, 30))

    def test_birth_county_subset_mean(self):
        recs = hr.rollup_heat([heat(TRAVIS, 7, 10), heat(WILLIAMSON, 7, 8), heat(BLANCO, 7, 2)],
                              self.lookup, period="month", birth_counties={TRAVIS, WILLIAMSON})
        self.assertEqual(recs[0]["extreme_heat_days_mean"], round(20 / 3, 2))
        self.assertEqual(recs[0]["extreme_heat_days_mean_birth_counties"], 9.0)
        self.assertEqual(recs[0]["birth_counties_with_heat"], 2)

    def test_births_weighted_when_weights_supplied(self):
        weights = {(TRAVIS, 2020, 7): 1000, (BLANCO, 2020, 7): 10}
        recs = hr.rollup_heat([heat(TRAVIS, 7, 10), heat(BLANCO, 7, 2)], self.lookup,
                              period="month", weights=weights)
        self.assertEqual(recs[0]["extreme_heat_days_mean"], 6.0)                       # simple
        self.assertEqual(recs[0]["extreme_heat_days_births_weighted"], round(10020 / 1010, 2))  # 9.92
        self.assertEqual(recs[0]["weighted_counties"], 2)

    def test_weighted_blank_without_weights(self):
        recs = hr.rollup_heat([heat(TRAVIS, 7, 10)], self.lookup, period="month")
        self.assertIsNone(recs[0]["extreme_heat_days_births_weighted"])
        self.assertIsNone(recs[0]["extreme_heat_days_mean_birth_counties"])

    def test_incomplete_month_excluded_and_counted(self):
        recs = hr.rollup_heat([heat(TRAVIS, 7, 10), heat(BLANCO, 7, 25, complete=False)],
                              self.lookup, period="month")
        self.assertEqual(recs[0]["extreme_heat_days_mean"], 10.0)
        self.assertEqual(recs[0]["incomplete_county_periods_excluded"], 1)

    def test_year_sums_months_and_requires_all_twelve(self):
        records = full_year(TRAVIS, 2) + full_year(BLANCO, 1)[:11]   # Blanco missing December
        recs = hr.rollup_heat(records, self.lookup, period="year")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["extreme_heat_days_mean"], 24.0)
        self.assertEqual(recs[0]["counties_with_heat"], 1)
        self.assertEqual(recs[0]["incomplete_county_periods_excluded"], 1)

    def test_csv_header(self):
        out = hr.to_csv(hr.rollup_heat([heat(TRAVIS, 7, 10)], self.lookup, period="month"))
        self.assertEqual(out.splitlines()[0], ",".join(hr.HEAT_REGION_FIELDS))


if __name__ == "__main__":
    unittest.main()
