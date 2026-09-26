#!/bin/bash
# Glue database + crawler. Infers the survey CSV's schema and registers
# it as a table in the Data Catalog.
source "$(dirname "$0")/common.sh"

echo "=== Creating Glue database: $GLUE_DATABASE ==="
# A Glue "database" is only a namespace for table definitions -- the
# equivalent of a schema in Postgres. It stores no data itself.
aws glue create-database --database-input "{\"Name\": \"${GLUE_DATABASE}\"}"

echo "=== Creating crawler: $GLUE_CRAWLER_NAME ==="
# Scoped to raw-data/surveys/ ONLY. Glue Data Quality operates on catalog
# tables, so only the structured survey CSV belongs here; reviews, images
# and audio are handled by Lambdas. A crawler pointed at a mixed-content
# prefix produces a nonsense schema or a pile of stray tables.
#
# The table name is never specified -- Glue derives it from the last path
# segment, producing a table called "surveys".
aws glue create-crawler \
    --name "$GLUE_CRAWLER_NAME" \
    --role "$GLUE_ROLE_NAME" \
    --database-name "$GLUE_DATABASE" \
    --targets "{\"S3Targets\": [{\"Path\": \"s3://${DATA_BUCKET}/${PREFIX_RAW}/surveys/\"}]}"

echo "=== Starting crawler ==="
# create-crawler only defines it. start-crawler reads S3, infers the
# schema, and writes the table into the catalog.
aws glue start-crawler --name "$GLUE_CRAWLER_NAME"

echo "Waiting for crawl (~60-90s)..."
while true; do
    STATE="$(aws glue get-crawler --name "$GLUE_CRAWLER_NAME" --query 'Crawler.State' --output text)"
    echo "  state: $STATE"
    [ "$STATE" = "READY" ] && break
    sleep 10
done
aws glue get-tables --database-name "$GLUE_DATABASE" --query 'TableList[].Name' --output text
