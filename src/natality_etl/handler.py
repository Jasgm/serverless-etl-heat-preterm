"""Lambda #1 — Natality ETL.

Cleans a CDC WONDER Natality export and aggregates it to one row per
county-month with preterm (<37 weeks) birth counts and rate.

Trigger : S3 ObjectCreated on  raw/natality/*.txt
Output  : processed/natality/<name>.csv                county-month
          processed/natality_region/<name>.csv         DSHS region-year (or -month)
          processed/natality/<name>.manifest.json      row counts, data quality, region coverage

Expected export: Group By = County, Year, Month, OE Gestational Age Recode 10,
with "Show Totals" allowed (Total rows are dropped).
"""
import csv
import io
import json
import logging
import os
import urllib.parse
from collections import OrderedDict
from datetime import datetime, timezone

try:  # package import (tests) vs. flat Lambda zip
    from . import region_rollup
except ImportError:  # pragma: no cover
    import region_rollup

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RAW_PREFIX = os.environ.get("RAW_PREFIX", "raw/natality/")
OUT_PREFIX = os.environ.get("OUT_PREFIX", "processed/natality/")
REGION_OUT_PREFIX = os.environ.get("REGION_OUT_PREFIX", "processed/natality_region/")
REGION_LEVEL = os.environ.get("REGION_LEVEL", "hsr")      # hsr = 8 admin regions, phr = 11
REGION_PERIOD = os.environ.get("REGION_PERIOD", "year")   # year | month
GA_COLUMN = os.environ.get("GA_COLUMN", "OE Gestational Age Recode 10")

COUNTY_COL = "County of Residence"
COUNTY_CODE_COL = "County of Residence Code"
YEAR_COL = "Year Code"
MONTH_COL = "Month Code"
BIRTHS_COL = "Births"

PRETERM = {"Under 20 weeks", "20 - 27 weeks", "28 - 31 weeks", "32 - 33 weeks", "34 - 36 weeks"}
TERM = {"37 - 38 weeks", "39 weeks", "40 weeks", "41 weeks", "42 weeks or more"}
UNKNOWN_GA = {"Unknown or Not Stated"}
MISSING_VALUES = {"", "Suppressed", "Not Available", "Not Applicable", "Missing"}

OUTPUT_FIELDS = [
    "county_fips", "county_name", "state", "year", "month",
    "births_total", "births_preterm", "births_term", "births_unknown_ga",
    "missing_cells", "preterm_rate", "unidentified_county", "source_file",
]


class DataQualityError(ValueError):
    """Raised when the input can't be trusted — fail loudly, don't guess."""


# ---------------------------------------------------------------- extract
def parse_wonder_export(text):
    """Return the data rows of a WONDER tab-delimited export as dicts.

    Stops at the '---' footer and drops 'Total' rows.
    """
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    header = next(reader, None)
    if not header:
        raise DataQualityError("Export is empty")
    header = [h.strip() for h in header]
    required = [COUNTY_COL, COUNTY_CODE_COL, YEAR_COL, MONTH_COL, GA_COLUMN, BIRTHS_COL]
    missing = [c for c in required if c not in header]
    if missing:
        raise DataQualityError(f"Missing columns {missing}. Check the WONDER Group By settings.")

    rows, totals_dropped = [], 0
    for raw in reader:
        if not raw or not any(cell.strip() for cell in raw):
            continue
        if raw[0].strip() == "---":
            break  # footer: dataset notes, query criteria, citation
        row = dict(zip(header, (cell.strip() for cell in raw)))
        if row.get("Notes") == "Total":
            totals_dropped += 1
            continue
        rows.append(row)
    return rows, totals_dropped


# -------------------------------------------------------------- transform
def _to_int(value, field):
    if value in MISSING_VALUES:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        raise DataQualityError(f"Non-numeric {field}: {value!r}")


def _fips(code):
    code = code.zfill(5)
    if len(code) != 5 or not code.isdigit():
        raise DataQualityError(f"Bad county FIPS: {code!r}")
    return code


def aggregate_county_month(rows, source_file=""):
    groups = OrderedDict()
    unknown_labels = set()

    for row in rows:
        fips = _fips(row[COUNTY_CODE_COL])
        year = _to_int(row[YEAR_COL], YEAR_COL)
        month = _to_int(row[MONTH_COL], MONTH_COL)
        if year is None or month is None or not 1 <= month <= 12:
            raise DataQualityError(f"Bad year/month for {fips}: {row[YEAR_COL]}/{row[MONTH_COL]}")
        label = row[GA_COLUMN]
        births = _to_int(row[BIRTHS_COL], BIRTHS_COL)

        name, _, state = row[COUNTY_COL].rpartition(", ")
        g = groups.setdefault((fips, year, month), {
            "county_fips": fips, "county_name": name or row[COUNTY_COL], "state": state,
            "year": year, "month": month,
            "births_total": 0, "births_preterm": 0, "births_term": 0, "births_unknown_ga": 0,
            "missing_cells": 0, "unidentified_county": fips.endswith("999"),
            "source_file": source_file,
        })

        if births is None:
            g["missing_cells"] += 1
            continue
        if label in PRETERM:
            g["births_preterm"] += births
        elif label in TERM:
            g["births_term"] += births
        elif label in UNKNOWN_GA:
            g["births_unknown_ga"] += births
        else:
            unknown_labels.add(label)
            continue
        g["births_total"] += births

    if unknown_labels:
        raise DataQualityError(f"Unrecognized gestational age labels: {sorted(unknown_labels)}")

    out = []
    for g in groups.values():
        denom = g["births_preterm"] + g["births_term"]  # known gestational age only
        g["preterm_rate"] = (
            round(g["births_preterm"] / denom, 4) if denom and g["missing_cells"] == 0 else None
        )
        out.append(g)
    return out


# ------------------------------------------------------------------- load
def to_csv(records):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for r in records:
        writer.writerow({k: ("" if r[k] is None else r[k]) for k in OUTPUT_FIELDS})
    return buf.getvalue()


def output_keys(key):
    stem = key[len(RAW_PREFIX):].rsplit(".", 1)[0]
    return (f"{OUT_PREFIX}{stem}.csv", f"{OUT_PREFIX}{stem}.manifest.json",
            f"{REGION_OUT_PREFIX}{stem}.csv")


def process_object(s3, bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    rows, totals_dropped = parse_wonder_export(body)
    records = aggregate_county_month(rows, source_file=key)

    csv_key, manifest_key, region_key = output_keys(key)
    s3.put_object(Bucket=bucket, Key=csv_key, Body=to_csv(records).encode(), ContentType="text/csv")

    lookup = region_rollup.load_region_lookup(REGION_LEVEL)
    region_records, coverage = region_rollup.rollup(records, lookup, REGION_LEVEL, REGION_PERIOD)
    s3.put_object(Bucket=bucket, Key=region_key, Body=region_rollup.to_csv(region_records).encode(),
                  ContentType="text/csv")

    manifest = {
        "source": f"s3://{bucket}/{key}",
        "output": f"s3://{bucket}/{csv_key}",
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "input_rows": len(rows),
        "total_rows_dropped": totals_dropped,
        "output_rows": len(records),
        "rows_with_missing_cells": sum(1 for r in records if r["missing_cells"]),
        "unidentified_county_rows": sum(1 for r in records if r["unidentified_county"]),
        "region_output": f"s3://{bucket}/{region_key}",
        "region_rows": len(region_records),
        "region_coverage": coverage,
    }
    s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest, indent=2).encode(),
                  ContentType="application/json")
    logger.info(json.dumps(manifest))
    return manifest


# ---------------------------------------------------------------- handler
def _default_s3():
    import boto3  # available in the Lambda runtime; tests inject a fake instead
    return boto3.client("s3")


def lambda_handler(event, context, s3=None):
    s3 = s3 or _default_s3()
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys
        if not key.startswith(RAW_PREFIX):
            logger.warning("Skipping %s: outside %s (guards against trigger loops)", key, RAW_PREFIX)
            continue
        results.append(process_object(s3, bucket, key))
    return {"processed": results}
