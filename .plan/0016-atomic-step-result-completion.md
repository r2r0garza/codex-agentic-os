# Plan 0016: Atomic Step Result Completion

## Status
Complete

## Goal
Ensure recording a terminal sandbox result cannot leave its durable step and run in inconsistent lifecycle states.

## Tasks
- [x] Commit failed-step and failed-run result updates in one transaction.
- [x] Commit final successful-step and successful-run result updates in one transaction.
- [x] Preserve single-record completion for successful non-final steps.
- [x] Prove injected mid-batch failures roll back both coupled updates.

## Resume Notes
Selected queue issue: #7. `RunCoordinator.complete_step_from_result()` now prepares the
terminal step and, when applicable, run records before committing them through
`StateStore.put_many()`. Successful non-final steps still update only the step. Resume
with the next prioritized unblocked `agent-ready` GitHub issue.
