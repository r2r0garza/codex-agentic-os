# Plan 0037: Atomic Durable Run Transitions

## Status
Complete

## Goal
Prevent competing coordinators from overwriting a newer durable run lifecycle state
during an explicit `RunCoordinator.transition()` call.

## Tasks
- [x] Add `StateStore.transition_run()`: a SQLite `BEGIN IMMEDIATE` transaction that
      re-reads a run's status and revision, compares them against caller-supplied
      `expected_status`/`expected_revision`, and only then writes the new status,
      payload, and incremented revision. Mismatches raise `StateConflictError` without
      mutating the record; a missing run raises `KeyError`.
- [x] Route `RunCoordinator.transition()` through `transition_run()`, passing the
      snapshot it already reads via `self.get()` as the expected status/revision.
      Preserve the existing lifecycle-edge and terminal-output validation performed
      before the store call, and preserve the existing public error types
      (`ValueError` for invalid/conflicting transitions, `KeyError` for a missing run).
- [x] Add `StateStore`-level tests for a successful compare-and-transition, a missing
      run, mismatched expected status/revision (no mutation), and invalid arguments.
- [x] Add a `RunCoordinator`-level concurrency test using two coordinators against the
      same database and `ThreadPoolExecutor` to prove only one of two competing
      `QUEUED -> RUNNING` transitions can succeed.

## Resume Notes
Selected queue issue: #28. `RunCoordinator.transition()` no longer calls
`StateStore.put()` (an unconditional upsert); it calls the new
`StateStore.transition_run()`, which performs the compare-and-swap under one write
transaction. `cancel()`, `start_next_step()`, and step transitions were left unchanged —
out of this issue's bounded scope. The step-transition equivalent is tracked separately
as issue #29 ("Make durable step transitions atomic"). Resume with the next prioritized
unblocked `agent-ready` GitHub issue.
