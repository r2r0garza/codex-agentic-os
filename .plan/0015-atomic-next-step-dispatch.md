# Plan 0015: Atomic Next-step Dispatch

## Status
Complete

## Goal
Ensure first-step dispatch cannot persist a running run without also persisting its
earliest queued step as running.

## Tasks
- [x] Commit the queued run and earliest queued step transitions in one transaction.
- [x] Preserve single-record dispatch for runs that are already running.
- [x] Prove an injected mid-batch failure rolls back both transitions.

## Resume Notes
Selected queue issue: #6. The plan is complete. `RunCoordinator.start_next_step()` now
uses `StateStore.put_many()` when the first queued step starts, so the run and step
transitions commit together and each revision advances exactly once. Later dispatches
on an already-running run continue to update only the next step. Resume with the next
prioritized unblocked `agent-ready` GitHub issue.
