"""
Publishes Glue Data Quality evaluation scores to CloudWatch, automatically.

NOTE ON REDUNDANCY -- read before assuming this file is necessary.
With CloudWatchMetricsEnabled=true on the evaluation run, Glue ALREADY
publishes glue.data.quality.rules.passed / .failed to a namespace called
"Glue Data Quality", from which the score is derivable (5 passed /
2 failed = 0.714). An earlier version of this comment claimed Glue
publishes nothing; that was wrong.

What this function still adds: a normalised pass RATE rather than raw
counts, published into the same CustomerFeedback/* namespace as the text
pipeline so both report consistently. And the EventBridge wiring it sits
behind does things metrics cannot -- SNS alerts, triggering remediation,
kicking off a downstream job on completion. For pure dashboarding,
Glue's built-in metrics would have been enough.

Triggered by EventBridge on "Data Quality Evaluation Results
Available" events (source: aws.glue-dataquality). No filtering on
detail.state at the rule level, deliberately -- see below.

Real event shape, confirmed by capturing an actual live event (NOT
just trusting the AWS docs sample, which differs in three small ways:
lowercase "resultId" not "resultID", and "numRulesSucceeded" /
"numRulesFailed" not "rulesSucceeded" / "rulesFailed"):

{
  "detail-type": "Data Quality Evaluation Results Available",
  "source": "aws.glue-dataquality",
  "detail": {
    "resultId": "dqresult-...",
    "context": {"runId": "...", "databaseName": "...", "tableName": "..."},
    "rulesetNames": ["ruleset1"],
    "state": "FAILED",
    "score": 0.71,
    "numRulesSucceeded": 5, "numRulesFailed": 2, "numRulesSkipped": 0
  }
}

IMPORTANT: detail.state here means "did the DATA pass the quality
rules" (SUCCEEDED/FAILED as a verdict on the data), NOT "did the Glue
job run without error." A ruleset with intentionally-planted bad rows
will report state=FAILED forever, with a perfectly valid score
alongside it. Gating on state=SUCCEEDED would silently drop every
evaluation that found real problems -- exactly backwards for a
monitoring pipeline, where a failing score is the interesting data
point. So the actual gate is just "is there a score to publish."
"""

import json

import boto3

CW_NAMESPACE_DATA_QUALITY = "CustomerFeedback/DataQuality"


def should_process(detail: dict) -> bool:
    """Pure decision logic. Gates on score presence, not on
    detail.state -- see module docstring for why state is the wrong
    field to gate on here."""
    return detail.get("score") is not None


def extract_ruleset_names(detail: dict) -> list:
    """Pure extraction. rulesetNames is a list because one evaluation
    run can cover multiple rulesets against the same data source."""
    return detail.get("rulesetNames", [])


def build_metric_data(score: float, ruleset_name: str) -> dict:
    """Pure construction of the CloudWatch MetricData shape."""
    return {
        "MetricName": "RulesetPassRate",
        "Value": score,
        "Unit": "None",
        "Dimensions": [{"Name": "Ruleset", "Value": ruleset_name}],
    }


def lambda_handler(event, context):
    cloudwatch = boto3.client("cloudwatch")

    detail = event.get("detail", {})

    if not should_process(detail):
        return {"statusCode": 200, "body": json.dumps("No score present in event, skipping")}

    score = detail["score"]
    ruleset_names = extract_ruleset_names(detail)
    published = []

    for ruleset_name in ruleset_names:
        metric = build_metric_data(score, ruleset_name)
        cloudwatch.put_metric_data(
            Namespace=CW_NAMESPACE_DATA_QUALITY,
            MetricData=[metric],
        )
        published.append({"ruleset": ruleset_name, "score": score})

    return {"statusCode": 200, "body": json.dumps({"published": published})}
