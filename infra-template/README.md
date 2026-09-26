# infra-template/

Portable version of the infrastructure runbook. **This is the committed
copy** — it contains no account IDs and no hardcoded ARNs, so it runs in
any AWS account.

There is a second directory, `infra/`, which is the same scripts with
every value written out literally for this sandbox account. That one is
gitignored. Easier to read a single command out of and paste; useless to
anyone else.

## What this is and is not

A **runbook**, not infrastructure-as-code. CloudFormation, CDK and
Terraform track state, so re-running them converges on the desired
result. These scripts call `create-*` APIs, which fail if the resource
already exists — they build from empty, they do not reconcile.

## Run order

```bash
bash infra-template/00_s3.sh                    # bucket + upload data
bash infra-template/01_iam_glue.sh              # Glue service role
bash infra-template/02_glue_catalog.sh          # database, crawler, crawl
bash infra-template/03_glue_dq.sh               # DQ ruleset + evaluation
bash infra-template/04_lambda_text_validator.sh # review validation Lambda
bash infra-template/05_lambda_dq_publisher.sh   # DQ score -> CloudWatch
bash infra-template/06_lambda_comprehend.sh     # Comprehend enrichment
bash infra-template/07_s3_notifications.sh      # ALL S3 triggers — last
```

Numbering is dependency order. `02` needs the role from `01`, `03` needs
the table from `02`, `07` needs every Lambda it references to exist.

Generate source data first if `data/raw/` is empty:

```bash
python3 data/generate_feedback_dataset.py
```

Reviews, images and surveys are regenerated deterministically; the three call
recordings are pre-committed under `data/raw/calls/` and are not regenerated.

## How parameterisation works

`common.sh` is sourced by every script and holds every shared value in
one place. The account ID is never stored — it is resolved at runtime:

```bash
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
```

Policy documents needing the account ID or bucket name are `.template`
files with `${AWS_ACCOUNT_ID}` / `${DATA_BUCKET}` / `${AWS_REGION}`
placeholders, rendered by `render_template` into `.build/` (gitignored)
at run time.

To run in another account, override nothing — the account ID resolves
itself. Bucket names are globally unique across all of AWS, so you will
want a different suffix:

```bash
PROJECT_INITIALS=abc bash infra-template/00_s3.sh
```

## Keeping the two copies in step

`infra/` and `infra-template/` contain the same logic twice, so they will
drift the moment one is edited alone. Treat `infra-template/` as the
source of truth and mirror changes down to `infra/`, not the reverse.
