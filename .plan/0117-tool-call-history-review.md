# Plan 0117: Tool-Call History and Review

## Status
Complete

## Goal
Record safe, durable tool activity for successful and failed single-round
provider steps, preserve rejected undeclared-tool evidence atomically with
failure, and prove the operator can reconstruct a tool-using step without
leaking sensitive tool details over HTTP.

## Tasks
- [x] Extend ordered run history with a tool name and bounded outcome while
      deliberately excluding model arguments, command argv, environment
      values, provider payloads, and terminal output.
- [x] Record requested, executed, and undeclared-tool rejection activity in
      the same transactions as their corresponding durable step mutations.
- [x] Preserve trusted local CLI inspection while redacting tool declarations,
      calls, and captured results from loopback HTTP step payloads.
- [x] Add focused persistence, runtime, CLI, and API coverage for successful,
      failed, rejected, and redacted tool activity.
- [x] Commit and execute an end-to-end review that reconstructs one successful
      tool-using provider step from durable state.
- [x] Run the full suite, refresh and check the code index, and verify the diff.

## Resume Notes
Selected active-milestone issue: #129 (Sprint 21 "Durable model tool calling",
priority:2, `agent-ready`), the sole open and ready issue.

History will carry only `tool_name` and a small outcome vocabulary. The full
request/result remains on the step record for trusted local CLI inspection;
history must not duplicate model arguments, command argv, or terminal output.
An undeclared request is recorded as `rejected_undeclared` atomically with the
existing definitive step/run failure so there is no observable state where the
evidence and failure disagree.

The established retry path initially copied a failed tool step's sandbox policy
but not its tool declarations, producing an invalid new attempt. The retry copy
now preserves `tools` (and its optional provider-response artifact declaration)
while intentionally dropping the prior attempt's `tool_call` evidence.

The loopback HTTP API applies Decision 0008 recursively to tool details:
declaration commands and call arguments, executed command, stdout, and stderr
are replaced with `<redacted>`. Tool name, phase, and exit code remain visible;
trusted local CLI inspection continues to show the complete durable record.

Verification: 7 focused runtime tool-call tests, the CLI tool-call test, and the
HTTP tool-redaction test passed; the complete state/runtime/CLI/API files passed
43/286/250/89 tests; the full suite passed 843 tests. The Docker-backed
`scripts/tool-call-history-review.sh` passed. The committed index was refreshed
to 27 files / 1270 symbols / 7511 relationships, `index check` reported current,
and `git diff --check` passed.
