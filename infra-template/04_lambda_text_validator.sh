#!/bin/bash
# Lambda validating unstructured review JSON -- the checks Glue Data
# Quality cannot express (min text length, rating range, date format).
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $ROLE_TEXT_VALIDATOR ==="
aws iam create-role \
    --role-name "$ROLE_TEXT_VALIDATOR" \
    --assume-role-policy-document file://infra-template/policies/lambda_trust_policy.json

echo "=== Attaching basic execution policy ==="
# Every Lambda needs this -- it is what lets the function write its own
# CloudWatch Logs.
aws iam attach-role-policy \
    --role-name "$ROLE_TEXT_VALIDATOR" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "=== Attaching S3 + CloudWatch policy ==="
# cloudwatch:PutMetricData has no resource-level permission support, so
# "*" there is the only valid value, not laziness.
POLICY_FILE="$(render_template infra-template/policies/lambda_s3_cloudwatch_policy.json.template)"
aws iam put-role-policy \
    --role-name "$ROLE_TEXT_VALIDATOR" \
    --policy-name feedback-pipeline-s3-cloudwatch \
    --policy-document "file://${POLICY_FILE}"

echo "=== Packaging and deploying ==="
(cd lambda && rm -f text_validator.zip && zip -j text_validator.zip text_validator.py)

aws lambda create-function \
    --function-name "$FN_TEXT_VALIDATOR" \
    --runtime python3.12 \
    --role "$(role_arn "$ROLE_TEXT_VALIDATOR")" \
    --handler text_validator.lambda_handler \
    --zip-file fileb://lambda/text_validator.zip \
    --timeout 30 --memory-size 128

echo "=== Granting S3 permission to invoke it ==="
# RESOURCE-BASED policy, attached to the function itself -- distinct from
# the execution role above. The role says what this function may call;
# this says who may call this function. S3 has no IAM role of its own, so
# this is the only way to grant it.
# SourceArn/SourceAccount prevent the confused-deputy problem: without
# them, any bucket in any AWS account could invoke this function.
aws lambda add-permission \
    --function-name "$FN_TEXT_VALIDATOR" \
    --statement-id s3-invoke-permission \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn "arn:aws:s3:::${DATA_BUCKET}" \
    --source-account "$AWS_ACCOUNT_ID"

echo "Done. The S3 trigger itself is set in 07_s3_notifications.sh."
