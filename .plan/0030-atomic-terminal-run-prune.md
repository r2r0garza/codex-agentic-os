# Plan 0030: Atomic Terminal Run Prune

## Status
Complete

## Goal
Allow explicit cleanup of one terminal durable run without leaving orphaned steps.

## Tasks
- [x] Delete one succeeded, failed, or cancelled run and all of its steps in one transaction.
- [x] Return the removed run and position-ordered steps as typed records.
- [x] Reject active and missing runs without mutation and prove deletion rollback.

## Resume Notes
Selected queue issue: #16. `RunCoordinator.prune()` delegates terminal validation and
atomic run-and-step deletion to `StateStore.prune_run()`, returning the removed typed run
and its position-ordered step history. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.
