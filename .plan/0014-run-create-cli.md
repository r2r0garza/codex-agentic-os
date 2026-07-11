# Plan 0014: Run Create CLI

## Status
Complete

## Goal
Expose queued durable run creation through the operator CLI.

## Tasks
- [x] Add `run create` with a required objective and optional agent identifier.
- [x] Create the state database when needed and print the standard run payload.
- [x] Verify durable round trips and rejection without mutation.

## Resume Notes
Selected queue issue: #3. The plan is complete. `codex-agentic-os run create` delegates
validation and persistence to `RunCoordinator.create()`, creates the configured state
database when needed, and prints the same run-plus-steps representation as `run inspect`.
Resume with the next prioritized unblocked `agent-ready` GitHub issue.
