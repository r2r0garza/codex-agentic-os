# Plan 0010: Running Step Recovery

## Status
Complete

## Goal
Explicitly reconcile a running step whose executor was interrupted or timed out before
returning a durable command result.

## Tasks

- [x] Add typed recovery that durably fails an uncertain running step and its run.

## Verification

- Recover interrupted and timed-out running steps with durable reason metadata.
- Reject missing steps, non-running state, invalid reasons, and empty detail without
  changing lifecycle state.

## Resume Notes

The plan is complete. `RunCoordinator.recover_running_step()` records either an
`interrupted` or `timed_out` reason, optionally preserves operator detail, and fails the
running step and run. Recovery deliberately does not retry because the prior command's
side effects may be uncertain. Resume by creating a focused plan for the next execution
core capability.
