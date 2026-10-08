"""Run from the project root:  python3 -m unittest discover -s tests -v"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from natality_etl import handler as h  # noqa: E402

HEADER = ["Notes", "County of Residence", "County of Residence Code", "Year", "Year Code",
          "Month", "Month Code", "OE Gestational Age Recode 10",
          "OE Gestational Age Recode 10 Code", "Births"]


def line(cells):
    return "\t".join(f'"{c}"' if c else "" for c in cells)


def row(county, fips, month, ga, births, notes=""):
    return line([notes, county, fips, "2020", "2020", "July", str(month), ga, "x", str(births)])


def export(*rows):
    footer = ['"---"', '"Dataset: Natality, 2016-2022 expanded"', '"---"']
    return "\n".join([line(HEADER), *rows, *footer]) + "\n"


SAMPLE = export(
    row("Travis County, TX", "48453", 7, "34 - 36 weeks", 120),
    row("Travis County, TX", "48453", 7, "28 - 31 weeks", 15),
    row("Travis County, TX", "48453", 7, "39 weeks", 700),
    row("Travis County, TX", "48453", 7, "40 weeks", 165),
    row("Travis County, TX", "48453", 7, "Unknown or Not Stated", 4),
    row("Travis County, TX", "48453", 7, "", 1004, notes="Total"),
    row("Unidentified Counties, TX", "48999", 7, "34 - 36 weeks", 30),
    row("Unidentified Counties, TX", "48999", 7, "39 weeks", 270),
)


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body


def s3_event(bucket, key):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


class ParseTests(unittest.TestCase):
    def test_drops_totals_and_footer(self):
        rows, totals = h.parse_wonder_export(SAMPLE)
        self.assertEqual(len(rows), 7)
        self.assertEqual(totals, 1)

    def test_missing_column_fails_loudly(self):
        bad = SAMPLE.replace("OE Gestational Age Recode 10", "LMP Gestational Age Recode 10")
        with self.assertRaises(h.DataQualityError):
            h.parse_wonder_export(bad)


class TransformTests(unittest.TestCase):
    def setUp(self):
        rows, _ = h.parse_wonder_export(SAMPLE)
        self.recs = {r["county_fips"]: r for r in h.aggregate_county_month(rows)}

    def test_preterm_counts(self):
        travis = self.recs["48453"]
        self.assertEqual(travis["births_preterm"], 135)
        self.assertEqual(travis["births_term"], 865)
        self.assertEqual(travis["births_unknown_ga"], 4)
        self.assertEqual(travis["births_total"], 1004)

    def test_rate_excludes_unknown_ga(self):
        self.assertEqual(self.recs["48453"]["preterm_rate"], round(135 / 1000, 4))

    def test_unidentified_county_flagged(self):
        self.assertTrue(self.recs["48999"]["unidentified_county"])
        self.assertFalse(self.recs["48453"]["unidentified_county"])

    def test_missing_cell_blanks_rate(self):
        rows, _ = h.parse_wonder_export(export(
            row("Travis County, TX", "48453", 7, "34 - 36 weeks", "Not Available"),
            row("Travis County, TX", "48453", 7, "39 weeks", 700),
        ))
        rec = h.aggregate_county_month(rows)[0]
        self.assertEqual(rec["missing_cells"], 1)
        self.assertIsNone(rec["preterm_rate"])

    def test_unknown_label_fails_loudly(self):
        rows, _ = h.parse_wonder_export(export(row("Travis County, TX", "48453", 7, "37 weeks", 5)))
        with self.assertRaises(h.DataQualityError):
            h.aggregate_county_month(rows)

    def test_short_fips_is_padded(self):
        rows, _ = h.parse_wonder_export(export(row("Autauga County, AL", "1001", 7, "39 weeks", 50)))
        self.assertEqual(h.aggregate_county_month(rows)[0]["county_fips"], "01001")


class HandlerTests(unittest.TestCase):
    def test_end_to_end_with_url_encoded_key(self):
        s3 = FakeS3({("bkt", "raw/natality/tx 2020.txt"): SAMPLE.encode()})
        result = h.lambda_handler(s3_event("bkt", "raw/natality/tx+2020.txt"), None, s3=s3)

        csv_out = s3.objects[("bkt", "processed/natality/tx 2020.csv")].decode()
        manifest = json.loads(s3.objects[("bkt", "processed/natality/tx 2020.manifest.json")])
        self.assertEqual(csv_out.splitlines()[0], ",".join(h.OUTPUT_FIELDS))
        self.assertEqual(manifest["output_rows"], 2)
        self.assertEqual(manifest["unidentified_county_rows"], 1)
        self.assertEqual(len(result["processed"]), 1)

    def test_ignores_keys_outside_raw_prefix(self):
        s3 = FakeS3()
        result = h.lambda_handler(s3_event("bkt", "processed/natality/x.csv"), None, s3=s3)
        self.assertEqual(result["processed"], [])
        self.assertEqual(s3.objects, {})


if __name__ == "__main__":
    unittest.main()
