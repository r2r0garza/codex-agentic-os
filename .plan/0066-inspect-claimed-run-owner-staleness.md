# Plan 0066: Inspect Claimed-Run Owner Staleness

## Status
Complete

## Goal
Give an operator a deterministic, read-only way to learn whether a claimed
run's owning agent is stale relative to an explicitly supplied threshold,
before any reassignment is attempted.

## Tasks
- [x] Add a clock-injectable `RunCoordinator.evaluate_claim_staleness` that
      compares a claimed run's owning agent's durable `last_seen` heartbeat
      against an operator-supplied positive threshold and the coordinator's
      current time.
- [x] Reject unclaimed runs, unregistered owners, legacy owners without a
      heartbeat, invalid thresholds, and naive/ambiguous timestamps without
      any state or history mutation.
- [x] Add read-only `run staleness RUN_ID --threshold-seconds N` CLI
      inspection reporting owner, last-seen time, threshold, evaluation
      time, and the stale result.
- [x] Add focused runtime and CLI coverage for fresh/stale boundary
      behavior, restart durability, invalid input, and no mutation.
- [x] Run the full suite, refresh/check the index, and run `git diff --check`.

## Resume Notes
Selected queue issue #63, the sole ready issue in active Sprint 7. Staleness
is evaluated only when explicitly requested; automatic reassignment,
background monitoring, and notifications remain out of scope for issues
#64 and #65.

Verification: focused runtime suite 11 staleness tests passed (111 total in
`test_runtime.py`); focused CLI suite 7 staleness tests passed (149 total in
`test_run_cli.py`); full suite 398 passed (up from 380); index rebuilt and
current (20 files, 582 symbols, 3287 relationships); `git diff --check`
clean; live CLI UAT confirmed fresh/stale evaluation, unclaimed/missing-run/
invalid-threshold rejection, and unchanged run revision across evaluations.
