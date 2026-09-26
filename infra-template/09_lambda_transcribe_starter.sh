#!/bin/bash
# Transcribe STARTER: S3 audio upload -> StartTranscriptionJob -> exit.
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $ROLE_TRANSCRIBE_STARTER ==="
aws iam create-role \
    --role-name "$ROLE_TRANSCRIBE_STARTER" \
    --assume-role-policy-document file://infra-template/policies/lambda_trust_policy.json

aws iam attach-role-policy \
    --role-name "$ROLE_TRANSCRIBE_STARTER" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "=== Attaching Transcribe + S3 policy ==="
# S3 permissions are for TRANSCRIBE's benefit -- it reads the audio and
# writes the transcript with the caller's identity.
POLICY_FILE="$(render_template infra-template/policies/transcribe_starter_policy.json.template)"
aws iam put-role-policy \
    --role-name "$ROLE_TRANSCRIBE_STARTER" \
    --policy-name feedback-pipeline-transcribe-starter \
    --policy-document "file://${POLICY_FILE}"

echo "=== Waiting for IAM propagation (eventual consistency) ==="
sleep 12

echo "=== Packaging and deploying ==="
(cd lambda && rm -f call_transcribe_starter.zip && zip -jq call_transcribe_starter.zip call_transcribe_starter.py)

aws lambda create-function \
    --function-name "$FN_TRANSCRIBE_STARTER" \
    --runtime python3.12 \
    --role "$(role_arn "$ROLE_TRANSCRIBE_STARTER")" \
    --handler call_transcribe_starter.lambda_handler \
    --zip-file fileb://lambda/call_transcribe_starter.zip \
    --timeout 30 --memory-size 128

echo "=== Granting S3 permission to invoke it ==="
aws lambda add-permission \
    --function-name "$FN_TRANSCRIBE_STARTER" \
    --statement-id s3-invoke-permission \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn "arn:aws:s3:::${DATA_BUCKET}" \
    --source-account "$AWS_ACCOUNT_ID"

echo "Done. Trigger wiring is in 07_s3_notifications.sh (run it AFTER this)."
