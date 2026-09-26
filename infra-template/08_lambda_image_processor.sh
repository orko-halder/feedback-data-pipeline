#!/bin/bash
# Textract-based extraction from product feedback images.
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $ROLE_IMAGE ==="
aws iam create-role \
    --role-name "$ROLE_IMAGE" \
    --assume-role-policy-document file://infra-template/policies/lambda_trust_policy.json

aws iam attach-role-policy \
    --role-name "$ROLE_IMAGE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

echo "=== Attaching S3 + Textract policy ==="
# Textract reads the image from S3 using the CALLER's permissions, so this
# role needs s3:GetObject even though the Lambda never downloads the file
# itself -- it passes an S3Object reference, not bytes.
POLICY_FILE="$(render_template infra-template/policies/image_processor_policy.json.template)"
aws iam put-role-policy \
    --role-name "$ROLE_IMAGE" \
    --policy-name feedback-pipeline-image-s3-textract \
    --policy-document "file://${POLICY_FILE}"

echo "=== Packaging and deploying ==="
(cd lambda && rm -f image_textract_processor.zip && zip -jq image_textract_processor.zip image_textract_processor.py)

aws lambda create-function \
    --function-name "$FN_IMAGE" \
    --runtime python3.12 \
    --role "$(role_arn "$ROLE_IMAGE")" \
    --handler image_textract_processor.lambda_handler \
    --zip-file fileb://lambda/image_textract_processor.zip \
    --timeout 60 --memory-size 256

echo "=== Granting S3 permission to invoke it ==="
aws lambda add-permission \
    --function-name "$FN_IMAGE" \
    --statement-id s3-invoke-permission \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn "arn:aws:s3:::${DATA_BUCKET}" \
    --source-account "$AWS_ACCOUNT_ID"

echo "Done. Trigger wiring is in 07_s3_notifications.sh (run it AFTER this)."
