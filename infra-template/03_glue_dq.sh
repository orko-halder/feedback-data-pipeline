#!/bin/bash
# Glue Data Quality ruleset + an evaluation run against the surveys table.
source "$(dirname "$0")/common.sh"

echo "=== Creating DQ ruleset: $GLUE_DQ_RULESET_NAME ==="
# Rules are DQDL, in dqdl/. Note IsComplete is paired with ColumnLength>0
# on the same columns: IsComplete only catches SQL NULL, and a CSV's empty
# cell arrives as an empty STRING, which counts as present. IsComplete
# alone scored this data 1.00 while two fields were visibly blank.
if aws glue get-data-quality-ruleset --name "$GLUE_DQ_RULESET_NAME" >/dev/null 2>&1; then
    # A ruleset must be UPDATED when the table schema changes -- surveys
    # gained product_id -- and create-... fails with AlreadyExistsException
    # on any re-run. Validating a STALE ruleset against a new schema still
    # reports a score; it is just scoring the wrong columns.
    aws glue update-data-quality-ruleset \
      --name "$GLUE_DQ_RULESET_NAME" \
      --ruleset file://infra-template/dqdl/surveys_ruleset.dqdl
else
    aws glue create-data-quality-ruleset \
        --name "$GLUE_DQ_RULESET_NAME" \
        --description "Completeness and validity checks for ${GLUE_DATABASE}.surveys" \
        --ruleset file://infra-template/dqdl/surveys_ruleset.dqdl \
        --target-table "{\"TableName\": \"surveys\", \"DatabaseName\": \"${GLUE_DATABASE}\"}"
fi

echo "=== Starting evaluation run ==="
# CloudWatchMetricsEnabled defaults to FALSE. With it off, Glue publishes
# NOTHING to EventBridge, the downstream publisher Lambda never fires, and
# no error anywhere explains why. This flag is load-bearing.
RUN_ID="$(aws glue start-data-quality-ruleset-evaluation-run \
    --data-source "{\"GlueTable\": {\"DatabaseName\": \"${GLUE_DATABASE}\", \"TableName\": \"surveys\"}}" \
    --role "$GLUE_ROLE_NAME" \
    --ruleset-names "$GLUE_DQ_RULESET_NAME" \
    --additional-run-options '{"CloudWatchMetricsEnabled": true}' \
    --query RunId --output text)"

echo "Run: $RUN_ID  (spins up a Spark cluster, usually ~90s)"
while true; do
    STATUS="$(aws glue get-data-quality-ruleset-evaluation-run --run-id "$RUN_ID" --query 'Status' --output text)"
    echo "  status: $STATUS"
    case "$STATUS" in SUCCEEDED|FAILED|STOPPED) break ;; esac
    sleep 15
done

RESULT_ID="$(aws glue get-data-quality-ruleset-evaluation-run --run-id "$RUN_ID" --query 'ResultIds[0]' --output text)"
aws glue get-data-quality-result --result-id "$RESULT_ID" \
    --query '{Score: Score, Rules: RuleResults[].{Rule: Description, Result: Result}}'
