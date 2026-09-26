#!/bin/bash
# Creates the data lake bucket and uploads the synthetic source data.
# Run from the project root:  bash infra-template/00_s3.sh
source "$(dirname "$0")/common.sh"

echo "=== Creating bucket: $DATA_BUCKET ==="
# us-east-1 is the one region where --create-bucket-configuration must be
# OMITTED; passing it there is an error. Every other region requires it.
if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3 mb "s3://${DATA_BUCKET}" --region "$AWS_REGION"
else
    aws s3api create-bucket --bucket "$DATA_BUCKET" --region "$AWS_REGION" \
        --create-bucket-configuration "LocationConstraint=${AWS_REGION}"
fi

echo "=== Uploading synthetic data ==="
# Generate first if data/raw/ is empty:
#   python3 data/generate_feedback_dataset.py
aws s3 cp data/raw/ "s3://${DATA_BUCKET}/${PREFIX_RAW}/" --recursive
aws s3 ls "s3://${DATA_BUCKET}/${PREFIX_RAW}/" --recursive --summarize | tail -3
