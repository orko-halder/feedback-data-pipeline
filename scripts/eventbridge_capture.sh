#!/bin/bash
# Captures EVERY event an AWS service publishes to EventBridge into a
# CloudWatch log group, unfiltered, so you can see the real payload shape
# before writing code against it.
#
#   bash scripts/eventbridge_capture.sh <event-source> <short-name>
#   bash scripts/eventbridge_capture.sh aws.transcribe transcribe
#
# WHY THIS EXISTS: coding against the documented event shape broke the
# Glue Data Quality integration three ways (wrong source, wrong
# detail-type, and the docs sample's field names differed from the live
# payload). Capture the real event first; write the handler second.
#
# TEMPORARY BY DESIGN. Remove with eventbridge_capture_remove.sh when done.
# CloudWatch Logs allows only 10 resource policies per region, so leaving
# these behind eventually blocks creating new ones.
set -euo pipefail

SOURCE="${1:?usage: $0 <event-source e.g. aws.transcribe> <short-name>}"
NAME="${2:?usage: $0 <event-source> <short-name>}"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGION="${AWS_REGION:-us-east-1}"
LOG_GROUP="/aws/events/${NAME}-debug"
RULE="debug-${NAME}-catchall"
POLICY_NAME="eventbridge-to-${NAME}-debug"

aws logs create-log-group --log-group-name "$LOG_GROUP"
echo "log group:  $LOG_GROUP"

POLICY_FILE="$(mktemp)"
cat > "$POLICY_FILE" << JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "events.amazonaws.com"},
    "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
    "Resource": "arn:aws:logs:${REGION}:${ACCOUNT}:log-group:${LOG_GROUP}:*"
  }]
}
JSON
aws logs put-resource-policy --policy-name "$POLICY_NAME" \
  --policy-document "file://${POLICY_FILE}" > /dev/null
rm -f "$POLICY_FILE"
echo "policy:     $POLICY_NAME  (EventBridge may write to the log group)"

# Filter on source ONLY -- no detail-type, no state -- so every event the
# service emits is captured, including states you did not expect.
aws events put-rule --name "$RULE" \
  --event-pattern "{\"source\": [\"${SOURCE}\"]}" \
  --description "TEMP debug capture for ${SOURCE}" > /dev/null
aws events put-targets --rule "$RULE" \
  --targets "[{\"Id\":\"debug-log\",\"Arn\":\"arn:aws:logs:${REGION}:${ACCOUNT}:log-group:${LOG_GROUP}\"}]" > /dev/null
echo "rule:       $RULE  (source = ${SOURCE})"
echo
echo "Read captured events:"
echo "  aws logs filter-log-events --log-group-name $LOG_GROUP --query 'events[].message' --output text"
echo "Remove when done:"
echo "  bash scripts/eventbridge_capture_remove.sh $NAME"
