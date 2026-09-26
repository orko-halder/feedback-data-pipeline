#!/bin/bash
# IAM role assumed by the Glue crawler and Data Quality evaluation runs.
source "$(dirname "$0")/common.sh"

echo "=== Creating role: $GLUE_ROLE_NAME ==="
aws iam create-role \
    --role-name "$GLUE_ROLE_NAME" \
    --assume-role-policy-document file://infra-template/policies/glue_trust_policy.json

echo "=== Attaching AWS-managed Glue service policy ==="
# Managed policy: AWS-maintained, reusable. Covers crawler execution and
# Data Catalog writes, but grants NO S3 access -- that is the next step.
aws iam attach-role-policy \
    --role-name "$GLUE_ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole

echo "=== Attaching bucket-scoped S3 read policy (inline) ==="
# Inline policy: custom to this role, deleted with it. Two ARNs because
# s3:ListBucket acts on the BUCKET and s3:GetObject on the OBJECTS inside
# it -- different resource types; the wrong one fails with AccessDenied
# even though the policy looks complete.
POLICY_FILE="$(render_template infra-template/policies/glue_s3_policy.json.template)"
aws iam put-role-policy \
    --role-name "$GLUE_ROLE_NAME" \
    --policy-name feedback-pipeline-s3-read \
    --policy-document "file://${POLICY_FILE}"

echo "Done. IAM propagation takes a few seconds."
