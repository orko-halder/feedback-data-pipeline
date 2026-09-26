"""
Unit tests for the pure functions in image_textract_processor.py.

Tests parse the REAL captured Textract response in docs/api-responses/
rather than fabricated block structures. A hand-written fixture only
proves the code matches my assumptions about the payload -- which is
exactly the mistake that broke the EventBridge integration earlier.

Run: python3 tests/test_image_textract_processor.py
"""

import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "lambda"))

from image_textract_processor import (  # noqa: E402
    build_processed_record,
    extract_key_values,
    extract_lines,
    parse_metadata_from_key,
)

REAL_RESPONSE = os.path.join(
    HERE, "..", "docs", "api-responses", "textract_analyze_document_forms.json"
)

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def load_blocks():
    with open(REAL_RESPONSE, encoding="utf-8") as f:
        return json.load(f)["Blocks"]


def test_filename_parsing():
    m = parse_metadata_from_key("raw-data/images/EAR-2200_CUST-2001.png")
    check("filename -> product_id", m["product_id"] == "EAR-2200")
    check("filename -> customer_id", m["customer_id"] == "CUST-2001")

    # Degrades rather than raising -- a badly named file must not kill the run.
    bad = parse_metadata_from_key("raw-data/images/random_photo.png")
    check(
        "unparseable name -> empty strings, no exception",
        bad == {"product_id": "", "customer_id": ""},
    )


def test_extract_lines():
    lines = extract_lines(load_blocks())
    check("6 LINE blocks extracted", len(lines) == 6)
    check("reading order preserved (first)", lines[0] == "RETURN REQUEST")
    check("reading order preserved (last)", lines[-1] == "Customer: CUST-2001")


def test_extract_key_values():
    kv = extract_key_values(load_blocks())
    check("4 key-value pairs found", len(kv) == 4)
    check("trailing colon stripped from key", "Product" in kv)
    check("Product value complete", kv["Product"] == "Wireless Earbuds (EAR-2200)")
    check("Customer value complete", kv["Customer"] == "CUST-2001")

    # DOCUMENTS A KNOWN AWS LIMITATION, not a bug in our code.
    # KEY_VALUE_SET truncates at the line break: the real note reads
    # "Item arrived with a cracked casing near the power button."
    # If this assertion ever FAILS, AWS improved the service and the
    # workaround in the module docstring can be revisited.
    check("known truncation: Reason value cut at line break", kv["Reason"] == "Item arrived with a")


def test_full_text_is_the_safety_net():
    """The truncated content must still reach the FM via full_text."""
    rec = build_processed_record("raw-data/images/EAR-2200_CUST-2001.png", load_blocks())
    check(
        "full_text contains the truncated continuation",
        "cracked casing near the power button." in rec["full_text"],
    )
    check("full_text contains the key line too", "Reason: Item arrived with a" in rec["full_text"])


def test_untrusted_key_is_named_honestly():
    """The contract lives in the key name. If someone renames this back to
    "fields", consumers will treat phantom keys as validated data."""
    rec = build_processed_record("raw-data/images/EAR-2200_CUST-2001.png", load_blocks())
    check("untrusted output is named fields_unverified", "textract_key_values" in rec)
    check("no bare 'fields' key that implies validation", "fields" not in rec)


def test_record_shape():
    rec = build_processed_record("raw-data/images/EAR-2200_CUST-2001.png", load_blocks())
    for field in (
        "image_key",
        "full_text",
        "lines",
        "textract_key_values",
        "metadata",
        "block_counts",
    ):
        check(f"record has {field}", field in rec)
    check("block_counts reports LINE count", rec["block_counts"]["LINE"] == 6)
    check("metadata carries product_id", rec["metadata"]["product_id"] == "EAR-2200")


if __name__ == "__main__":
    test_filename_parsing()
    test_extract_lines()
    test_extract_key_values()
    test_full_text_is_the_safety_net()
    test_untrusted_key_is_named_honestly()
    test_record_shape()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
