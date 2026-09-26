#!/bin/bash
# Surveys -> SageMaker Processing -> processed-data/surveys/
#
# Processing COPIES data in and out; nothing streams:
#   s3://.../raw-data/surveys/     -> /opt/ml/processing/input/data
#   s3://.../processing-code/      -> /opt/ml/processing/input/code
#   /opt/ml/processing/output      -> s3://.../processed-data/surveys/
#
# Batch by design: the job reads the WHOLE file. An S3 trigger would re-run
# the entire file per row, so this is launched on demand, not by an event.
source "$(dirname "$0")/common.sh"

JOB_NAME="survey-processing-$(date -u +%Y%m%dT%H%M%S)"   # must be unique per account

echo "=== Creating role: $ROLE_SAGEMAKER ==="
# Principal is sagemaker.amazonaws.com: the SERVICE assumes this role and runs
# the container with it. Your own credentials are not used inside the job.
aws iam create-role \
    --role-name "$ROLE_SAGEMAKER" \
    --assume-role-policy-document file://infra-template/policies/sagemaker_trust_policy.json || true

POLICY_FILE="$(render_template infra-template/policies/sagemaker_processing_policy.json.template)"
aws iam put-role-policy \
    --role-name "$ROLE_SAGEMAKER" \
    --policy-name feedback-pipeline-sagemaker-processing \
    --policy-document "file://${POLICY_FILE}"

echo "=== Waiting for IAM propagation ==="
sleep 12

echo "=== Uploading the processing script ==="
# The script is INPUT DATA to the job, not a deployment artifact -- Processing
# has no concept of a code package. It is copied in like any other input.
aws s3 cp processing/survey_processor.py "s3://${DATA_BUCKET}/${PREFIX_CODE}/survey_processor.py"

echo "=== Starting processing job: $JOB_NAME ==="
aws sagemaker create-processing-job \
    --processing-job-name "$JOB_NAME" \
    --role-arn "$(role_arn "$ROLE_SAGEMAKER")" \
    --app-specification "{
        \"ImageUri\": \"${SM_IMAGE_URI}\",
        \"ContainerEntrypoint\": [\"python3\", \"/opt/ml/processing/input/code/survey_processor.py\"]
    }" \
    --processing-resources "{
        \"ClusterConfig\": {\"InstanceCount\": 1, \"InstanceType\": \"${SM_INSTANCE_TYPE}\", \"VolumeSizeInGB\": 5}
    }" \
    --stopping-condition '{"MaxRuntimeInSeconds": 900}' \
    --processing-inputs "[
        {\"InputName\": \"data\", \"S3Input\": {
            \"S3Uri\": \"s3://${DATA_BUCKET}/${PREFIX_RAW}/surveys/\",
            \"LocalPath\": \"/opt/ml/processing/input/data\",
            \"S3DataType\": \"S3Prefix\", \"S3InputMode\": \"File\",
            \"S3DataDistributionType\": \"FullyReplicated\"}},
        {\"InputName\": \"code\", \"S3Input\": {
            \"S3Uri\": \"s3://${DATA_BUCKET}/${PREFIX_CODE}/\",
            \"LocalPath\": \"/opt/ml/processing/input/code\",
            \"S3DataType\": \"S3Prefix\", \"S3InputMode\": \"File\",
            \"S3DataDistributionType\": \"FullyReplicated\"}}
    ]" \
    --processing-output-config "{
        \"Outputs\": [{\"OutputName\": \"processed\", \"S3Output\": {
            \"S3Uri\": \"s3://${DATA_BUCKET}/${PREFIX_PROCESSED}/surveys/\",
            \"LocalPath\": \"/opt/ml/processing/output\",
            \"S3UploadMode\": \"EndOfJob\"}}]
    }"

echo
echo "Job started. Watch it:"
echo "  aws sagemaker describe-processing-job --processing-job-name $JOB_NAME --query '[ProcessingJobStatus,FailureReason]' --output json"
echo "Container logs:"
echo "  aws logs tail /aws/sagemaker/ProcessingJobs --log-stream-name-prefix $JOB_NAME --follow"
