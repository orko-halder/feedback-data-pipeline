"""
Unit tests for processing/feedback_document.py -- no AWS, no network.

Fixtures are REAL: each source record is built by the SAME function the Lambda
uses, fed the saved API responses in docs/api-responses/. Nothing here is a
hand-written approximation of what a processor emits.

Run: python3 tests/test_feedback_document.py
"""

import json
import os
import sys
import types

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "processing"))
sys.path.insert(0, os.path.join(ROOT, "lambda"))
sys.modules.setdefault("boto3", types.ModuleType("boto3"))

import feedback_document as fd  # noqa: E402
import image_textract_processor as img  # noqa: E402
import review_comprehend_processor as rev  # noqa: E402
import survey_processor as sv  # noqa: E402

FAILURES = []
API = os.path.join(ROOT, "docs", "api-responses")


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def load(name):
    with open(os.path.join(API, name), encoding="utf-8") as f:
        d = json.load(f)
    return d.get("response", d)


# ---- real records, built by the real processors -------------------------
with open(os.path.join(ROOT, "data", "raw", "reviews", "review_001.json"), encoding="utf-8") as _f:
    RAW_REVIEW = json.load(_f)
REVIEW_REC = rev.build_processed_record(
    RAW_REVIEW,
    load("comprehend_detect_entities.json").get("Entities", []),
    load("comprehend_detect_sentiment.json"),
    load("comprehend_detect_key_phrases.json").get("KeyPhrases", []),
)
IMAGE_REC = img.build_processed_record(
    "raw-data/images/EAR-2200_CUST-2001.png",
    load("textract_analyze_document_forms.json")["Blocks"],
)
CALL_REC = load("pipeline_output_call_001_processed.json")
SURVEY_OUT = sv.process_csv(os.path.join(ROOT, "data", "raw", "surveys", "surveys.csv"))

KEYS = {
    "doc_id",
    "source_type",
    "source_key",
    "customer_id",
    "product_id",
    "occurred_on",
    "order_ref",
    "text",
    "signals",
    "provenance",
}


def test_shape_is_identical_across_sources():
    docs = (
        fd.normalise(REVIEW_REC, "processed-data/reviews/review_001_processed.json")
        + fd.normalise(CALL_REC, "processed-data/calls/call_001_processed.json")
        + fd.normalise(IMAGE_REC, "processed-data/images/r_processed.json")
        + fd.normalise(SURVEY_OUT, "processed-data/surveys/surveys_processed.json")
    )
    check("all four sources normalise", len(docs) == 3 + 30)
    check("every doc has exactly the same top-level keys", all(set(d) == KEYS for d in docs))
    check(
        "every doc has the same signals keys", len({tuple(sorted(d["signals"])) for d in docs}) == 1
    )
    check(
        "every doc has the same provenance keys",
        len({tuple(sorted(d["provenance"])) for d in docs}) == 1,
    )
    check("doc_ids are unique", len({d["doc_id"] for d in docs}) == len(docs))
    check(
        "doc_id is prefixed by source type",
        all(d["doc_id"].startswith(d["source_type"] + ":") for d in docs),
    )
    json.dumps(docs)
    check("whole batch is JSON-serialisable", True)


def test_review():
    d = fd.normalise(REVIEW_REC, "processed-data/reviews/review_001_processed.json")[0]
    check("review: doc_id from the key", d["doc_id"] == "review:review_001")
    check("review: verbatim text carried", d["text"] == RAW_REVIEW["review_text"])
    check("review: rating from metadata", d["signals"]["rating"] == RAW_REVIEW["rating"])
    check(
        "review: customer/product ids carried",
        (d["customer_id"], d["product_id"])
        == (RAW_REVIEW["customer_id"], RAW_REVIEW["product_id"]),
    )
    check("review: date carried", d["occurred_on"] == RAW_REVIEW["review_date"])
    check("review: sentiment from Comprehend", d["signals"]["sentiment"] == REVIEW_REC["sentiment"])
    check(
        "review: confidence matches the CHOSEN label's score",
        d["signals"]["sentiment_confidence"]
        == round(REVIEW_REC["sentiment_scores"][REVIEW_REC["sentiment"].title()], 4),
    )
    blob = json.dumps(d)
    check(
        "review: entities and key_phrases dropped",
        "entities" not in blob and "key_phrases" not in blob,
    )


def test_call():
    d = fd.normalise(CALL_REC, "processed-data/calls/call_001_processed.json")[0]
    check("call: doc_id from the AUDIO key, not the job name", d["doc_id"] == "call:call_001")
    check(
        "call: text is rendered dialogue with roles",
        d["text"].startswith("agent: Thank you for calling")
        and "\ncustomer: Hi, my wireless" in d["text"],
    )
    check("call: one line per turn", len(d["text"].splitlines()) == len(CALL_REC["turns"]))
    check(
        "call: sentiment is the CUSTOMER's, not an average",
        d["signals"]["sentiment"] == CALL_REC["sentiment"]["customer_overall"]["sentiment"],
    )
    check(
        "call: sentiment_source names the customer-turn basis",
        d["signals"]["sentiment_source"] == "comprehend/customer_turns",
    )
    check(
        "call: role guess is declared in trust_notes",
        any("POSITIONAL GUESS" in n for n in d["provenance"]["trust_notes"]),
    )
    blob = json.dumps(d)
    check(
        "call: timings and sentiment_docs dropped",
        "start_time" not in blob and "sentiment_docs" not in blob,
    )

    unknown = json.loads(json.dumps(CALL_REC))
    for v in unknown["speakers"].values():
        v.update(role_guess="unknown", role_method="not_inferred:speaker_count=3")
    d2 = fd.from_call(unknown, "k")
    check(
        "call: unknown roles produce their own trust note",
        any("unknown" in n for n in d2["provenance"]["trust_notes"]),
    )
    check("call: unknown roles render as 'unknown:' lines", d2["text"].startswith("unknown:"))

    failed = {
        "status": "FAILED",
        "turns": [],
        "speakers": {},
        "sentiment": {},
        "source_audio": "x/y.mp3",
    }
    check(
        "call: FAILED job is not admissible",
        fd.from_call(failed, "k")["provenance"]["admissible"] is False,
    )


def test_survey():
    docs = fd.normalise(SURVEY_OUT, "processed-data/surveys/surveys_processed.json")
    check("survey: one doc PER ROW", len(docs) == 30)
    check("survey: doc_id uses row number", docs[0]["doc_id"] == "survey:row_2")
    with_comment = [d for d in docs if d["text"]]
    check(
        "survey: text is the comment only",
        all(d["text"] in [r["comments"] for r in SURVEY_OUT["records"]] for d in with_comment),
    )
    check(
        "survey: gap carried into signals",
        any(d["signals"]["rating_satisfaction_gap"] is not None for d in docs),
    )
    check(
        "survey: self-reported label is flagged as such",
        docs[0]["signals"]["sentiment_source"] == "self_reported_label",
    )
    check(
        "survey: self-report caveat in every row",
        all(any("self-reported" in n for n in d["provenance"]["trust_notes"]) for d in docs),
    )
    bad = [d for d in docs if not d["provenance"]["admissible"]]
    check("survey: the 2 seeded bad rows are inadmissible", len(bad) == 2)
    check(
        "survey: failure reason is stated, not just a flag",
        all(any("failed validation" in n for n in d["provenance"]["trust_notes"]) for d in bad),
    )


def test_image():
    d = fd.normalise(IMAGE_REC, "processed-data/images/x_processed.json")[0]
    check("image: text is the OCR full_text", d["text"] == IMAGE_REC["full_text"].strip())
    check(
        "image: ids parsed from the filename",
        (d["customer_id"], d["product_id"]) == ("CUST-2001", "EAR-2200"),
    )
    odd = img.build_processed_record(
        "raw-data/images/scan (1).png", load("textract_analyze_document_forms.json")["Blocks"]
    )
    check(
        "image: unparseable filename -> null ids, no crash",
        fd.from_image(odd, "k")["customer_id"] is None,
    )
    check("image: no sentiment invented", d["signals"]["sentiment"] is None)
    check(
        "image: OCR-error caveat present", any("OCR" in n for n in d["provenance"]["trust_notes"])
    )
    check(
        "image: textract_key_values deliberately NOT sent",
        "textract_key_values" not in json.dumps(d),
    )
    check(
        "image: and the omission is EXPLAINED to the model",
        any("unreliable" in n for n in d["provenance"]["trust_notes"]),
    )


def test_order_ref_join():
    """The one deterministic cross-channel link in the dataset."""
    call = fd.normalise(CALL_REC, "processed-data/calls/call_001_processed.json")[0]
    image = fd.normalise(IMAGE_REC, "processed-data/images/x_processed.json")[0]
    check(
        "call: order ref recovered from spoken digits ('order 9001')",
        call["order_ref"] == "ORD-9001",
    )
    check(
        "image: order ref recovered from printed 'Order #ORD-9001'",
        image["order_ref"] == "ORD-9001",
    )
    check(
        "the two match -> a call can be joined to a product WITHOUT guessing",
        call["order_ref"] == image["order_ref"] and image["product_id"] == "EAR-2200",
    )
    check(
        "a review with no order number gets None",
        fd.normalise(REVIEW_REC, "processed-data/reviews/review_001_processed.json")[0]["order_ref"]
        is None,
    )


def test_batch_selection():
    docs = fd.normalise(
        REVIEW_REC, "processed-data/reviews/review_001_processed.json"
    ) + fd.normalise(SURVEY_OUT, "processed-data/surveys/surveys_processed.json")
    sent = fd.admissible_only(docs)
    check("batch: inadmissible rows excluded", all(d["provenance"]["admissible"] for d in sent))
    check("batch: empty-text docs excluded (nothing to reason over)", all(d["text"] for d in sent))
    m = fd.manifest(docs, sent)
    check(
        "manifest: totals are consistent",
        m["documents_total"] == len(docs)
        and m["documents_sent"] == len(sent)
        and m["excluded"] == len(docs) - len(sent),
    )
    check("manifest: counts by source", m["by_source"]["survey"] == 30)
    check("manifest: excluded count is non-zero and visible", m["excluded"] > 0)


def test_unknown_prefix():
    try:
        fd.normalise({}, "processed-data/podcasts/x.json")
        check("unknown prefix raises rather than silently dropping", False)
    except ValueError:
        check("unknown prefix raises rather than silently dropping", True)


if __name__ == "__main__":
    test_shape_is_identical_across_sources()
    test_review()
    test_call()
    test_survey()
    test_image()
    test_order_ref_join()
    test_batch_selection()
    test_unknown_prefix()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
