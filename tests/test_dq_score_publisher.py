"""
Unit tests for the pure functions in dq_score_publisher.py -- no AWS
calls, no mocking. Run: python3 tests/test_dq_score_publisher.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from dq_score_publisher import (  # noqa: E402
    build_metric_data,
    extract_ruleset_names,
    should_process,
)

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def test_should_process():
    # The real-world case that broke this: state=FAILED (data quality
    # verdict) but a perfectly valid score present -- must still process.
    check(
        "state=FAILED but score present -> True",
        should_process({"state": "FAILED", "score": 0.71}) is True,
    )
    check(
        "state=SUCCEEDED with score present -> True",
        should_process({"state": "SUCCEEDED", "score": 1.0}) is True,
    )
    check("score missing entirely -> False", should_process({"state": "SUCCEEDED"}) is False)
    check("score explicitly None -> False", should_process({"score": None}) is False)
    check("score is 0.0 (falsy but valid) -> True", should_process({"score": 0.0}) is True)


def test_extract_ruleset_names():
    check(
        "normal payload: extracts list",
        extract_ruleset_names({"rulesetNames": ["surveys_ruleset"]}) == ["surveys_ruleset"],
    )
    check(
        "missing rulesetNames key -> empty list", extract_ruleset_names({"state": "FAILED"}) == []
    )
    check("empty rulesetNames -> empty list", extract_ruleset_names({"rulesetNames": []}) == [])


def test_build_metric_data():
    metric = build_metric_data(0.71, "surveys_ruleset")
    check("metric name is RulesetPassRate", metric["MetricName"] == "RulesetPassRate")
    check("value matches input", metric["Value"] == 0.71)
    check("unit is None", metric["Unit"] == "None")
    check(
        "dimensions carry the ruleset name",
        metric["Dimensions"] == [{"Name": "Ruleset", "Value": "surveys_ruleset"}],
    )


if __name__ == "__main__":
    test_should_process()
    test_extract_ruleset_names()
    test_build_metric_data()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")
