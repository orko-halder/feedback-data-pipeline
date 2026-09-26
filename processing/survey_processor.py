#!/usr/bin/env python3
"""SageMaker Processing job: raw survey CSV -> per-respondent JSON records.

WHERE THIS RUNS
SageMaker Processing COPIES S3 -> container local disk, runs this script, then
copies local disk -> S3. Nothing streams. The paths below are the mount points
SageMaker creates; they are not S3 paths:

    /opt/ml/processing/input/data     <- raw-data/surveys/     (ProcessingInput)
    /opt/ml/processing/output         -> processed-data/surveys/ (ProcessingOutput)

STDLIB ONLY, deliberately. The built-in SKLearn image ships pandas, but this
job is a 30-row parse: stdlib keeps the script runnable (and unit-testable)
outside the container, on any machine, with no environment to reproduce.

SEVERITY MODEL -- same as the review validator, so 'admissible' means the same
thing across all four data types: FATAL = the record cannot be trusted
downstream; ADVISORY = worth recording, still usable.
"""
import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone

INPUT_DIR = "/opt/ml/processing/input/data"
OUTPUT_DIR = "/opt/ml/processing/output"

SATISFACTION_SCALE = {
    "Very Dissatisfied": 1,
    "Dissatisfied": 2,
    "Neutral": 3,
    "Satisfied": 4,
    "Very Satisfied": 5,
}
RATING_FIELDS = ("product_rating", "service_rating")
FATAL = "FATAL"
ADVISORY = "ADVISORY"


def parse_rating(value: str):
    """Returns an int 1-5, or None. Out-of-range and non-numeric are both None:
    Glue DQ already flags them at the column level (ColumnValues in [1..5])."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None


def parse_date(value: str):
    """ISO date only. Returns the string back, or None -- never a datetime,
    so the record stays JSON-serialisable without a custom encoder."""
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


def check_row(row: dict) -> list:
    """Every issue found, each with a severity. Order is stable for tests."""
    issues = []
    if not str(row.get("customer_id", "")).strip():
        issues.append({"check": "customer_id_present", "severity": FATAL})
    if parse_date(row.get("survey_date")) is None:
        issues.append({"check": "survey_date_valid", "severity": FATAL})
    for field in RATING_FIELDS:
        if parse_rating(row.get(field)) is None:
            issues.append({"check": f"{field}_in_1_5", "severity": FATAL})
    if row.get("overall_satisfaction") not in SATISFACTION_SCALE:
        issues.append({"check": "overall_satisfaction_known_label", "severity": FATAL})
    if not str(row.get("product_id", "")).strip():
        issues.append({"check": "product_id_present", "severity": ADVISORY})
    if not str(row.get("comments", "")).strip():
        issues.append({"check": "comments_present", "severity": ADVISORY})
    if not str(row.get("improvement_area", "")).strip():
        issues.append({"check": "improvement_area_present", "severity": ADVISORY})
    return issues


def rating_satisfaction_gap(ratings: list, ordinal):
    """Stated satisfaction MINUS mean numeric rating, rounded to 2dp.

    Positive  -> says happier than the numbers suggest.
    Negative  -> numbers better than the stated mood.
    Part 4 uses this to find records worth a closer look; it is a DISCREPANCY
    measure, not a quality score. None when either side is missing -- a gap of
    0.0 would be a lie about missing data.
    """
    present = [r for r in ratings if r is not None]
    if ordinal is None or not present:
        return None
    return round(ordinal - sum(present) / len(present), 2)


def build_record(row: dict, row_number: int) -> dict:
    issues = check_row(row)
    ratings = [parse_rating(row.get(f)) for f in RATING_FIELDS]
    ordinal = SATISFACTION_SCALE.get(row.get("overall_satisfaction"))
    comments = str(row.get("comments", "")).strip()
    return {
        "row_number": row_number,
        "customer_id": str(row.get("customer_id", "")).strip() or None,
        # Added when the dataset was rebuilt: without it the survey channel
        # (the largest by volume) cannot be joined to a product at all.
        "product_id": str(row.get("product_id", "")).strip() or None,
        "survey_date": parse_date(row.get("survey_date")),
        "product_rating": ratings[0],
        "service_rating": ratings[1],
        "overall_satisfaction": row.get("overall_satisfaction") or None,
        "satisfaction_ordinal": ordinal,
        "rating_satisfaction_gap": rating_satisfaction_gap(ratings, ordinal),
        "improvement_area": str(row.get("improvement_area", "")).strip() or None,
        "comments": comments or None,
        "has_comment": bool(comments),
        "admissible": not any(i["severity"] == FATAL for i in issues),
        "issues": issues,
    }


def summarise(records: list) -> dict:
    admissible = [r for r in records if r["admissible"]]

    def mean(field):
        vals = [r[field] for r in admissible if r[field] is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "rows_read": len(records),
        "admissible": len(admissible),
        "rejected": len(records) - len(admissible),
        "admission_rate": round(len(admissible) / len(records), 3) if records else None,
        "mean_product_rating": mean("product_rating"),
        "mean_service_rating": mean("service_rating"),
        "mean_satisfaction_ordinal": mean("satisfaction_ordinal"),
        "with_comment": sum(1 for r in admissible if r["has_comment"]),
        "improvement_areas": dict(Counter(
            r["improvement_area"] for r in admissible if r["improvement_area"]).most_common()),
        "issue_counts": dict(Counter(
            i["check"] for r in records for i in r["issues"]).most_common()),
    }


def process_csv(path: str) -> dict:
    with open(path, newline="", encoding="utf-8") as f:
        # start=2: row 1 is the header, so numbers match what a spreadsheet shows
        records = [build_record(row, n) for n, row in enumerate(csv.DictReader(f), start=2)]
    return {
        "source_file": os.path.basename(path),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarise(records),
        "records": records,
    }


def main(input_dir: str, output_dir: str) -> None:
    csvs = sorted(f for f in os.listdir(input_dir) if f.lower().endswith(".csv"))
    if not csvs:
        raise SystemExit(f"no CSV found in {input_dir}")  # non-zero exit -> job Failed
    os.makedirs(output_dir, exist_ok=True)
    for name in csvs:
        result = process_csv(os.path.join(input_dir, name))
        out = os.path.join(output_dir, f"{os.path.splitext(name)[0]}_processed.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        s = result["summary"]
        print(f"{name}: {s['rows_read']} rows, {s['admissible']} admissible -> {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", default=INPUT_DIR)
    p.add_argument("--output-dir", default=OUTPUT_DIR)
    a = p.parse_args()
    main(a.input_dir, a.output_dir)
