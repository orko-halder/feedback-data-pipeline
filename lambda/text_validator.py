"""
Custom validation for unstructured customer review JSON files.

Glue Data Quality (Part 1 Step 1) validates the structured survey table --
schema-level, columnar checks. This Lambda covers the gap: per-record
content and structural validity for review files, which have no fixed
schema and can legitimately be missing fields.

Triggered on S3 ObjectCreated under raw-data/reviews/.

WHY SEVERITY LEVELS AND NOT JUST A SCORE
-----------------------------------------
The first version of this returned only quality_score -- the fraction of
checks that passed -- and downstream processing gated on score >= 0.7.
That failed in production, silently, and the failure is instructive.

Every broken record in our test set scores 0.875, because 7 of 8 checks
still pass. A rating of 6 on a 5-point scale and a date written
DD-MM-YYYY produce the identical score, so no single threshold can admit
one and reject the other. Both sailed through into business-facing
output, where the out-of-range rating formed its own bogus category in a
per-product breakdown.

The deeper flaw: an unweighted mean of boolean checks is not a property
of the DATA, it is a property of the CHECK LIST. Add two trivial checks
that always pass and every record's score rises -- the same record clears
a higher threshold without improving. The metric is gameable by whoever
writes the checks and is not comparable across validator versions.

So the two jobs are separated:
  - quality_score  -> a TREND signal, published to CloudWatch. Fine for
                      "is quality drifting week over week", because you
                      are comparing like with like.
  - admissible     -> an ADMISSION decision, based on whether any FATAL
                      check failed. Admission is a question about WHICH
                      things failed; an average discards exactly that.

FATAL vs ADVISORY
-----------------
FATAL means the record cannot be meaningfully processed downstream:
nothing to analyse, nothing to attribute it to, or a value that would
corrupt arithmetic. ADVISORY means imperfect but still usable, and in
several cases repairable later (Part 4's normalisation step).

customer_id is deliberately ADVISORY, which is arguable: you can still
learn "someone reported poor battery life on EAR-2200" without knowing
who. If this pipeline ever needed to close the loop with the individual
customer, it would become FATAL.
"""

import json
import re
from datetime import datetime
from urllib.parse import unquote_plus

import boto3

REQUIRED_FIELDS = ["review_text", "product_id", "customer_id", "rating", "review_date"]
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_REVIEW_LENGTH = 10

# A failed check in this set blocks the record from downstream processing,
# whatever the aggregate score says.
FATAL_CHECKS = {
    "has_review_text",  # nothing for Comprehend to analyse
    "has_product_id",  # cannot attribute the feedback to anything
    "min_length",  # too short to yield signal; still costs an API call
    "rating_in_range",  # corrupts any downstream arithmetic
}


def validate_review(review: dict) -> dict:
    """Runs every check independently -- a missing field fails its own check
    but doesn't block the others, so one bad record still yields a full
    diagnostic picture instead of stopping at the first error."""
    checks = {}

    for field in REQUIRED_FIELDS:
        checks[f"has_{field}"] = field in review and review[field] not in (None, "")

    text = review.get("review_text", "")
    checks["min_length"] = isinstance(text, str) and len(text) >= MIN_REVIEW_LENGTH

    rating = review.get("rating")
    checks["rating_in_range"] = isinstance(rating, int) and 1 <= rating <= 5

    date = review.get("review_date", "")
    checks["valid_date_format"] = bool(DATE_PATTERN.match(str(date)))

    failed = [name for name, ok in checks.items() if not ok]
    fatal_failures = [name for name in failed if name in FATAL_CHECKS]
    advisory_failures = [name for name in failed if name not in FATAL_CHECKS]

    passed = sum(1 for v in checks.values() if v)

    return {
        "checks": checks,
        # Kept for CloudWatch trending only -- NOT the admission decision.
        "quality_score": passed / len(checks),
        "fatal_failures": fatal_failures,
        "advisory_failures": advisory_failures,
        # The admission decision. Downstream gates on this, not on the score.
        "admissible": len(fatal_failures) == 0,
    }


def lambda_handler(event, context):
    s3 = boto3.client("s3")
    cloudwatch = boto3.client("cloudwatch")

    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    # S3 event notifications URL-encode the object key ("my file.json"
    # arrives as "my+file.json"). Used raw, the key 404s on get_object.
    key = unquote_plus(event["Records"][0]["s3"]["object"]["key"])

    if not key.endswith(".json"):
        return {"statusCode": 200, "body": json.dumps("Not a review JSON file")}

    response = s3.get_object(Bucket=bucket, Key=key)
    review = json.loads(response["Body"].read().decode("utf-8"))

    result = validate_review(review)
    result["file_name"] = key
    result["timestamp"] = datetime.utcnow().isoformat()

    # Two metrics, because they answer different questions. QualityScore
    # tracks drift; AdmissionRate tracks how much data is actually making
    # it through. A stable score with a collapsing admission rate is a
    # signal you would completely miss with only the first.
    cloudwatch.put_metric_data(
        Namespace="CustomerFeedback/TextQuality",
        MetricData=[
            {
                "MetricName": "QualityScore",
                "Value": result["quality_score"],
                "Unit": "None",
                "Dimensions": [{"Name": "Source", "Value": "TextReviews"}],
            },
            {
                "MetricName": "AdmissionRate",
                "Value": 1.0 if result["admissible"] else 0.0,
                "Unit": "None",
                "Dimensions": [{"Name": "Source", "Value": "TextReviews"}],
            },
        ],
    )

    validation_key = key.replace("raw-data/reviews", "validation-results/reviews").replace(
        ".json", "_validation.json"
    )
    s3.put_object(
        Bucket=bucket,
        Key=validation_key,
        Body=json.dumps(result, indent=2),
        ContentType="application/json",
    )

    return {"statusCode": 200, "body": json.dumps(result)}
