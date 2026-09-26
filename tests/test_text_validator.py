"""
Unit tests for validate_review() -- pure function, no AWS calls, no
mocking needed. Run: python3 tests/test_text_validator.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from text_validator import validate_review  # noqa: E402

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def test_severity_classification():
    """The case that broke production: every malformed record scores
    0.875, so the score alone cannot separate fatal from cosmetic."""
    fatal_cases = {
        "rating out of range": {"review_text": "A perfectly reasonable length review here.",
                                "product_id": "EAR-2200", "customer_id": "CUST-1",
                                "rating": 6, "review_date": "2026-03-14"},
        "missing product_id": {"review_text": "A perfectly reasonable length review here.",
                               "customer_id": "CUST-1", "rating": 4,
                               "review_date": "2026-03-14"},
        "text below min length": {"review_text": "Bad.", "product_id": "EAR-2200",
                                  "customer_id": "CUST-1", "rating": 2,
                                  "review_date": "2026-03-14"},
    }
    for label, review in fatal_cases.items():
        r = validate_review(review)
        check(f"FATAL blocks: {label}", r["admissible"] is False)
        check(f"FATAL recorded: {label}", len(r["fatal_failures"]) > 0)
        # All of these still score 0.875 -- proving the score cannot gate.
        check(f"...yet still scores 0.875: {label}", r["quality_score"] == 0.875)

    advisory_cases = {
        "malformed date": {"review_text": "A perfectly reasonable length review here.",
                           "product_id": "EAR-2200", "customer_id": "CUST-1",
                           "rating": 3, "review_date": "15-09-2026"},
        "missing customer_id": {"review_text": "A perfectly reasonable length review here.",
                                "product_id": "EAR-2200", "rating": 3,
                                "review_date": "2026-03-14"},
    }
    for label, review in advisory_cases.items():
        r = validate_review(review)
        check(f"ADVISORY admits: {label}", r["admissible"] is True)
        check(f"ADVISORY recorded: {label}", len(r["advisory_failures"]) > 0)
        check(f"no fatal flags: {label}", r["fatal_failures"] == [])


def test_valid_review():
    review = {
        "review_text": "This product works great and I would recommend it to anyone.",
        "product_id": "EAR-2200",
        "customer_id": "CUST-1001",
        "rating": 5,
        "review_date": "2026-03-14",
    }
    result = validate_review(review)
    check("valid review: quality_score == 1.0", result["quality_score"] == 1.0)
    check("valid review: all checks True", all(result["checks"].values()))
    check("valid review: admissible", result["admissible"] is True)


def test_missing_field():
    review = {
        "review_text": "This product works great and I would recommend it.",
        "customer_id": "CUST-1002",
        "rating": 4,
        "review_date": "2026-03-14",
    }  # product_id missing
    result = validate_review(review)
    check("missing product_id: has_product_id == False", result["checks"]["has_product_id"] is False)
    check("missing product_id: other checks still True", result["checks"]["has_customer_id"] is True)
    check("missing product_id: quality_score < 1.0", result["quality_score"] < 1.0)


def test_short_text():
    review = {
        "review_text": "Bad.",
        "product_id": "EAR-2200",
        "customer_id": "CUST-1003",
        "rating": 2,
        "review_date": "2026-03-14",
    }
    result = validate_review(review)
    check("short text: min_length == False", result["checks"]["min_length"] is False)
    check("short text: has_review_text still True (present, just short)", result["checks"]["has_review_text"] is True)


def test_out_of_range_rating():
    review = {
        "review_text": "This product works great and I would recommend it.",
        "product_id": "EAR-2200",
        "customer_id": "CUST-1004",
        "rating": 6,
        "review_date": "2026-03-14",
    }
    result = validate_review(review)
    check("rating=6: rating_in_range == False", result["checks"]["rating_in_range"] is False)


def test_bad_date_format():
    review = {
        "review_text": "This product works great and I would recommend it.",
        "product_id": "EAR-2200",
        "customer_id": "CUST-1005",
        "rating": 3,
        "review_date": "15-09-2026",
    }
    result = validate_review(review)
    check("bad date format: valid_date_format == False", result["checks"]["valid_date_format"] is False)


if __name__ == "__main__":
    test_severity_classification()
    test_valid_review()
    test_missing_field()
    test_short_text()
    test_out_of_range_rating()
    test_bad_date_format()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")
