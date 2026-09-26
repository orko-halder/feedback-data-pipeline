"""
Enriches validated customer reviews with Amazon Comprehend.

WHY THIS TRIGGERS OFF validation-results/ AND NOT raw-data/
-----------------------------------------------------------
Part 1's text validator writes a quality score per review. If this
processor listened to raw-data/ directly it would happily run
Comprehend over records we already know are broken -- paying per API
call to extract sentiment from a 4-character review, and polluting
downstream FM input with garbage. Chaining off the validation output
makes the quality gate load-bearing: a review only reaches paid
enrichment if it cleared the bar. That ordering is the whole point of
having built validation first.

WHY COMPREHEND AND NOT A FOUNDATION MODEL FOR THIS STEP
--------------------------------------------------------
Claude could extract entities and sentiment too, and Part 3 will use
it for the actual reasoning. But for mechanical, high-volume, per-
record extraction, Comprehend is the better tool: purpose-built,
deterministic output shape (no parsing prose or coaxing JSON), lower
cost per record, and no prompt to drift. Reserve the FM for work that
needs judgment -- synthesis across records, not tagging one at a time.
The pipeline's job is to hand the FM clean, pre-structured input.

WHY IT GATES ON "admissible" AND NOT ON THE SCORE
--------------------------------------------------
The first version gated on quality_score >= 0.7 and let every broken
record through, because all of them score 0.875 (7 of 8 checks pass).
An averaged score cannot distinguish "date is formatted wrong" from
"rating is 6 on a 5-point scale" -- but only one of those makes the
record unusable. The validator now decides that question itself and
publishes a boolean; this function reads the decision rather than
re-deriving it from a number that threw the information away.

Note it fails CLOSED: a validation record with no "admissible" key at
all is treated as not admissible. Validation files written by the
earlier version lack the field, and the safe reading of a missing
signal is refusal, not permission.

WHY THE TRUNCATION GUARD
-------------------------
Comprehend's synchronous single-document APIs cap input at 5,000 UTF-8
bytes. Our synthetic reviews are nowhere near that, but real customer
feedback absolutely is -- and the failure mode is a hard API error
mid-pipeline, not a graceful degrade. Guarding here is cheap.
"""

import json
from urllib.parse import unquote_plus

import boto3

COMPREHEND_MAX_BYTES = 5000


def is_admissible(validation_result: dict) -> bool:
    """Pure. Reads the validator's admission decision. Fails closed: a
    missing "admissible" key means refuse, because an absent signal is
    not evidence of quality."""
    return validation_result.get("admissible") is True


def derive_raw_key(validation_key: str) -> str:
    """validation-results/reviews/review_011_validation.json
    -> raw-data/reviews/review_011.json"""
    return validation_key.replace("validation-results/reviews", "raw-data/reviews").replace(
        "_validation.json", ".json"
    )


def derive_processed_key(validation_key: str) -> str:
    """validation-results/reviews/review_011_validation.json
    -> processed-data/reviews/review_011_processed.json"""
    return validation_key.replace("validation-results/reviews", "processed-data/reviews").replace(
        "_validation.json", "_processed.json"
    )


def truncate_for_comprehend(text: str, max_bytes: int = COMPREHEND_MAX_BYTES) -> str:
    """Pure. Truncates on BYTE length, not character count -- a
    multibyte character near the boundary would otherwise slip the
    payload over the limit. Cuts back to a clean character boundary."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def build_processed_record(
    review: dict, entities: list, sentiment: dict, key_phrases: list
) -> dict:
    """Pure assembly of the enriched record. Kept separate from the
    API calls so the output shape can be tested without AWS."""
    return {
        "original_text": review.get("review_text", ""),
        "entities": entities,
        "sentiment": sentiment.get("Sentiment"),
        "sentiment_scores": sentiment.get("SentimentScore", {}),
        "key_phrases": key_phrases,
        "metadata": {
            "product_id": review.get("product_id", ""),
            "customer_id": review.get("customer_id", ""),
            "review_date": review.get("review_date", ""),
            "rating": review.get("rating"),
        },
    }


def lambda_handler(event, context):
    s3 = boto3.client("s3")
    comprehend = boto3.client("comprehend")

    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    # S3 event notifications URL-encode the object key ("my file.json"
    # arrives as "my+file.json"). Used raw, the key 404s on get_object.
    validation_key = unquote_plus(event["Records"][0]["s3"]["object"]["key"])

    validation_result = json.loads(
        s3.get_object(Bucket=bucket, Key=validation_key)["Body"].read().decode("utf-8")
    )

    if not is_admissible(validation_result):
        fatal = validation_result.get("fatal_failures", ["(no admissible flag present)"])
        return {
            "statusCode": 200,
            "body": json.dumps(f"Skipped: fatal validation failures {fatal}"),
        }

    raw_key = derive_raw_key(validation_key)
    review = json.loads(s3.get_object(Bucket=bucket, Key=raw_key)["Body"].read().decode("utf-8"))

    text = truncate_for_comprehend(review.get("review_text", ""))
    if not text:
        return {"statusCode": 200, "body": json.dumps("Skipped: empty review text")}

    entities = comprehend.detect_entities(Text=text, LanguageCode="en")["Entities"]
    sentiment = comprehend.detect_sentiment(Text=text, LanguageCode="en")
    key_phrases = comprehend.detect_key_phrases(Text=text, LanguageCode="en")["KeyPhrases"]

    processed = build_processed_record(review, entities, sentiment, key_phrases)

    s3.put_object(
        Bucket=bucket,
        Key=derive_processed_key(validation_key),
        Body=json.dumps(processed, indent=2),
        ContentType="application/json",
    )

    return {
        "statusCode": 200,
        "body": json.dumps({"processed": derive_processed_key(validation_key)}),
    }
