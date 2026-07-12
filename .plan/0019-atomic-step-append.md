# Plan 0019: Atomic Step Append

## Status
Complete

## Goal
Ensure competing coordinators cannot reuse a durable run-step position or overwrite a
globally unique step identifier.

## Tasks
- [x] Select the next per-run position and insert the queued step in one SQLite write transaction.
- [x] Reject duplicate step identifiers without changing their payload or revision.
- [x] Prove separate coordinators append distinct contiguous positions.

## Resume Notes
Selected queue issue: #10. `RunCoordinator.add_step()` now delegates duplicate checking,
per-run position selection, and insertion to `StateStore.append_step()` under one
immediate SQLite transaction. Successful steps begin at revision 1, and competing store
instances cannot overwrite an identifier or select the same position. Resume with the
next prioritized unblocked `agent-ready` GitHub issue.
