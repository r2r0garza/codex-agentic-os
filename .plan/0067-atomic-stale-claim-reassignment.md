# Plan 0067: Atomic Stale-Claim Reassignment

## Status
Complete

## Goal
Transfer a queued or running run from a demonstrably stale owner to a registered
replacement exactly once, without changing any step state.

## Tasks
- [x] Add one SQLite transaction that compares run owner and revision, re-reads the
      durable heartbeat, verifies explicit-threshold staleness, transfers ownership,
      and appends reassignment history atomically.
- [x] Add a clock-driven coordinator operation with explicit expected owner/revision.
- [x] Cover success, fresh-owner and heartbeat-race rejection, contention, restart,
      and byte-for-byte running-step preservation with focused tests.
- [x] Run the full suite, refresh/check the index, and run `git diff --check`.

## Resume Notes
Selected active Sprint 7 queue issue #64. CLI presentation remains reserved for
#65; automatic reassignment and uncertain-step recovery remain out of scope.

Verification: focused state/runtime suites passed (148 tests); full suite passed
(402 tests); index rebuilt and current (20 files, 589 symbols, 3367 relationships);
`git diff --check` clean.
