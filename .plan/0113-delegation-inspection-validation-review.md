# Plan 0113: Delegation Inspection, Validation, and Interruption Review

## Status
Complete

## Goal
Make parent-child delegation provenance visible from both inspection/history
directions, reject detectable agent-lineage self-delegation and cycles before
mutation, and prove the workflow across a two-worker interruption.

## Tasks
- [x] Persist parent/child linkage on delegation history and expose it through
      the existing read-only CLI/API payload.
- [x] Reject delegation to the current run's assigned agent or any assigned
      ancestor agent before appending the step.
- [x] Add focused runtime, state migration, CLI inspection/history, and
      redaction-stability tests.
- [x] Add and run a reproducible two-agent worker interruption review.
- [x] Refresh the index and complete proportional verification.

## Resume Notes
Selected active-milestone issue: #124 (Sprint 20 "Parent-child run
delegation", priority:2, `agent-ready`), the sole open issue. Dependencies
#121, #122, and #123 are closed. Existing inspection already exposes
`delegated_run_id` on the parent step and `parent_run_id`/`parent_step_id` on
the child run; this slice makes the same relationship durable in history and
adds the missing declaration-time lineage checks and end-to-end review.

`RunHistoryEntry` and the additive SQLite history migration now carry
`delegated_run_id` for the parent's `step_delegated` entry and
`parent_run_id`/`parent_step_id` for the child's initial `created` entry. The
shared payload builder omits absent linkage fields, preserving existing
history shapes and redaction behavior for unrelated runs.

Self-delegation and cycles are defined over declared durable agent assignment:
a target may not equal the current run's agent or any assigned ancestor run's
agent. The check walks the existing parent linkage and rejects before
`append_step`; unassigned edges remain valid because no agent cycle is
detectable. The visited-run guard also rejects malformed pre-existing parent
cycles rather than looping.

`scripts/delegation-interruption-review.sh` uses two registered identities and
a Docker command step. It terminates the parent worker after atomic dispatch,
lets the child worker succeed, restarts the parent identity in a fresh worker
process, and asserts terminal reconciliation plus both inspection/history
directions. Verification: review passed; 29 focused delegation tests passed;
full `pytest` passed 793 tests; refreshed index contains 27 files, 1146 symbols,
and 7040 relationships; `index check` and `git diff --check` passed.
