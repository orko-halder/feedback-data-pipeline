#!/bin/bash
# Removes everything eventbridge_capture.sh created, in dependency order.
#   bash scripts/eventbridge_capture_remove.sh <short-name>
set -uo pipefail
NAME="${1:?usage: $0 <short-name>}"

# Targets must be detached before a rule can be deleted.
aws events remove-targets --rule "debug-${NAME}-catchall" --ids debug-log > /dev/null 2>&1 \
  && echo "targets removed" || echo "targets: none / already gone"
aws events delete-rule --name "debug-${NAME}-catchall" 2>/dev/null \
  && echo "rule deleted" || echo "rule: none / already gone"
aws logs delete-log-group --log-group-name "/aws/events/${NAME}-debug" 2>/dev/null \
  && echo "log group deleted" || echo "log group: none / already gone"
aws logs delete-resource-policy --policy-name "eventbridge-to-${NAME}-debug" 2>/dev/null \
  && echo "resource policy deleted" || echo "resource policy: none / already gone"
