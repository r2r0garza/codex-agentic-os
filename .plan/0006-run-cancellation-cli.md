# Plan 0006: Run Cancellation CLI

## Status
Complete

## Goal
Expose coordinated durable run cancellation as a small operator-facing command without
changing the established lifecycle contract.

## Tasks

- [x] Add a `run cancel` CLI command that cancels active runs and prints the resulting run and ordered steps.

## Verification

- Cancel a running run with active and completed steps and verify the persisted JSON view.
- Reject missing databases, missing runs, and terminal runs without creating new state.

## Resume Notes

The plan is complete. `codex-agentic-os run cancel <run-id>` opens an existing state
database for mutation, delegates lifecycle enforcement to `RunCoordinator.cancel()`,
and prints the cancelled run with its position-ordered steps. Resume by creating a new
focused plan for the next execution-core capability.
