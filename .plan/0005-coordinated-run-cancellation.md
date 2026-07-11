# Plan 0005: Coordinated Run Cancellation

## Status
Complete

## Goal
Make cancellation a consistent durable operation across a run and its ordered steps.

## Tasks

- [x] Add a coordinator cancellation operation that cancels queued or running steps while preserving terminal step history.

## Verification

- Cancel queued and running runs with mixed queued, running, and succeeded steps.
- Confirm completed step output is preserved and active steps become durably cancelled.
- Reject cancellation of terminal runs through the existing lifecycle rules.

## Resume Notes

The plan is complete. `RunCoordinator.cancel()` accepts queued or running runs, durably
cancels their queued and running steps in position order, and preserves terminal step
status and output. Missing or terminal runs are rejected through the established
lifecycle errors. Resume by creating a new focused plan for the next execution-core
capability.
