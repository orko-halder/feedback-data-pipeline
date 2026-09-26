"""
Unit tests for the pure functions in review_comprehend_processor.py.
No AWS calls, no mocking. Run: python3 tests/test_review_comprehend_processor.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from review_comprehend_processor import (  # noqa: E402
    build_processed_record,
    derive_processed_key,
    derive_raw_key,
    is_admissible,
    truncate_for_comprehend,
)

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def test_admission_gate():
    check("admissible True -> process", is_admissible({"admissible": True}) is True)
    check("admissible False -> skip", is_admissible({"admissible": False}) is False)
    # Fails closed. Validation files from the earlier validator have no
    # such key; refusing is the safe reading of a missing signal.
    check("key absent entirely -> skip", is_admissible({"quality_score": 1.0}) is False)
    check("admissible None -> skip", is_admissible({"admissible": None}) is False)
    # A high score must NOT override a fatal verdict -- the exact bug
    # this replaced.
    check("high score but not admissible -> skip",
          is_admissible({"quality_score": 0.875, "admissible": False}) is False)


def test_key_derivation():
    val_key = "validation-results/reviews/review_011_validation.json"
    check(
        "validation key -> raw key",
        derive_raw_key(val_key) == "raw-data/reviews/review_011.json",
    )
    check(
        "validation key -> processed key",
        derive_processed_key(val_key) == "processed-data/reviews/review_011_processed.json",
    )


def test_truncate():
    short = "This review is short."
    check("short text passes through unchanged", truncate_for_comprehend(short) == short)

    long_ascii = "a" * 6000
    result = truncate_for_comprehend(long_ascii)
    check("long ascii truncated to 5000 bytes", len(result.encode("utf-8")) == 5000)

    # Multibyte: 3 bytes per char. 2000 chars = 6000 bytes. Truncating
    # at a raw byte offset could land mid-character -- result must
    # still be valid UTF-8 and within the limit.
    multibyte = "é" * 4000  # 2 bytes each = 8000 bytes
    result_mb = truncate_for_comprehend(multibyte)
    check("multibyte stays within byte limit", len(result_mb.encode("utf-8")) <= 5000)
    check("multibyte result is still valid/decodable", isinstance(result_mb, str))


def test_build_processed_record():
    review = {
        "review_text": "Great product.",
        "product_id": "EAR-2200",
        "customer_id": "CUST-1001",
        "review_date": "2026-03-14",
        "rating": 5,
    }
    sentiment = {"Sentiment": "POSITIVE", "SentimentScore": {"Positive": 0.99}}
    record = build_processed_record(review, [{"Text": "Great"}], sentiment, [{"Text": "Great product"}])

    check("sentiment lifted to top level", record["sentiment"] == "POSITIVE")
    check("sentiment scores carried", record["sentiment_scores"] == {"Positive": 0.99})
    check("metadata carries product_id", record["metadata"]["product_id"] == "EAR-2200")
    check("metadata carries rating", record["metadata"]["rating"] == 5)
    check("original text preserved", record["original_text"] == "Great product.")


if __name__ == "__main__":
    test_admission_gate()
    test_key_derivation()
    test_truncate()
    test_build_processed_record()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")
