"""
Unit tests for processing/quality_review.py -- pure functions, no AWS.
Run: python3 tests/test_quality_review.py
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "processing"))
import quality_review as qr  # noqa: E402

FAILURES = []


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def doc(doc_id="review:r1", source="review", sentiment=None, rating=None,
        gap=None, text="some text", admissible=True, product="EAR-2200"):
    return {"doc_id": doc_id, "source_type": source, "product_id": product,
            "text": text,
            "signals": {"sentiment": sentiment, "rating": rating,
                        "rating_satisfaction_gap": gap},
            "provenance": {"admissible": admissible}}


def test_rating_vs_sentiment():
    check("NEGATIVE text rated 5 -> mismatch", qr.rating_sentiment_mismatch("NEGATIVE", 5)[0])
    check("POSITIVE text rated 1 -> mismatch", qr.rating_sentiment_mismatch("POSITIVE", 1)[0])
    check("POSITIVE text rated 5 -> agreement", not qr.rating_sentiment_mismatch("POSITIVE", 5)[0])
    check("NEGATIVE text rated 1 -> agreement", not qr.rating_sentiment_mismatch("NEGATIVE", 1)[0])
    check("NEUTRAL -> no verdict (rule cannot decide)",
          not qr.rating_sentiment_mismatch("NEUTRAL", 1)[0])
    check("MIXED -> no verdict", not qr.rating_sentiment_mismatch("MIXED", 5)[0])
    check("missing rating -> no verdict", not qr.rating_sentiment_mismatch("NEGATIVE", None)[0])
    check("missing sentiment -> no verdict", not qr.rating_sentiment_mismatch(None, 5)[0])
    check("lowercase label still handled", qr.rating_sentiment_mismatch("negative", 5)[0])
    check("divergence measures distance from the band",
          qr.rating_sentiment_mismatch("NEGATIVE", 5)[1] == 3
          and qr.rating_sentiment_mismatch("POSITIVE", 1)[1] == 3)
    check("rating 3 with NEGATIVE text is NOT flagged (inside no band)",
          not qr.rating_sentiment_mismatch("NEGATIVE", 3)[0])


def test_survey_gap():
    check("gap 4.0 -> mismatch", qr.survey_label_mismatch(4.0)[0])
    check("gap -3.0 -> mismatch (sign ignored)", qr.survey_label_mismatch(-3.0)[0])
    check("gap 0.5 -> not a mismatch", not qr.survey_label_mismatch(0.5)[0])
    check("gap exactly at the threshold -> mismatch",
          qr.survey_label_mismatch(qr.MIN_SURVEY_GAP)[0])
    check("gap None -> no verdict", not qr.survey_label_mismatch(None)[0])


def test_routing():
    v = qr.assess(doc(sentiment="NEGATIVE", rating=5))
    check("mismatch routes to review_queue", v["route"] == "review_queue")
    check("flag carries its rule and evidence",
          v["flags"][0]["rule"] == "sentiment_vs_rating" and "rated it 5/5" in v["flags"][0]["detail"])
    check("agreement routes to auto_accept",
          qr.assess(doc(sentiment="POSITIVE", rating=5))["route"] == "auto_accept")
    check("inadmissible routes to rejected, whatever else is true",
          qr.assess(doc(sentiment="NEGATIVE", rating=5, admissible=False))["route"] == "rejected")
    check("no sentiment but has text -> escalate_to_fm (no rule applies)",
          qr.assess(doc(source="image", sentiment=None, rating=None))["route"] == "escalate_to_fm")
    check("survey gap alone is enough to flag",
          qr.assess(doc(source="survey", sentiment="Satisfied", rating=1, gap=4.0))["route"]
          == "review_queue")
    check("excerpt is truncated for the queue",
          len(qr.assess(doc(text="x" * 500))["text_excerpt"]) == 160)


def test_summary_and_scoring():
    verdicts = [qr.assess(d) for d in (
        doc("review:r1", sentiment="NEGATIVE", rating=5),          # flagged
        doc("review:r2", sentiment="POSITIVE", rating=5),          # accepted
        doc("review:r3", sentiment="POSITIVE", rating=4),          # accepted
        doc("image:i1", source="image"),                           # escalated
        doc("review:r4", sentiment="NEGATIVE", rating=1, admissible=False),  # rejected
    )]
    s = qr.summarise(verdicts)
    check("routes counted", s["by_route"]["review_queue"] == 1 and s["by_route"]["rejected"] == 1)
    check("rate is over what COULD be judged, not everything",
          s["judged"] == 3 and s["mismatch_rate"] == round(1 / 3, 4))

    truth = {"planted_rating_mismatches": ["review:r1", "review:r9"]}
    sc = qr.score_against_truth(verdicts, truth)
    check("true positive found", sc["true_positives"] == ["review:r1"])
    check("missed planted record reported", sc["missed"] == ["review:r9"])
    check("precision 1.0 (nothing extra flagged)", sc["precision"] == 1.0)
    check("recall 0.5 (one of two found)", sc["recall"] == 0.5)
    check("f1 computed", sc["f1"] == round(2 * 1.0 * 0.5 / 1.5, 3))
    check("no flags -> precision None, not a crash",
          qr.score_against_truth([qr.assess(doc(sentiment="POSITIVE", rating=5))],
                                 truth)["precision"] is None)


if __name__ == "__main__":
    test_rating_vs_sentiment()
    test_survey_gap()
    test_routing()
    test_summary_and_scoring()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
