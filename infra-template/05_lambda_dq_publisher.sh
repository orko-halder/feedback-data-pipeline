#!/bin/bash
# Publishes a normalised Glue Data Quality pass RATE to CloudWatch.
# NOTE: with CloudWatchMetricsEnabled=true (set in 03_glue_dq.sh), Glue
# already publishes rules.passed / rules.failed counts to the "Glue Data
# Quality" namespace, so this is redundant for plain dashboarding. It
# earns its place for the normalised rate in our own namespace, and for
# the EventBridge pattern, which can also drive alerts and remediation.
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $ROLE_DQ_PUBLISHER ==="
aws iam create-role \
    --role-name "$ROLE_DQ_PUBLISHER" \
    --assume-role-policy-document file://infra-template/policies/lambda_trust_policy.json

aws iam attach-role-policy \
    --role-name "$ROLE_DQ_PUBLISHER" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "=== Attaching CloudWatch policy ==="
# Only PutMetricData is needed. An earlier version called
# glue:GetDataQualityResult to fetch the score -- unnecessary, because the
# EventBridge event already carries "score". Reading it off the event
# removed both an API call and an IAM permission.
aws iam put-role-policy \
    --role-name "$ROLE_DQ_PUBLISHER" \
    --policy-name feedback-pipeline-dq-cloudwatch \
    --policy-document file://infra-template/policies/dq_publisher_policy.json

echo "=== Packaging and deploying ==="
(cd lambda && rm -f dq_score_publisher.zip && zip -j dq_score_publisher.zip dq_score_publisher.py)

aws lambda create-function \
    --function-name "$FN_DQ_PUBLISHER" \
    --runtime python3.12 \
    --role "$(role_arn "$ROLE_DQ_PUBLISHER")" \
    --handler dq_score_publisher.lambda_handler \
    --zip-file fileb://lambda/dq_score_publisher.zip \
    --timeout 30 --memory-size 128

echo "=== Creating EventBridge rule: $EB_RULE_DQ ==="
# Three things about this pattern, each one cost a debugging cycle:
#  1. source is "aws.glue-dataquality", NOT "aws.glue". Different source.
#  2. detail-type is "Data Quality Evaluation Results Available".
#  3. There is deliberately NO filter on detail.state. In this event
#     "state" means "did the DATA pass the rules" -- a ruleset with any
#     failing rule emits state=FAILED alongside a perfectly valid score.
#     Filtering on SUCCEEDED silently drops every evaluation that found a
#     problem, which is the data a monitor exists to capture. (The
#     unrelated Status field on the RUN does mean "executed without
#     error" -- same word, different meaning.)
aws events put-rule \
    --name "$EB_RULE_DQ" \
    --event-pattern file://infra-template/policies/dq_success_event_pattern.json \
    --description "Fires whenever a Glue DQ evaluation produces a result (pass or fail)"

echo "=== Granting EventBridge permission to invoke the Lambda ==="
aws lambda add-permission \
    --function-name "$FN_DQ_PUBLISHER" \
    --statement-id eventbridge-invoke-permission \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${AWS_REGION}:${AWS_ACCOUNT_ID}:rule/${EB_RULE_DQ}"

echo "=== Attaching the Lambda as the rule's target ==="
# A rule with no target matches events and does nothing with them.
aws events put-targets \
    --rule "$EB_RULE_DQ" \
    --targets "[{\"Id\": \"dq-score-publisher-target\", \"Arn\": \"$(fn_arn "$FN_DQ_PUBLISHER")\"}]"
