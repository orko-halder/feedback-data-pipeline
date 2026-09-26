# Project conventions

## Code must be testable in isolation

Any function containing real logic (validation rules, scoring,
transforms, parsing) should be written so it can be unit tested with
zero AWS dependency -- no live credentials, no network call, no
deployed resource required to verify it's correct.

Concretely:
- boto3 clients (or any external-service client) are constructed
  inside the function/handler that uses them, never at module level.
  Module-level client construction forces credential/region
  resolution the instant the file is imported, which makes it
  impossible to import and test the pure logic in an environment
  without AWS configured -- even when that logic itself never touches
  AWS.
- Side-effecting calls (S3 read/write, CloudWatch, Glue API calls,
  etc.) should be isolated at the edges of a function, with the
  decision-making logic (what counts as valid, what score to assign)
  kept as a separate, pure, directly-callable function.
- Before deploying or wiring up any new Lambda/handler, write and run
  a local unit test against its pure logic first. A live AWS trigger
  test afterward is an integration test, not a substitute for this --
  it confirms wiring, not correctness of the logic itself.

## Minimize concrete dependencies where possible

Prefer passing dependencies in (or constructing them at the narrowest
possible scope) over hardcoding a specific client, table name, or
resource inline deep in a function. Config values live in config.py,
not scattered as literals -- makes swapping/mocking straightforward
without a rewrite.

## Definition of done for a new pipeline stage

A stage is not finished when it works in AWS. All five of these, in order,
before moving on:

1. **Lambda code written**, with boto3 clients constructed inside the
   handler and the decision logic split into pure functions.
2. **Unit test written and run** against those pure functions, before any
   deploy. A live AWS trigger test afterwards confirms wiring, not
   correctness.
3. **Infra script written in BOTH copies** — `infra/` (literal values,
   gitignored) and `infra-template/` (parameterised, committed). Write
   both at the same time; they drift the moment one is done alone.
   `infra-template/` is the source of truth, mirror down to `infra/`.
4. **`TEARDOWN.md` updated** with the delete commands for whatever was
   created, in dependency-safe order, marking anything with a standing
   cost.
5. **`EXAM_NOTES.md` updated** (at the AWS_tutorials root) with any
   non-obvious behaviour hit along the way, in brief Q&A form.

Never run an `aws` command that creates or configures a resource without
it landing in both infra copies. A command that only exists in chat
scrollback is lost.
