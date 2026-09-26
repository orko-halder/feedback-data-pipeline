#!/usr/bin/env python3
"""Part 4: deterministic quality review -- the cheap tier.

WHAT THIS IS FOR
Part 3 has a foundation model find contradictions by judgement. This finds them
by ARITHMETIC, on every record, for nothing. The two are not redundant:

  this file                          the FM (Part 3)
  ----------------------------------  ---------------------------------------
  runs on 100% of records, always     runs on a batch, on demand
  deterministic and alarmable          varies run to run (measured: it does)
  catches only what a RULE can catch   catches what needs reading
  no prompt, no tokens, no variance    ~2 cents and 40s per run

Its output is also the CROSS-CHECK on the FM: when the cheap tier and the model
disagree about a record, that disagreement is itself the signal worth a human.

Scored against the same 5 planted mismatches the model was scored on, so "code
vs Haiku on the same task" is a measured comparison rather than an opinion.

    python3 processing/quality_review.py --dry-run     # no AWS writes
    python3 processing/quality_review.py               # writes S3 + metric
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feedback_document as fd  # noqa: E402

BUCKET = os.environ.get("DATA_BUCKET", "customer-feedback-analysis")
REGION = os.environ.get("AWS_REGION", "us-east-1")
REVIEW_PREFIX = "quality-review"
CW_NAMESPACE = "CustomerFeedback/Quality"

# A rating is 1..5. These are the bands a sentiment label implies.
POSITIVE_BAND = (4, 5)
NEGATIVE_BAND = (1, 2)

# Thresholds are BUSINESS RULES, stated here rather than buried in an if:
#   - 2 rating points is "a different opinion", not rounding.
#   - A survey label disagreeing with its own ratings by >= 2 points on the
#     1-5 scale is the same idea applied to self-reported data.
# Both were chosen as "two points on a five point scale", NOT tuned until the
# planted records passed -- tuning a threshold against the answers is how you
# get a detector that only works on your test set.
MIN_RATING_DIVERGENCE = 2
MIN_SURVEY_GAP = 2.0


def rating_sentiment_mismatch(sentiment, rating):
    """(is_mismatch, divergence). NEUTRAL/MIXED implies no band, so no verdict:
    a rule that cannot decide must say so rather than guess."""
    if not sentiment or rating is None:
        return False, 0
    label = str(sentiment).upper()
    if label == "POSITIVE" and rating <= NEGATIVE_BAND[1]:
        return True, POSITIVE_BAND[0] - rating
    if label == "NEGATIVE" and rating >= POSITIVE_BAND[0]:
        return True, rating - NEGATIVE_BAND[1]
    return False, 0


def survey_label_mismatch(gap):
    """Survey self-report: the stated satisfaction label vs the numbers the same
    person gave. gap = satisfaction_ordinal - mean(ratings)."""
    if gap is None:
        return False, 0
    return abs(gap) >= MIN_SURVEY_GAP, abs(gap)


def assess(doc: dict) -> dict:
    """One verdict per document. Every flag carries its EVIDENCE, so a human in
    the review queue does not have to re-derive why it was flagged."""
    signals = doc.get("signals", {})
    sentiment, rating = signals.get("sentiment"), signals.get("rating")
    gap = signals.get("rating_satisfaction_gap")

    flags = []
    mismatch, divergence = rating_sentiment_mismatch(sentiment, rating)
    if mismatch and divergence >= MIN_RATING_DIVERGENCE:
        flags.append({
            "rule": "sentiment_vs_rating",
            "detail": f"text scored {sentiment} but the customer rated it {rating}/5",
            "divergence": divergence,
        })
    survey_mismatch, size = survey_label_mismatch(gap)
    if survey_mismatch:
        flags.append({
            "rule": "survey_label_vs_ratings",
            "detail": f"stated satisfaction differs from own ratings by {size} points",
            "divergence": size,
        })

    # ESCALATION. The cheap tier decides what it can and names what it cannot.
    if not doc["provenance"]["admissible"]:
        route = "rejected"
    elif flags:
        route = "review_queue"
    elif sentiment is None and doc.get("text"):
        # No deterministic signal exists for this record (images have no
        # sentiment, calls have no rating): only reading it can judge it.
        route = "escalate_to_fm"
    else:
        route = "auto_accept"

    return {
        "doc_id": doc["doc_id"],
        "source_type": doc["source_type"],
        "product_id": doc.get("product_id"),
        "route": route,
        "flags": flags,
        "sentiment": sentiment,
        "rating": rating,
        "rating_satisfaction_gap": gap,
        "text_excerpt": (doc.get("text") or "")[:160],
    }


def summarise(verdicts: list) -> dict:
    routes = {}
    for v in verdicts:
        routes[v["route"]] = routes.get(v["route"], 0) + 1
    judged = [v for v in verdicts if v["route"] in ("review_queue", "auto_accept")]
    flagged = [v for v in verdicts if v["route"] == "review_queue"]
    return {
        "documents": len(verdicts),
        "by_route": routes,
        # RATE over what this tier could actually judge -- including records it
        # could not judge would flatter the number.
        "mismatch_rate": round(len(flagged) / len(judged), 4) if judged else None,
        "judged": len(judged),
        "flagged": len(flagged),
        "rules": {"min_rating_divergence": MIN_RATING_DIVERGENCE,
                  "min_survey_gap": MIN_SURVEY_GAP},
    }


def score_against_truth(verdicts: list, truth: dict) -> dict:
    """Same arithmetic the FM was scored with, on the same planted records."""
    planted = set(truth["planted_rating_mismatches"])
    flagged = {v["doc_id"] for v in verdicts if v["route"] == "review_queue"}
    tp, fn, fp = planted & flagged, planted - flagged, flagged - planted
    precision = len(tp) / len(flagged) if flagged else None
    recall = len(tp) / len(planted) if planted else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else 0.0)
    return {"planted": sorted(planted), "flagged": sorted(flagged),
            "true_positives": sorted(tp), "missed": sorted(fn),
            "false_positives": sorted(fp),
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "f1": round(f1, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    args = ap.parse_args()

    import boto3
    s3 = boto3.client("s3", region_name=REGION)

    # Reuse Part 3's collector: same documents, but WITH the signals the prompt
    # withheld -- this tier is exactly the consumer those labels were kept for.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_insight_report import collect_documents
    docs = collect_documents(s3)
    verdicts = [assess(d) for d in docs]
    summary = summarise(verdicts)

    truth_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "ground_truth.json")
    scored = score_against_truth(verdicts, json.load(open(truth_path)))

    print(json.dumps(summary, indent=2))
    print("\nscored against the planted mismatches:")
    print(f"  precision {scored['precision']}  recall {scored['recall']}  f1 {scored['f1']}")
    for k in ("true_positives", "missed", "false_positives"):
        print(f"  {k}: {scored[k] or 'none'}")

    if args.dry_run:
        print("\n--- DRY RUN: nothing written ---")
        return

    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "summary": summary, "scored_against_ground_truth": scored,
           "queue": [v for v in verdicts if v["route"] == "review_queue"],
           "escalated": [v for v in verdicts if v["route"] == "escalate_to_fm"],
           "all_verdicts": verdicts}
    key = f"{REVIEW_PREFIX}/quality_review.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(out, indent=2),
                  ContentType="application/json")

    cw = boto3.client("cloudwatch", region_name=REGION)
    cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=[
        {"MetricName": "MismatchRate", "Value": summary["mismatch_rate"] or 0.0,
         "Unit": "None"},
        {"MetricName": "RecordsFlagged", "Value": summary["flagged"], "Unit": "Count"},
        {"MetricName": "RecordsEscalatedToFM",
         "Value": summary["by_route"].get("escalate_to_fm", 0), "Unit": "Count"},
    ])
    print(f"\nwrote s3://{BUCKET}/{key} and 3 metrics to {CW_NAMESPACE}")


if __name__ == "__main__":
    main()
