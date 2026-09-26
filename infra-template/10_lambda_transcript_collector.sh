#!/bin/bash
# Transcript COLLECTOR: EventBridge (Transcribe job COMPLETED/FAILED)
#   -> GetTranscriptionJob -> read transcript -> Comprehend -> processed-data/calls/
# Pairs with 09 (the starter). The starter fires on S3 upload and exits;
# this one is woken by the job's terminal state, because Transcribe is async.
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $ROLE_TRANSCRIPT_COLLECTOR ==="
aws iam create-role \
    --role-name "$ROLE_TRANSCRIPT_COLLECTOR" \
    --assume-role-policy-document file://infra-template/policies/lambda_trust_policy.json

aws iam attach-role-policy \
    --role-name "$ROLE_TRANSCRIPT_COLLECTOR" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "=== Attaching Transcribe + S3 + Comprehend + CloudWatch policy ==="
# PutMetricData cannot be scoped by resource -- the namespace condition key
# is the only way to stop this role writing to any metric namespace.
POLICY_FILE="$(render_template infra-template/policies/transcript_collector_policy.json.template)"
aws iam put-role-policy \
    --role-name "$ROLE_TRANSCRIPT_COLLECTOR" \
    --policy-name feedback-pipeline-transcript-collector \
    --policy-document "file://${POLICY_FILE}"

echo "=== Waiting for IAM propagation (eventual consistency) ==="
sleep 12

echo "=== Packaging and deploying ==="
(cd lambda && rm -f call_transcript_collector.zip && zip -jq call_transcript_collector.zip call_transcript_collector.py)

# 120s: one GetTranscriptionJob + one S3 read + one Comprehend batch per call.
aws lambda create-function \
    --function-name "$FN_TRANSCRIPT_COLLECTOR" \
    --runtime python3.12 \
    --role "$(role_arn "$ROLE_TRANSCRIPT_COLLECTOR")" \
    --handler call_transcript_collector.lambda_handler \
    --zip-file fileb://lambda/call_transcript_collector.zip \
    --timeout 120 --memory-size 256

echo "=== EventBridge rule: Transcribe job COMPLETED or FAILED ==="
# Pattern values were read off the REAL captured event, not the docs:
# source aws.transcribe (not aws.glue-dataquality's naming), and the status
# lives at detail.TranscriptionJobStatus -- each service names it differently.
aws events put-rule \
    --name "$EB_RULE_TRANSCRIBE" \
    --event-pattern file://infra-template/policies/transcribe_job_state_event_pattern.json \
    --description "Transcribe job reached a terminal state -> collect the transcript"

echo "=== Granting EventBridge permission to invoke the Lambda ==="
# Resource-based policy. SourceArn scopes it to THIS rule: without it any
# rule in the account could invoke this function (confused deputy).
aws lambda add-permission \
    --function-name "$FN_TRANSCRIPT_COLLECTOR" \
    --statement-id eventbridge-invoke-permission \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn "arn:aws:events:${AWS_REGION}:${AWS_ACCOUNT_ID}:rule/${EB_RULE_TRANSCRIBE}"

echo "=== Pointing the rule at the Lambda ==="
# A rule with no target matches events and does nothing at all.
aws events put-targets \
    --rule "$EB_RULE_TRANSCRIBE" \
    --targets "Id=1,Arn=$(fn_arn "$FN_TRANSCRIPT_COLLECTOR")"

echo "Done. Upload an audio file to ${PREFIX_RAW}/calls/ to exercise 09 -> Transcribe -> 10."
