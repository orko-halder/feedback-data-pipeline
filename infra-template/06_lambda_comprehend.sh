#!/bin/bash
# Enriches VALIDATED reviews with Comprehend entities/sentiment/key phrases.
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $ROLE_COMPREHEND ==="
aws iam create-role \
    --role-name "$ROLE_COMPREHEND" \
    --assume-role-policy-document file://infra-template/policies/lambda_trust_policy.json

aws iam attach-role-policy \
    --role-name "$ROLE_COMPREHEND" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "=== Attaching S3 + Comprehend policy ==="
# Reads from BOTH raw-data (original review text) and validation-results
# (the quality verdict); writes only to processed-data. The narrow write
# scope matters: it must not be able to write into a prefix that would
# re-trigger something, including itself.
POLICY_FILE="$(render_template infra-template/policies/comprehend_processor_policy.json.template)"
aws iam put-role-policy \
    --role-name "$ROLE_COMPREHEND" \
    --policy-name feedback-pipeline-comprehend-s3 \
    --policy-document "file://${POLICY_FILE}"

echo "=== Packaging and deploying ==="
(cd lambda && rm -f review_comprehend_processor.zip && zip -j review_comprehend_processor.zip review_comprehend_processor.py)

aws lambda create-function \
    --function-name "$FN_COMPREHEND" \
    --runtime python3.12 \
    --role "$(role_arn "$ROLE_COMPREHEND")" \
    --handler review_comprehend_processor.lambda_handler \
    --zip-file fileb://lambda/review_comprehend_processor.zip \
    --timeout 60 --memory-size 256

echo "=== Granting S3 permission to invoke it ==="
aws lambda add-permission \
    --function-name "$FN_COMPREHEND" \
    --statement-id s3-invoke-permission \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn "arn:aws:s3:::${DATA_BUCKET}" \
    --source-account "$AWS_ACCOUNT_ID"

echo "Done. Trigger wiring happens in 07_s3_notifications.sh."
