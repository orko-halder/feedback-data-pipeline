#!/bin/bash
# Wires every S3 event trigger. Run LAST -- every Lambda referenced must
# already exist or S3 rejects the whole document.
source "$(dirname "$0")/common.sh"

# WHY ONE SCRIPT AND NOT ONE PER LAMBDA
# put-bucket-notification-configuration REPLACES the bucket's entire
# notification document. It does not append. Pushing a config containing
# only the new trigger silently deletes every existing one -- no error, no
# warning, and the pipeline quietly stops working. So all triggers live in
# one template and get written together, every time.
#
# The chain this creates:
#   raw-data/reviews/*.json              -> text validator
#   validation-results/*_validation.json -> comprehend processor
# The second Lambda is triggered by the FIRST Lambda's output. Neither
# knows the other exists; S3 is the connective tissue. That is event
# choreography -- no orchestrator, each stage reacts to what landed.
#
# Note "aws s3api", not "aws s3". The s3 commands are simplified
# file-transfer verbs (cp, ls, mb); bucket CONFIGURATION lives in s3api.

echo "=== Current config, before overwrite ==="
aws s3api get-bucket-notification-configuration --bucket "$DATA_BUCKET" \
    --query 'LambdaFunctionConfigurations[].Id' --output text || echo "(none)"

echo "=== Writing combined notification config ==="
CONFIG_FILE="$(render_template infra-template/policies/s3_notification_config.json.template)"
aws s3api put-bucket-notification-configuration \
    --bucket "$DATA_BUCKET" \
    --notification-configuration "file://${CONFIG_FILE}"

echo "=== Verifying ==="
aws s3api get-bucket-notification-configuration --bucket "$DATA_BUCKET" \
    --query 'LambdaFunctionConfigurations[].Id' --output text
