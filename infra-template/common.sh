#!/bin/bash
# Shared configuration for every script in this directory.
# Source it, never run it directly.
#
# WHY THIS FILE EXISTS
# The same account ID appearing in fifteen places is how a project
# becomes un-runnable in anyone else's account -- and un-committable,
# since the ID ends up in git history. Here it is resolved live from
# whoever's credentials are in use, and never written to a file.

set -euo pipefail

# --- Identity, resolved at runtime ---
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export AWS_ACCOUNT_ID
export AWS_REGION="${AWS_REGION:-us-east-1}"

# --- Project identity ---
# Bucket names are globally unique across all of AWS, so the initials
# suffix is what stops this colliding with someone else's bucket.
# Bucket names are a GLOBAL namespace across all of AWS, so a cloned repo
# cannot share one. Set this to your own suffix before running anything:
#   export PROJECT_INITIALS=abc
export PROJECT_INITIALS="${PROJECT_INITIALS:?set PROJECT_INITIALS to a short unique suffix, e.g. your initials}"
export DATA_BUCKET="${DATA_BUCKET:-customer-feedback-analysis-${PROJECT_INITIALS}}"

# --- S3 prefixes: one per pipeline stage ---
# Prefix separation is not cosmetic. A Lambda writing output into the
# prefix that triggers it will trigger itself, forever.
export PREFIX_RAW="raw-data"
export PREFIX_VALIDATION="validation-results"
export PREFIX_PROCESSED="processed-data"

# --- Glue ---
export GLUE_DATABASE="customer_feedback_db"
export GLUE_CRAWLER_NAME="customer-feedback-crawler"
export GLUE_DQ_RULESET_NAME="surveys_ruleset"
export GLUE_ROLE_NAME="feedback-pipeline-glue-role"

# --- Lambda functions and their roles ---
export FN_TEXT_VALIDATOR="feedback-pipeline-text-validator"
export ROLE_TEXT_VALIDATOR="feedback-pipeline-lambda-role"
export FN_DQ_PUBLISHER="feedback-pipeline-dq-score-publisher"
export ROLE_DQ_PUBLISHER="feedback-pipeline-dq-publisher-role"
export FN_COMPREHEND="feedback-pipeline-comprehend-processor"
export ROLE_IMAGE="feedback-pipeline-image-role"
export FN_IMAGE="feedback-pipeline-image-processor"
export ROLE_TRANSCRIBE_STARTER="feedback-pipeline-transcribe-starter-role"
export FN_TRANSCRIBE_STARTER="feedback-pipeline-transcribe-starter"
export ROLE_COMPREHEND="feedback-pipeline-comprehend-role"
export ROLE_TRANSCRIPT_COLLECTOR="feedback-pipeline-transcript-collector-role"
export FN_TRANSCRIPT_COLLECTOR="feedback-pipeline-transcript-collector"

# --- EventBridge ---
export EB_RULE_DQ="feedback-pipeline-dq-success-rule"
export EB_RULE_TRANSCRIBE="feedback-pipeline-transcribe-job-rule"

# --- SageMaker Processing (surveys) ---
export ROLE_SAGEMAKER="feedback-pipeline-sagemaker-role"
export PREFIX_CODE="processing-code"
export SM_INSTANCE_TYPE="${SM_INSTANCE_TYPE:-ml.m5.large}"
# Built-in SKLearn image. Registry account is PER REGION -- 683313688378 is
# us-east-1; see docs.aws.amazon.com/sagemaker/latest/dg-ecr-paths/.
export SM_IMAGE_URI="${SM_IMAGE_URI:-683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-scikit-learn:1.2-1}"

# --- Rendered templates land here (gitignored) ---
export BUILD_DIR="infra-template/.build"
mkdir -p "$BUILD_DIR"

# Substitutes ${DATA_BUCKET}, ${AWS_REGION}, ${AWS_ACCOUNT_ID} into a
# .template file. sed rather than envsubst, which ships with gettext and
# is not guaranteed present.
render_template() {
    local src="$1"
    local dest="$BUILD_DIR/$(basename "${src%.template}")"
    sed -e "s|\${DATA_BUCKET}|${DATA_BUCKET}|g" \
        -e "s|\${AWS_REGION}|${AWS_REGION}|g" \
        -e "s|\${AWS_ACCOUNT_ID}|${AWS_ACCOUNT_ID}|g" \
        "$src" > "$dest"
    echo "$dest"
}

role_arn() { echo "arn:aws:iam::${AWS_ACCOUNT_ID}:role/$1"; }
fn_arn()   { echo "arn:aws:lambda:${AWS_REGION}:${AWS_ACCOUNT_ID}:function:$1"; }
