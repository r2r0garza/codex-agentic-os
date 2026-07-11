# Plan 0008: Durable Step Command

## Status
Complete

## Goal
Give ordered run steps an optional, backend-neutral command specification that survives
process restarts and can be consumed by a later execution operation.

## Tasks

- [x] Persist validated command arguments and an optional timeout on ordered run steps.

## Verification

- Round-trip command arguments and timeouts through SQLite.
- Preserve compatibility for coordination-only steps without commands.
- Reject empty commands, empty arguments, and non-positive timeouts before persistence.

## Resume Notes

The plan is complete. `RunCoordinator.add_step()` now accepts an optional command and
timeout, and `RunStep` exposes those inputs after durable reloads. Objective-only steps
remain supported. Resume by creating a focused plan for executing the next command step
through an injected sandbox boundary; do not make dispatch perform subprocess work
implicitly.
