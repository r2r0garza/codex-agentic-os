# Plan 0064: Approve and Reject Pending Step Decisions

## Status
Complete

## Goal
Let an operator decide a pending approval-gated step: approval clears the
dispatch gate from Plan 0063 so execution proceeds normally, and rejection
produces an explicit terminal step/run outcome without ever executing it.

## Tasks
- [x] Add `RunCoordinator.approve_step`/`reject_step`, backed by `StateStore.put_many`
      compare-and-swap on the step's (and, for rejection, the run's) expected
      status and revision.
- [x] Reject a decision against an already-decided (non-pending) step or a stale
      expected revision without mutating state or appending history.
- [x] Append an atomic run-history entry (`step_approved`/`step_rejected`) recording
      the deciding agent id when known, in the same transaction as the mutation.
- [x] Add focused persistence, dispatch-unblock, rejection, conflict, and history tests.
- [x] Run the full suite, refresh/check the index, and run `git diff --check`.

## Resume Notes
Selected queue issue #60. CLI approval inspection and decision commands remain
reserved for #61.

Verification: focused runtime suite 101 passed; full suite 376 passed; index
rebuilt and current (20 files, 559 symbols, 3097 relationships); `git diff --check`
clean.
