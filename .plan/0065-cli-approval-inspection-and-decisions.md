# Plan 0065: CLI Approval Inspection and Decisions

## Status
Complete

## Goal
Let an operator create, inspect, approve, or reject a durable approval-gated
step entirely through the CLI while keeping sensitive execution input out of
the approval inspection view.

## Tasks
- [x] Add CLI creation of approval-required command and provider steps.
- [x] Add a read-only sanitized approval listing with execution kind and known
      requesting/deciding agent attribution.
- [x] Add approve and reject commands using the existing atomic coordinator operations.
- [x] Add focused CLI coverage and end-to-end durable history reconstruction.
- [x] Document the commands and complete full verification.

## Resume Notes
Selected queue issue #61, the sole ready issue in active Sprint 6.

Focused CLI verification: 141 passed. Full verification and index evidence are
recorded in the repository run handoff.
