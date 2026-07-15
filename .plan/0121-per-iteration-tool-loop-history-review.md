# Plan 0121: Per-Iteration Tool-Loop History and Review

## Status
Complete

## Goal
Make every durable tool-loop history mutation identify its iteration and safe
phase, then prove a multi-iteration tool task can survive worker replacement
and still be reconstructed without exposing sensitive tool evidence.

## Tasks
- [x] Extend ordered history with a one-based tool iteration and bounded phase.
- [x] Record safe per-iteration activity for execution and rejected responses.
- [x] Cover success, budget exhaustion, resume, cancellation, CLI, and HTTP history.
- [x] Upgrade the committed review to a multi-iteration worker-restart proof.
- [x] Run the full suite, refresh/check the index, and verify the diff.

## Resume Notes
Selected active-milestone issue: #134 (Sprint 22 "Bounded agentic tool loop",
priority:2, `agent-ready`), the sole open and ready issue.

History will add only a one-based iteration number and the persisted
`ToolCallPhase` value. The full normalized provider response, arguments,
command, stdout, stderr, and raw provider evidence remain exclusively on the
trusted durable step record and continue to be redacted from loopback HTTP.

`tool_iteration` and `tool_phase` are nullable additions to the append-only
history schema, so pre-existing lifecycle entries retain their shape while
new tool-loop mutations carry one-based provenance. Requested and executed
entries carry their own phase and bounded outcome; rejected provider
responses retain a phase-only `tool_response_recorded` mutation and gain a
separate atomic rejection entry with the iteration, name, phase, and outcome.

Verification: 9 focused runtime/CLI/API tests passed; the broader
state/runtime/CLI/API/worker group passed 704 tests; the full suite passed
859 tests. `scripts/tool-call-history-review.sh` passed with two actual Python
worker processes and Docker executions. The committed index rebuilt to 27
files / 1334 symbols / 7757 relationships, `index check` reported current,
and `git diff --check` passed.
