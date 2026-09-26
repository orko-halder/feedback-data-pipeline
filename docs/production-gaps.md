# Production gaps — what this pipeline would need, and what is scaffolding

Written deliberately: the pipeline works and is measured, but it was built to
demonstrate AWS surface for a certification, and those two goals do not always
agree. Everything below is a decision made with a reason, not an oversight.

## Choices that would change in production

| Built | Would be | Why |
|---|---|---|
| Textract **AnalyzeDocument FORMS** | `DetectDocumentText` | The FORMS key/values were audited and found unreliable (a real label scored 60.0, a fabricated one 75.4). Only `full_text` is used downstream, and FORMS costs strictly more to produce it. |
| Comprehend `DetectEntities` + `DetectKeyPhrases` on reviews | removed | Nothing consumes them. Two API calls per review for output no stage reads. |
| **SageMaker Processing** for 30 survey rows | Lambda | Measured 2m45s of provisioning for a script that runs in milliseconds. Processing earns its place on data that does not fit in memory, needs heavy libraries, or must be sharded across instances. |
| `infer_roles` first-speaker guess | Transcribe **Call Analytics** with `ChannelDefinitions` | Stereo channels give AGENT/CUSTOMER deterministically. A labelled guess is still a guess in the output. |
| Glue DQ **and** per-row validation on surveys | one of them, justified | DQ gives a dataset-level verdict and a trend metric; the row check gives a per-record decision that travels with the data. Keeping both is defensible only if you can say that in one sentence. |
| Hand-rolled PII redaction + an anti-injection prompt rule | **Bedrock Guardrails** | Managed PII masking and prompt-injection filtering as configuration. Our version works and taught more, but it is ours to maintain. |
| Shell scripts in `infra/` | CDK or CloudFormation | The scripts are a runbook: ordered, commented, idempotent in places. They are not infrastructure-as-code — no drift detection, no rollback, no dependency graph. |

## Missing entirely

| Gap | Consequence today |
|---|---|
| **Dead-letter queues + alarms** on every Lambda | A failed invocation disappears. Nothing notices. |
| **Idempotency** | S3 notifications are at-least-once. We survive by overwriting the same key — correct by luck, not design. A duplicate Transcribe start would cost real money. |
| **Partitioned output** (`dt=YYYY-MM-DD/`) | Every run overwrites a flat prefix. No history, no point-in-time comparison, no cheap Athena scan. |
| **Orchestration** (Step Functions / Glue workflows) | Crawler -> DQ -> Processing -> report is a sequence of terminal commands. Nothing retries, nothing branches, nothing reports where it stopped. |
| **Prompt caching / batch inference** | The same ~8k-token prompt is re-sent in full on every run. |
| **n-run evaluation** | Every prompt comparison in EXAM_NOTES is n=1. Run 3 changed recall, precision AND citation hygiene from a change that only removed a field -- that is variance, not causation. |
| Schema versioning on the feedback document | A consumer cannot tell v1 from v2. |
| Tests in CI | 8 test files, 200+ checks, all run by hand. |

## What would survive review unchanged

- **S3 prefix-staged lake with event-driven Lambdas.** Prefix separation is not
  cosmetic: a Lambda writing into the prefix that triggers it runs forever.
- **FATAL vs ADVISORY severity**, with `admissible` meaning the same thing in
  every stage, and the quality gate upstream of the expensive API calls.
- **Provenance in the output, not in a wiki.** `role_method`,
  `textract_key_values`, `sentiment_source`, `trust_notes`, `pii_allowlisted` --
  every uncertain field says how it was produced.
- **Least-privilege IAM by hand.** No `*FullAccess` anywhere; `PutMetricData`
  scoped by the `cloudwatch:namespace` condition key, invoke permissions scoped
  by `SourceArn`.
- **Ground truth written before the model ran**, so the FM stage is scored
  rather than admired -- and the deterministic tier is scored the same way, on
  the same records, which is what makes "rule vs model" a measurement.
- **The two-tier design**: a deterministic scorer on 100% of records feeding an
  alarmable rate, and an FM for what needs reading. This matches what Amazon
  Connect Contact Lens actually does (per-utterance scoring + generative
  post-contact summaries), rather than sending everything to a model.

## The one thing I would fix first

Idempotency and DLQs. Everything else on this page is a cost or clarity
question; those two are the difference between a pipeline that fails loudly and
one that loses data quietly.
