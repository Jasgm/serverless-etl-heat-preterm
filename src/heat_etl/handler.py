"""Heat Lambdas.

1) build_thresholds_handler  (run manually once per baseline)
   event: {"start_year": 1991, "end_year": 2020}
   reads  raw/temperature/tmax-YYYYMM-cty-scaled.csv[.gz] for every baseline month
   writes processed/heat/thresholds/thresholds_1991_2020.csv

2) lambda_handler  (S3 ObjectCreated on raw/temperature/)
   reads one month file, counts extreme-heat days against EACH baseline's thresholds
   writes processed/heat/county_month/<baseline>/tmax-YYYYMM.csv (+ .manifest.json)

Order matters: thresholds must exist before monthly files are processed. If they don't,
the monthly function fails loudly; rerun it with scripts/backfill (or re-upload) afterwards.
"""
import csv
import gzip
import io
import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone

try:
    from . import monthly, nclimgrid, thresholds
except ImportError:  # pragma: no cover
    import monthly, nclimgrid, thresholds

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RAW_PREFIX = os.environ.get("HEAT_RAW_PREFIX", "raw/temperature/")
THRESHOLD_PREFIX = os.environ.get("THRESHOLD_PREFIX", "processed/heat/thresholds/")
OUT_PREFIX = os.environ.get("HEAT_OUT_PREFIX", "processed/heat/county_month/")
BASELINES = [b.strip() for b in os.environ.get("BASELINES", "1991-2020,1981-2010").split(",")]


def _read_text(s3, bucket, key):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".gz"):
        body = gzip.decompress(body)
    return body.decode("utf-8")


def _threshold_key(baseline):
    return f"{THRESHOLD_PREFIX}thresholds_{baseline.replace('-', '_')}.csv"


def _list_keys(s3, bucket, prefix):
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kw)          # max 1000 keys per page: must paginate
        keys += [o["Key"] for o in resp.get("Contents", [])]
        if not resp.get("IsTruncated"):
            return keys
        token = resp["NextContinuationToken"]


# ------------------------------------------------------------ thresholds
def build_thresholds(s3, bucket, start_year, end_year, pct=95.0):
    builder = thresholds.ThresholdBuilder(start_year, end_year, pct)
    for key in sorted(_list_keys(s3, bucket, RAW_PREFIX)):
        try:
            var, year, month = nclimgrid.parse_filename(key)
        except nclimgrid.NClimGridFormatError:
            continue
        if var != "tmax" or not start_year <= year <= end_year:
            continue
        days = nclimgrid.parse_county_file(_read_text(s3, bucket, key), expect=(var, year, month))
        builder.add_month(year, month, days)
    rows = builder.build()
    out_key = _threshold_key(f"{start_year}-{end_year}")
    s3.put_object(Bucket=bucket, Key=out_key, Body=thresholds.to_csv(rows).encode(), ContentType="text/csv")
    summary = {"output": f"s3://{bucket}/{out_key}", "counties": len(rows),
               "counties_without_threshold": [r["county_fips"] for r in rows if r["tmax_threshold_c"] is None]}
    logger.info(json.dumps(summary))
    return summary


def build_thresholds_handler(event, context, s3=None):
    s3 = s3 or _default_s3()
    return build_thresholds(s3, event.get("bucket", os.environ.get("BUCKET")),
                            int(event["start_year"]), int(event["end_year"]), float(event.get("percentile", 95)))


# --------------------------------------------------------------- monthly
def process_month(s3, bucket, key):
    var, year, month = nclimgrid.parse_filename(key)
    if var != "tmax":
        logger.info("Skipping %s: only tmax is used", key)
        return None
    county_days = nclimgrid.parse_county_file(_read_text(s3, bucket, key), expect=(var, year, month))

    results = []
    for baseline in BASELINES:
        try:
            th = thresholds.from_csv(_read_text(s3, bucket, _threshold_key(baseline)))
        except s3_missing_errors(s3) as exc:
            raise RuntimeError(f"Thresholds for {baseline} not built yet; run build_thresholds first") from exc
        records, no_threshold = monthly.count_extreme_days(year, month, county_days, th, baseline)
        stem = f"{OUT_PREFIX}{baseline}/tmax-{year}{month:02d}"
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=monthly.MONTHLY_FIELDS, lineterminator="\n")
        w.writeheader(); w.writerows(records)
        s3.put_object(Bucket=bucket, Key=f"{stem}.csv", Body=buf.getvalue().encode(), ContentType="text/csv")
        manifest = {
            "source": f"s3://{bucket}/{key}", "output": f"s3://{bucket}/{stem}.csv", "baseline": baseline,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "counties_in_file": len(county_days), "output_rows": len(records),
            "incomplete_county_months": sum(1 for r in records if not r["complete"]),
            "counties_without_threshold": no_threshold,
        }
        s3.put_object(Bucket=bucket, Key=f"{stem}.manifest.json",
                      Body=json.dumps(manifest, indent=2).encode(), ContentType="application/json")
        results.append(manifest)
    return results


def s3_missing_errors(s3):
    """Exception types meaning 'object not found' for real boto3 clients and test fakes."""
    errs = (KeyError,)
    exc_ns = getattr(s3, "exceptions", None)
    if exc_ns is not None and hasattr(exc_ns, "NoSuchKey"):
        errs += (exc_ns.NoSuchKey,)
    return errs


def _default_s3():
    import boto3
    return boto3.client("s3")


def lambda_handler(event, context, s3=None):
    s3 = s3 or _default_s3()
    out = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        if not key.startswith(RAW_PREFIX):
            logger.warning("Skipping %s: outside %s", key, RAW_PREFIX)
            continue
        res = process_month(s3, bucket, key)
        if res:
            out += res
    return {"processed": out}
