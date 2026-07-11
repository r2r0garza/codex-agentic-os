# Plan 0012: Atomic Run Cancellation

## Status
Complete

## Goal
Ensure coordinated run cancellation cannot persist only a subset of its run and step updates.

## Tasks
- [x] Add a narrow transaction-capable state-store batch boundary.
- [x] Validate and persist all cancellation updates in one SQLite transaction.
- [x] Prove a persistence failure rolls back every cancellation update.

## Resume Notes
Selected queue issue: #1. The plan is complete. `RunCoordinator.cancel()` validates the run and all active step
transitions before passing their prepared records to one `StateStore.put_many()` SQLite
transaction. Terminal steps remain untouched, including their revisions and output, and
an injected mid-batch failure rolls back every update. Resume with the next prioritized
`agent-ready` GitHub issue.
