# Plan 0119: Bounded Iterative Tool Execution

## Status
Complete

## Goal
Replace the single-round in-memory tool follow-up with a bounded loop whose
provider responses, requested calls, and sandbox results are durably ordered
before the next side effect.

## Tasks
- [x] Add a durable ordered tool-iteration record while retaining trusted
      compatibility access to the latest call.
- [x] Replay completed iteration turns into each subsequent provider request.
- [x] Enforce the explicit iteration budget and preserve exhaustion evidence.
- [x] Cover multi-iteration success, durable phase ordering, exhaustion,
      undeclared calls, and one-iteration compatibility with focused tests.
- [x] Run the full suite, refresh the index, and complete documentation.

## Resume Notes
Selected active-milestone issue: #132 (Sprint 22 "Bounded agentic tool
loop", priority:1, `agent-ready`), the sole unblocked issue. Issues #133 and
#134 remain correctly blocked on this slice and later recovery/history work.

The budget bounds completed sandbox tool executions. When the model requests
another call after that many executions, the response is still persisted as
`rejected_budget` evidence but no additional sandbox side effect occurs.
This lets a budget of one continue to represent the existing one-tool-call
workflow (one execution followed by a final response).

`RunStep.tool_iterations` is the ordered typed view and `tool_iterations` is
the durable payload key. `RunStep.tool_call` and trusted JSON `tool_call`
remain latest-call compatibility aliases, so existing one-round callers do
not lose their inspection contract. Legacy durable records containing only
`tool_call` are upgraded into a one-entry typed sequence when read; all new
lifecycle writes use only the ordered key.

Each iteration stores the normalized response content, model, raw provider
evidence when available, usage evidence, and the call record. Requested and
executed writes remain CAS-guarded; an undeclared request is stored as
`rejected_undeclared`, and a declared request beyond the execution budget as
`rejected_budget`, before the existing definitive failure transaction. The
loop replays only normalized assistant/tool turns and never exposes or
interpolates the persisted command template.

Verification: the focused runtime/API checks passed 15 tests; all 296 runtime
tests passed; the CLI/API/worker/chat group passed 403 tests; full `pytest`
passed 853. The index rebuilt to 27 files / 1289 symbols / 7611
relationships, `index check` reported current, and `git diff --check` passed.
