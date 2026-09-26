# Teardown — run only after the exam / when done with this project

Nothing here costs ongoing money while it just exists (see reasoning
in conversation) EXCEPT anything explicitly marked (cost). Run top to
bottom — order matters where one resource depends on another.

```bash
# Glue Data Quality ruleset
aws glue delete-data-quality-ruleset --name surveys_ruleset

# Glue crawler
aws glue delete-crawler --name customer-feedback-crawler

# Glue database (also removes the tables registered inside it, e.g. surveys)
aws glue delete-database --name customer_feedback_db

# IAM role — must detach/remove all policies before the role itself can be deleted
aws iam delete-role-policy --role-name feedback-pipeline-glue-role --policy-name feedback-pipeline-s3-read
aws iam detach-role-policy --role-name feedback-pipeline-glue-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole
aws iam delete-role --role-name feedback-pipeline-glue-role

# S3 bucket — must be emptied before it can be deleted
aws s3 rm s3://customer-feedback-analysis --recursive
aws s3 rb s3://customer-feedback-analysis
```

## Lambda: feedback-pipeline-text-validator

```bash
aws lambda delete-function --function-name feedback-pipeline-text-validator

# Its auto-created log group persists after the function is deleted — storage cost (cost)
aws logs delete-log-group --log-group-name /aws/lambda/feedback-pipeline-text-validator

aws iam delete-role-policy --role-name feedback-pipeline-lambda-role --policy-name feedback-pipeline-s3-cloudwatch
aws iam detach-role-policy --role-name feedback-pipeline-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name feedback-pipeline-lambda-role
```

## EventBridge rule + DQ score publisher Lambda

```bash
# Targets must be detached before a rule can be deleted
aws events remove-targets --rule feedback-pipeline-dq-success-rule --ids dq-score-publisher-target
aws events delete-rule --name feedback-pipeline-dq-success-rule

aws lambda delete-function --function-name feedback-pipeline-dq-score-publisher

# Log group persists after the function is deleted — storage cost (cost)
aws logs delete-log-group --log-group-name /aws/lambda/feedback-pipeline-dq-score-publisher

aws iam delete-role-policy --role-name feedback-pipeline-dq-publisher-role --policy-name feedback-pipeline-dq-cloudwatch
aws iam detach-role-policy --role-name feedback-pipeline-dq-publisher-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name feedback-pipeline-dq-publisher-role
```

## Part 2: Comprehend review processor

```bash
aws lambda delete-function --function-name feedback-pipeline-comprehend-processor
aws logs delete-log-group --log-group-name /aws/lambda/feedback-pipeline-comprehend-processor

aws iam delete-role-policy --role-name feedback-pipeline-comprehend-role --policy-name feedback-pipeline-comprehend-s3
aws iam detach-role-policy --role-name feedback-pipeline-comprehend-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name feedback-pipeline-comprehend-role
```

Comprehend itself is pay-per-call — nothing persistent to delete.

Note: the bucket notification config holds ALL S3 triggers in one
document. Removing one Lambda means re-pushing the config WITHOUT that
entry, not deleting the config wholesale (which would kill the others).

## Part 2: Textract image processor

```bash
aws lambda delete-function --function-name feedback-pipeline-image-processor
aws logs delete-log-group --log-group-name /aws/lambda/feedback-pipeline-image-processor

aws iam delete-role-policy --role-name feedback-pipeline-image-role --policy-name feedback-pipeline-image-s3-textract
aws iam detach-role-policy --role-name feedback-pipeline-image-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name feedback-pipeline-image-role
```

Textract is pay-per-call — nothing persistent to delete.

## Part 2: Transcribe starter Lambda

```bash
aws lambda delete-function --function-name feedback-pipeline-transcribe-starter
aws logs delete-log-group --log-group-name /aws/lambda/feedback-pipeline-transcribe-starter

aws iam delete-role-policy --role-name feedback-pipeline-transcribe-starter-role --policy-name feedback-pipeline-transcribe-starter
aws iam detach-role-policy --role-name feedback-pipeline-transcribe-starter-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name feedback-pipeline-transcribe-starter-role
```

Transcription JOBS persist in the account after completing (listed in the
console for 90 days) but cost nothing to keep. Delete explicitly if you
want a clean list:

```bash
aws transcribe list-transcription-jobs --job-name-contains feedback- \
  --query 'TranscriptionJobSummaries[].TranscriptionJobName' --output text \
  | tr '\t' '\n' | xargs -I{} aws transcribe delete-transcription-job --transcription-job-name {}
```

## Part 2: Transcript collector Lambda + its EventBridge rule

Order matters: remove the TARGET before the rule, or delete-rule fails
with "Rule can't be deleted since it has targets".

```bash
aws events remove-targets --rule feedback-pipeline-transcribe-job-rule --ids 1
aws events delete-rule --name feedback-pipeline-transcribe-job-rule

aws lambda delete-function --function-name feedback-pipeline-transcript-collector
aws logs delete-log-group --log-group-name /aws/lambda/feedback-pipeline-transcript-collector

aws iam delete-role-policy --role-name feedback-pipeline-transcript-collector-role --policy-name feedback-pipeline-transcript-collector
aws iam detach-role-policy --role-name feedback-pipeline-transcript-collector-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name feedback-pipeline-transcript-collector-role
```

The lambda add-permission statement disappears with the function -- a
resource-based policy has no life of its own.

## Part 2: SageMaker Processing (surveys)

Processing JOBS cannot be deleted and cost nothing once finished -- they stay
listed in the console as a record. Only the role and the S3 objects need
removing.

```bash
aws iam delete-role-policy --role-name feedback-pipeline-sagemaker-role --policy-name feedback-pipeline-sagemaker-processing
aws iam delete-role --role-name feedback-pipeline-sagemaker-role

aws s3 rm s3://customer-feedback-analysis/processing-code/ --recursive
aws logs delete-log-group --log-group-name /aws/sagemaker/ProcessingJobs
```

The log group is SHARED by every processing job in the account -- deleting it
removes other jobs' logs too. Leave it unless the account is being emptied.

## Debug captures (none outstanding)

`scripts/eventbridge_capture.sh <source> <name>` creates a rule, a log
group and a Logs resource policy; `eventbridge_capture_remove.sh <name>`
deletes all three. The Transcribe capture was removed on 2026-09-22 after
the collector was verified end to end. CloudWatch Logs allows only 10
resource policies per region, so any capture left running eventually
blocks new ones -- list this section's leftovers before the exam.

## Custom CloudWatch metrics (no deletion needed)

Custom metrics (`CustomerFeedback/TextQuality`,
`CustomerFeedback/DataQuality`) cannot be deleted manually and are not
billed for existing — they simply age out after 15 months of no new
data points. Nothing to do.

## Still to be added as we build further

- CloudWatch Dashboard (cost) — ~$3/month flat, delete this one deliberately
- Any Comprehend/Textract/Transcribe resources that turn out to be
  persistent rather than pay-per-call (most are pay-per-call, no
  cleanup needed, but flagging to verify per-service as we go)

## Part 3: insight report (nothing to delete)

The Bedrock call is pay-per-token with no persistent resource. The only artefact
is one S3 object:

```bash
aws s3 rm s3://customer-feedback-analysis/insights/insight_report.json
```

No role was created: the report builder runs locally under your own
credentials. Deployed as a Lambda it would need `bedrock:InvokeModel` on the
INFERENCE PROFILE arn (not the model arn) plus `comprehend:DetectPiiEntities`.
