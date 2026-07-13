# Plan 0068: CLI Reassign Stale Claim

## Status
Complete

## Goal
Let an operator explicitly reassign a previously inspected stale claim from
the CLI and verify the resulting owner and preserved in-flight state.

## Tasks
- [x] Add a mutating `run reassign-claim RUN_ID REPLACEMENT_AGENT_ID
      --expected-agent-id --expected-revision --threshold-seconds` CLI
      command that transfers ownership only through
      `RunCoordinator.reassign_stale_claim`'s atomic compare-and-swap path
      and prints the resulting sanitized run.
- [x] Confirm existing `run history`/`run inspect` presentation already
      exposes reassignment provenance (`claim_reassigned` transition and the
      replacement owner) without further changes.
- [x] Add focused CLI coverage for success, fresh-owner rejection, stale
      expected-revision contention, missing run, running-step preservation,
      and one-winner-under-concurrency behavior.
- [x] Document the command in DEVELOPMENT, run the full suite, refresh/check
      the index, and run `git diff --check`.

## Resume Notes
Selected the sole ready issue in active Sprint 7, #65. Runtime and state-layer
compare-and-swap behavior were already implemented and verified by #64
(Plan 0067); this slice is CLI presentation only.

Verification: focused CLI suite 6 new reassign-claim tests passed (154 total
in `test_run_cli.py`, up from 148); full suite 408 passed (up from 402);
index rebuilt and current (20 files, 596 symbols, 3456 relationships);
`git diff --check` clean; live CLI UAT confirmed fresh-owner rejection with
no mutation, successful reassignment, and durable reconstruction of the
updated owner and `claim_reassigned` history entry from fresh CLI processes.
