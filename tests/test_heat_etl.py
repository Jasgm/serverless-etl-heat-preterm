import calendar
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from common.regions import load_region_lookup  # noqa: E402
from heat_etl import handler as hh, monthly, nclimgrid as nc, thresholds as th  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "tmax-198101-cty-scaled.csv")


def make_file(year, month, counties):
    """counties: {ncei_id: (label, [daily values or None])} -> nClimGrid-format text."""
    lines = []
    for ncei_id, (label, vals) in counties.items():
        vals = list(vals) + [None] * (31 - len(vals))
        cells = [f"{-999.99 if v is None else v:8.2f}" for v in vals]
        lines.append(",".join(["cty", ncei_id, label, str(year), f"{month:02d}", "TMAX"] + cells))
    return "\n".join(lines) + "\n"


class ParseRealSampleTests(unittest.TestCase):
    """Uses real NOAA rows (Jan 1981): 2 Alabama rows, all Texas rows, 1 Wyoming row."""

    def setUp(self):
        with open(FIXTURE) as f:
            self.days = nc.parse_county_file(f.read(), expect=("tmax", 1981, 1))

    def test_only_texas_all_254_counties(self):
        self.assertEqual(len(self.days), 254)
        self.assertTrue(all(f.startswith("48") for f in self.days))

    def test_ncei_ids_translate_to_fips_that_match_region_lookup(self):
        self.assertEqual(set(self.days), set(load_region_lookup("hsr")))

    def test_known_value(self):
        self.assertEqual(len(self.days["48453"]), 31)
        self.assertEqual(self.days["48453"][0], 22.41)     # Travis, 1981-01-01


class ParseEdgeCaseTests(unittest.TestCase):
    def test_february_drops_padding_days_and_keeps_real_missing(self):
        vals = [10.0] * 28
        vals[5] = None                                         # a genuinely missing day
        text = make_file(2021, 2, {"41453": ("TX: Travis County", vals)})
        days = nc.parse_county_file(text)["48453"]
        self.assertEqual(len(days), 28)
        self.assertIsNone(days[5])

    def test_leap_year_february(self):
        text = make_file(2020, 2, {"41453": ("TX: Travis County", [10.0] * 29)})
        self.assertEqual(len(nc.parse_county_file(text)["48453"]), 29)

    def test_filename_rules(self):
        self.assertEqual(nc.parse_filename("raw/temperature/tmax-202007-cty-scaled.csv.gz"), ("tmax", 2020, 7))
        with self.assertRaises(nc.NClimGridFormatError):
            nc.parse_filename("raw/temperature/tmax-202007-cty-prelim.csv")

    def test_row_must_match_filename(self):
        text = make_file(2020, 8, {"41453": ("TX: Travis County", [10.0] * 31)})
        with self.assertRaises(nc.NClimGridFormatError):
            nc.parse_county_file(text, expect=("tmax", 2020, 7))

    def test_bad_row_width(self):
        with self.assertRaises(nc.NClimGridFormatError):
            nc.parse_county_file("cty,41453,TX: Travis County,2020,07,TMAX,1.0\n")


class ThresholdTests(unittest.TestCase):
    def test_percentile_matches_numpy_default(self):
        self.assertAlmostEqual(th.percentile(range(1, 101), 95), 95.05)
        self.assertEqual(th.percentile([5.0], 95), 5.0)

    def _year(self, builder, year, value_fn, drop=0):
        for m in range(1, 13):
            n = calendar.monthrange(year, m)[1]
            vals = [value_fn(m, d) for d in range(1, n + 1)]
            if drop:
                vals = [None if i < drop else v for i, v in enumerate(vals)]
            builder.add_month(year, m, {"48453": vals})

    def test_builder_requires_every_baseline_month(self):
        b = th.ThresholdBuilder(2000, 2001)
        self._year(b, 2000, lambda m, d: 20.0)
        with self.assertRaises(ValueError):
            b.build()

    def test_low_coverage_county_gets_no_threshold(self):
        b = th.ThresholdBuilder(2000, 2000)
        self._year(b, 2000, lambda m, d: 20.0, drop=5)        # ~16% missing
        self.assertIsNone(b.build()[0]["tmax_threshold_c"])

    def test_months_outside_baseline_ignored(self):
        b = th.ThresholdBuilder(2000, 2000)
        b.add_month(1999, 7, {"48453": [99.0] * 31})
        self._year(b, 2000, lambda m, d: float(m))           # monthly values 1..12
        row = b.build()[0]
        self.assertEqual(row["days_observed"], 366)
        self.assertEqual(row["tmax_threshold_c"], 12.0)


class MonthlyCountTests(unittest.TestCase):
    def test_strictly_above_threshold(self):
        recs, _ = monthly.count_extreme_days(2020, 7, {"48453": [38.0, 38.01, 37.9, 40.0]},
                                             {"48453": 38.0}, "1991-2020")
        self.assertEqual(recs[0]["extreme_heat_days"], 2)

    def test_complete_flag(self):
        days = [30.0] * 27 + [None] * 4                     # 27/31 = 87% observed
        recs, _ = monthly.count_extreme_days(2020, 7, {"48453": days}, {"48453": 35.0}, "1991-2020")
        self.assertFalse(recs[0]["complete"])

    def test_county_without_threshold_reported(self):
        recs, missing = monthly.count_extreme_days(2020, 7, {"48453": [1.0]}, {}, "1991-2020")
        self.assertEqual((recs, missing), ([], ["48453"]))


class FakeS3:
    PAGE = 5   # small page size so pagination is exercised

    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body if isinstance(Body, bytes) else Body.encode()

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.PAGE]
        resp = {"Contents": [{"Key": k} for k in page], "IsTruncated": start + self.PAGE < len(keys)}
        if resp["IsTruncated"]:
            resp["NextContinuationToken"] = str(start + self.PAGE)
        return resp


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        # one-year baseline (2000): Travis Tmax = day of month (1-31)
        for m in range(1, 13):
            n = calendar.monthrange(2000, m)[1]
            text = make_file(2000, m, {"41453": ("TX: Travis County", [float(d) for d in range(1, n + 1)])})
            self.s3.put_object("bkt", f"raw/temperature/tmax-2000{m:02d}-cty-scaled.csv", text)

    def test_build_thresholds_paginates_and_writes_reference(self):
        summary = hh.build_thresholds(self.s3, "bkt", 2000, 2000)
        self.assertEqual(summary["counties"], 1)
        text = self.s3.objects[("bkt", "processed/heat/thresholds/thresholds_2000_2000.csv")].decode()
        self.assertIn("48453,2000-2000,95.0,", text)

    def test_monthly_fails_loudly_without_thresholds(self):
        event = {"Records": [{"s3": {"bucket": {"name": "bkt"},
                                     "object": {"key": "raw/temperature/tmax-200007-cty-scaled.csv"}}}]}
        with self.assertRaises(RuntimeError):
            hh.lambda_handler(event, None, s3=self.s3)

    def test_monthly_end_to_end(self):
        hh.build_thresholds(self.s3, "bkt", 2000, 2000)
        old = hh.BASELINES
        hh.BASELINES = ["2000-2000"]
        try:
            event = {"Records": [{"s3": {"bucket": {"name": "bkt"},
                                         "object": {"key": "raw/temperature/tmax-200007-cty-scaled.csv"}}}]}
            res = hh.lambda_handler(event, None, s3=self.s3)["processed"]
        finally:
            hh.BASELINES = old
        out = self.s3.objects[("bkt", "processed/heat/county_month/2000-2000/tmax-200007.csv")].decode()
        row = out.splitlines()[1].split(",")
        self.assertEqual(row[:3], ["48453", "2000", "7"])
        self.assertEqual(int(row[3]), 2)          # baseline values are day numbers 1-31; p95 ~= 30, so July 30 and 31 exceed it
        self.assertEqual(res[0]["output_rows"], 1)


if __name__ == "__main__":
    unittest.main()
