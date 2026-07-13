# Plan 0063: Durable Step Approval Gate

## Status
Complete

## Goal
Allow command and provider steps to be durably marked as approval-required and
prevent pending steps from entering the existing execution lifecycle.

## Tasks
- [x] Add typed approval metadata to `RunStep` and its stored payload.
- [x] Default existing step creation to no approval requirement.
- [x] Reject pending-approval dispatch before any durable mutation or execution.
- [x] Add focused persistence, dispatch, and regression tests.
- [x] Run the full suite, refresh/check the index, and run `git diff --check`.

## Resume Notes
Selected queue issue #59. The approval decision operations and CLI presentation are
reserved for later Sprint 6 issues; this slice establishes only the durable gate.

Verification: focused runtime suite 95 passed; full suite 370 passed; index rebuilt
and current (20 files, 547 symbols, 3021 relationships); `git diff --check` clean.
