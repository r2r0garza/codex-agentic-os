# Plan 0061: Atomic Mixed-Step History

## Status
Complete

## Goal
Extend the durable run history contract across command and provider step starts,
completion, failure, cancellation, recovery, and their coupled run transitions,
without persisting sensitive step inputs.

## Tasks
- [x] Add optional step identity to durable history with an in-place SQLite schema migration.
- [x] Append command/provider step history inside the same transaction as each mutation.
- [x] Make coupled run/step writes compare-and-swap safe and append ordered history atomically.
- [x] Cover mixed execution, restart reconstruction, and rejected-write rollback.
- [x] Run the full suite, refresh/check the index, and run `git diff --check`.

## Resume Notes
Selected queue issue #56. The implementation uses `command` and `provider` as the
non-sensitive execution categories and stores only run/step identifiers, transition,
resulting status, responsible agent, and category. Existing databases gain the nullable
`step_id` column during normal initialization. Coupled mutations validate every expected
status/revision after `BEGIN IMMEDIATE`, then write state and history before one commit.

Verification: focused state/runtime suite 123 passed; full suite 361 passed; index
rebuilt/current (20 files, 529 symbols, 2925 relationships); `git diff --check`
clean. Mixed command/provider execution was reconstructed from a fresh store instance,
and a stale batch expectation left both state and history unchanged.
