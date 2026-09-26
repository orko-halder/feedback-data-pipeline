# Next steps — Task 1.3

Working state, not a résumé doc. Tick items off; delete the file when the
task is finished. Conventions for any new stage are in CLAUDE.md
(definition of done: code -> unit test -> infra in BOTH copies ->
TEARDOWN.md -> EXAM_NOTES.md).

## Start here next session

**The assignment is complete** -- Parts 1-4 all built, deployed and measured.
What remains is packaging and the surfaces deliberately left for next time:

1. `docs/production-gaps.md` -- the honest audit (what we would change for
   production, what is demo scaffolding). Strongest interview artefact here.
2. RESUME_SUMMARY.md -- rewrite with Parts 3 and 4 included.
3. Next assignment: Bedrock **Guardrails** (replaces our hand-rolled PII
   redaction and injection rule) and **Knowledge Bases / RAG** -- the biggest
   exam surface still untouched.

## Done

- [x] Part 1: review validation Lambda, Glue Crawler + Catalog, Glue Data
      Quality ruleset, DQ score publisher, CloudWatch metrics
- [x] Part 2: reviews -> Comprehend
- [x] Part 2: images -> Textract
- [x] Part 2: calls -> Transcribe starter (Lambda 1)
- [x] Part 2: calls -> transcript collector (Lambda 2), deployed and
      verified end to end 2026-09-22; design B (per-speaker documents)
      chosen against hand labels
- [x] Debug capture for aws.transcribe removed
- [x] Dataset regenerated around a written ground truth (3 issue products,
      2 controls, 5 planted mismatches); Glue crawler + DQ + Processing re-run
- [x] Part 3a: feedback_document.py normaliser, 4 shapes -> 1 (46 checks)
- [x] Part 3b: build_insight_report.py -- S3 collect, Comprehend PII redaction
      with an allow-list, one Bedrock call (Haiku 4.5 via us. inference
      profile), JSON validation, hallucinated-citation check
- [x] scripts/score_insight_report.py -- scores the report against ground truth
- [x] Part 4: quality_review.py -- deterministic mismatch detector (5/5,
      precision 1.0, recall 1.0), review queue in S3, MismatchRate metric,
      escalation by whether a rule has any signal (30 unit checks)
- [x] (was blocking Part 4) survey satisfaction now DERIVED from the ratings,
      with 5 mismatches planted deliberately -- Part 4 has a known answer
- [x] Part 2: surveys -> SageMaker Processing, job Completed 2026-09-22
      (ml.m5.large, SKLearn image 1.2-1, 30 rows -> 28 admissible)

## Open

- [ ] **Capture the Glue Data Quality EventBridge event** and save it to
      `docs/api-responses/`. The Transcribe (THIN) event is saved; the
      Glue DQ (FAT, carries the score) one is not, so the pair that makes
      the thin-vs-fat distinction concrete is incomplete.
      `bash scripts/eventbridge_capture.sh aws.glue-dataquality gluedq`,
      start a DQ run, read the log group, save the payload, then
      `bash scripts/eventbridge_capture_remove.sh gluedq`.
      (Logs allows only 10 resource policies per region -- remove it.)
- [ ] `scripts/capture_api_responses.sh` -- one script to refresh the
      saved payloads.
- [ ] Update RESUME_SUMMARY.md once Parts 3 and 4 land.

## Optional / strengthens the evidence

- [ ] Transcribe calls 002 and 003, hand-label them, rerun
      `scripts/build_comprehend_compare_requests.py` on each. The design B
      verdict currently rests on ONE short call.
- [ ] Exercise the truncation path for real (a call whose single turn
      exceeds 5,000 bytes). Covered by unit test, never seen live.
