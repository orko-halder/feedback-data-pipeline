#!/usr/bin/env python3
"""Scores a Bedrock insight report against data/ground_truth.json.

WHY THIS EXISTS
A fluent report is not a correct report. The dataset was regenerated with the
answers written down first -- three products with a planted issue, two clean
controls, five planted rating mismatches -- so the model's output can be
MEASURED rather than admired. Without this file, "the themes look plausible"
is the entire quality process.

    python3 scripts/score_insight_report.py /tmp/insight_report.json

Scoring is deliberately blunt and states its own limits: keyword matching on
theme text, one dataset, one run. It catches a report that missed a planted
issue or invented one about a control -- the two failures that matter -- not
subtle differences in wording quality.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRUTH = os.path.join(ROOT, "data", "ground_truth.json")

# Words that indicate the model found THIS issue, not merely this product.
ISSUE_KEYWORDS = {
    "EAR-2200": ("charg", "battery", "power"),
    "VAC-3000": ("stuck", "sensor", "firmware", "furniture", "shuts"),
    "BLD-4500": ("burn", "overheat", "hot", "loud", "nois", "smell"),
}


def theme_text(t: dict) -> str:
    return " ".join(str(t.get(k, "")) for k in ("theme", "quote")).lower()


def main(path: str) -> int:
    truth = json.load(open(TRUTH))
    doc = json.load(open(path))
    report = doc.get("report", doc)
    themes = report.get("themes", [])
    expected = {t["product_id"]: t for t in truth["expected_themes"]}
    controls = set(truth["controls"])

    print(f"model: {doc.get('model_id', '?')}   themes: {len(themes)}")
    usage = doc.get("usage", {})
    if usage:
        print(f"tokens: in={usage.get('input_tokens')} out={usage.get('output_tokens')}")
    print()

    # --- RECALL: was each planted issue found, and with cross-channel evidence?
    print("RECALL -- planted issues (should all be found)")
    found = 0
    for pid, exp in expected.items():
        hits = [t for t in themes
                if (t.get("product_id") == pid
                    or any(k in theme_text(t) for k in ISSUE_KEYWORDS.get(pid, ())))
                and any(k in theme_text(t) for k in ISSUE_KEYWORDS.get(pid, ()))]
        if hits:
            found += 1
            best = max(hits, key=lambda t: len(t.get("doc_ids", [])))
            channels = sorted(set(best.get("channels", [])))
            print(f"  [FOUND]  {pid} ({exp['issue'][:44]})")
            print(f"           theme: {best.get('theme', '')[:70]}")
            print(f"           severity={best.get('severity')} channels={channels} "
                  f"docs={len(best.get('doc_ids', []))} "
                  f"strength={best.get('evidence_strength')} link={best.get('product_link_basis')}")
        else:
            print(f"  [MISSED] {pid} ({exp['issue']})")
    print(f"  recall: {found}/{len(expected)}")
    print()

    # --- PRECISION: a control product must not acquire a serious problem theme.
    # A positive or low-severity theme about a control is legitimate; a high or
    # medium severity PROBLEM is an invented issue.
    print("PRECISION -- control products (no serious issue should be claimed)")
    invented = [t for t in themes
                if t.get("product_id") in controls
                and t.get("severity") in ("high", "medium")]
    if invented:
        for t in invented:
            print(f"  [INVENTED] {t.get('product_id')} severity={t.get('severity')}: "
                  f"{t.get('theme', '')[:70]}")
    else:
        print(f"  clean: no high/medium issue claimed for {sorted(controls)}")
    print()

    # --- CONTRADICTIONS: the five planted rating mismatches.
    print("CONTRADICTIONS -- planted rating/sentiment mismatches")
    planted = set(truth["planted_rating_mismatches"])
    cited = {i for c in report.get("contradictions", []) for i in c.get("doc_ids", [])}
    hit, miss, extra = planted & cited, planted - cited, cited - planted
    print(f"  planted: {len(planted)}  found: {len(hit)}  missed: {len(miss)}  other: {len(extra)}")
    for i in sorted(hit):
        print(f"    [HIT]   {i}")
    for i in sorted(miss):
        print(f"    [MISS]  {i}")
    for i in sorted(extra):
        print(f"    [OTHER] {i}  (not planted -- may still be a real observation)")
    print()

    # --- CITATION HYGIENE
    print("CITATIONS")
    problems = doc.get("validation_problems", [])
    hallucinated = [p for p in problems if "NOT in the batch" in p]
    print(f"  structural problems: {problems or 'none'}")
    uncited = [i for i, t in enumerate(themes) if not t.get("doc_ids")]
    print(f"  themes with no doc_ids: {uncited or 'none'}")
    print(f"  hallucinated doc_ids: {'YES -- report untrustworthy' if hallucinated else 'none'}")
    bases = {}
    for t in themes:
        b = t.get("product_link_basis", "unset")
        bases[b] = bases.get(b, 0) + 1
    print(f"  product_link_basis: {bases}")
    print()

    verdict_ok = (found == len(expected) and not invented and not hallucinated)
    print("VERDICT:", "PASS" if verdict_ok else "REVIEW NEEDED")
    print("Limits: keyword matching, one dataset, one run at temperature 0. "
          "This catches missed or invented ISSUES, not wording quality.")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/insight_report.json"))
