import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from natality_etl import handler as h, region_rollup as rr  # noqa: E402
from test_natality_handler import FakeS3, export, row, s3_event  # noqa: E402


def rec(fips, month, preterm, term, unknown=0, missing=0, year=2020):
    return {"county_fips": fips, "year": year, "month": month,
            "births_preterm": preterm, "births_term": term, "births_unknown_ga": unknown,
            "births_total": preterm + term + unknown, "missing_cells": missing,
            "unidentified_county": fips.endswith("999")}


class LookupTests(unittest.TestCase):
    def test_lookup_complete(self):
        hsr = rr.load_region_lookup("hsr")
        self.assertEqual(len(hsr), 254)
        self.assertEqual(len(set(hsr.values())), 8)
        self.assertEqual(len(set(rr.load_region_lookup("phr").values())), 11)

    def test_region_5_split(self):
        hsr, phr = rr.load_region_lookup("hsr"), rr.load_region_lookup("phr")
        self.assertEqual((phr["48245"], hsr["48245"]), ("5", "6/5S"))   # Jefferson
        self.assertEqual((phr["48005"], hsr["48005"]), ("5", "4/5N"))   # Angelina


class RollupTests(unittest.TestCase):
    def setUp(self):
        self.lookup = rr.load_region_lookup("hsr")

    def test_sums_counts_then_recomputes_rate(self):
        # Travis 10% preterm on 1000 births, Williamson 20% on 100: avg of rates = 15%, true = 10.9%
        recs, _ = rr.rollup([rec("48453", 7, 100, 900), rec("48491", 7, 20, 80)], self.lookup)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["region"], r["births_preterm"], r["births_term"]), ("7", 120, 980))
        self.assertEqual(r["preterm_rate"], round(120 / 1100, 4))
        self.assertEqual(r["counties_reported"], 2)
        self.assertEqual(r["counties_in_region"], 30)

    def test_year_period_combines_months(self):
        recs, _ = rr.rollup([rec("48453", 7, 10, 90), rec("48453", 8, 5, 95)], self.lookup, period="year")
        self.assertEqual(len(recs), 1)
        self.assertIsNone(recs[0]["month"])
        self.assertEqual(recs[0]["births_total"], 200)

    def test_month_period_keeps_months(self):
        recs, _ = rr.rollup([rec("48453", 7, 10, 90), rec("48453", 8, 5, 95)], self.lookup, period="month")
        self.assertEqual([r["month"] for r in recs], [7, 8])

    def test_unidentified_counties_are_unassigned_not_dropped(self):
        recs, cov = rr.rollup([rec("48453", 7, 100, 900), rec("48999", 7, 30, 270)], self.lookup)
        self.assertEqual(sum(r["births_total"] for r in recs), 1000)
        self.assertEqual(cov["statewide_births"], 1300)
        self.assertEqual(cov["unassigned_births"], 300)
        self.assertEqual(cov["unassigned_share"], round(300 / 1300, 4))
        self.assertEqual(cov["counties_reported_by_region"]["7"], "1 of 30")

    def test_missing_cell_blanks_region_rate(self):
        recs, _ = rr.rollup([rec("48453", 7, 100, 900), rec("48491", 7, 20, 80, missing=1)], self.lookup)
        self.assertIsNone(recs[0]["preterm_rate"])

    def test_out_of_state_excluded(self):
        recs, cov = rr.rollup([rec("48453", 7, 1, 9), rec("40109", 7, 1, 9)], self.lookup)
        self.assertEqual(cov["out_of_state_rows_excluded"], 1)
        self.assertEqual(cov["statewide_births"], 10)

    def test_unknown_texas_fips_fails_loudly(self):
        with self.assertRaises(rr.RegionLookupError):
            rr.rollup([rec("48998", 7, 1, 9)], self.lookup)

    def test_phr_level(self):
        recs, _ = rr.rollup([rec("48245", 7, 1, 9)], rr.load_region_lookup("phr"), level="phr")
        self.assertEqual((recs[0]["region_level"], recs[0]["region"]), ("phr", "5"))


class HandlerRegionOutputTests(unittest.TestCase):
    def test_handler_writes_region_file_and_coverage(self):
        data = export(
            row("Travis County, TX", "48453", 7, "34 - 36 weeks", 100),
            row("Travis County, TX", "48453", 7, "39 weeks", 900),
            row("Williamson County, TX", "48491", 7, "34 - 36 weeks", 20),
            row("Williamson County, TX", "48491", 7, "39 weeks", 80),
            row("Unidentified Counties, TX", "48999", 7, "39 weeks", 300),
        )
        s3 = FakeS3({("bkt", "raw/natality/tx_2020.txt"): data.encode()})
        h.lambda_handler(s3_event("bkt", "raw/natality/tx_2020.txt"), None, s3=s3)

        region_csv = s3.objects[("bkt", "processed/natality_region/tx_2020.csv")].decode().splitlines()
        self.assertEqual(region_csv[0], ",".join(rr.REGION_FIELDS))
        self.assertEqual(region_csv[1], "hsr,7,2020,,1100,120,980,0,0,0.1091,30,2")
        manifest = json.loads(s3.objects[("bkt", "processed/natality/tx_2020.manifest.json")])
        self.assertEqual(manifest["region_coverage"]["unassigned_births"], 300)


if __name__ == "__main__":
    unittest.main()
