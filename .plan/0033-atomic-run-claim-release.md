# Plan 0033: Atomic Run Claim Release

## Status
Complete

## Goal
Allow an exact worker assignment to be released from a queued durable run without
overwriting a newer owner or lifecycle state.

## Tasks
- [x] Clear an exact queued assignment in one SQLite write transaction.
- [x] Reject missing, unassigned, mismatched, and non-queued runs without mutation.
- [x] Verify revision advancement, stale coordinator safety, and unrelated records.

## Resume Notes
Selected queue issue: #24. `RunCoordinator.release_claim(run_id, agent_id)` delegates
exact owner and queued-lifecycle validation to `StateStore.release_run_claim()` under
one immediate SQLite transaction. A successful release advances the revision once and
leaves the run unassigned; stale owners cannot clear a newer assignment. Resume with
the next prioritized unblocked `agent-ready` GitHub issue.
