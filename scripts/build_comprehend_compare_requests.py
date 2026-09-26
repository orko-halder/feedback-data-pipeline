#!/usr/bin/env python3
"""Build the two Comprehend request files used to compare sentiment designs on one call.

Design A (per turn):    one document per speaker turn            -> TextList[i] = turn i
Design B (per speaker): [customer_all, agent_all, customer_last] -> fixed role slots

BatchDetectSentiment has no speaker field: speaker identity is carried by array
POSITION only and re-attached on our side by Index (see apply_batch_results).

Roles come from infer_roles (first speaker = agent), the same labelled guess the
Lambda uses -- so this comparison inherits that assumption.

Usage (from project root):
    python3 scripts/build_comprehend_compare_requests.py [transcript.json]
Then run the two `aws comprehend batch-detect-sentiment --cli-input-json file://...`
commands and tee the responses next to the requests.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lambda"))
from call_transcript_collector import infer_roles, merge_turns  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs", "api-responses")
DEFAULT_TRANSCRIPT = os.path.join(OUT_DIR, "transcribe_transcript_output.json")


def build_requests(turns: list, roles: dict) -> tuple:
    by_role = lambda r: [t["text"] for t in turns if roles[t["speaker"]]["role_guess"] == r]
    customer, agent = by_role("customer"), by_role("agent")
    a = {"TextList": [t["text"] for t in turns], "LanguageCode": "en"}
    b = {"TextList": [" ".join(customer), " ".join(agent), customer[-1]], "LanguageCode": "en"}
    return a, b


def main(path: str) -> None:
    transcript = json.load(open(path))
    turns = merge_turns(transcript["results"].get("audio_segments", []))
    a, b = build_requests(turns, infer_roles(turns))
    for name, req in (("a", a), ("b", b)):
        out = os.path.join(OUT_DIR, f"comprehend_compare_design_{name}_request.json")
        with open(out, "w") as f:
            json.dump(req, f, indent=2)
        billed = sum(max(300, len(s)) for s in req["TextList"])
        print(f"design {name.upper()}: {len(req['TextList'])} docs, ~{billed} chars billed -> {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TRANSCRIPT)
