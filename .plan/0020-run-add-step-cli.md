# Plan 0020: Run Add-step CLI

## Status
Complete

## Goal
Expose durable command-step append through the operator CLI while preserving coordinator validation and atomic position selection.

## Tasks
- [x] Add `run add-step` with a required objective and command plus an optional timeout.
- [x] Print the standard run payload with all steps in durable position order.
- [x] Verify command round trips, timeout persistence, ordering, and rejection without mutation.

## Resume Notes
Selected queue issue: #4. `codex-agentic-os run add-step` delegates command, timeout,
lifecycle, duplicate-identifier, and position validation to `RunCoordinator.add_step()`
and prints the resulting run with its ordered steps. Resume with the next prioritized
unblocked `agent-ready` GitHub issue.
