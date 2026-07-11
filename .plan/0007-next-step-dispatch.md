# Plan 0007: Next-step Dispatch

## Status
Complete

## Goal
Provide one coordinator operation that starts the next queued step in durable position
order without allowing multiple active steps in the same run.

## Tasks

- [x] Add backend-neutral next-step dispatch with lifecycle validation and tests.

## Verification

- Start the first queued step and transition its queued run to running.
- Start the next queued step after the current step succeeds.
- Reject dispatch while another step is running or when the run is terminal.
- Return no step when a non-terminal run has no queued work.

## Resume Notes

The plan is complete. `RunCoordinator.start_next_step()` advances a queued run to
running, starts its earliest queued step in durable position order, rejects concurrent
active-step dispatch and terminal runs, and returns `None` when no queued work remains.
Resume by creating a new focused plan for the next execution-core capability; command
execution remains a caller responsibility.
