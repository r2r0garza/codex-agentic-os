# Plan 0048: Operator Step Transition CLI

## Status
Complete

## Goal
Expose explicit, validated durable step lifecycle transitions without command execution.

## Tasks
- [x] Add `run transition-step STEP_ID STATUS` for coordinator-supported lifecycle edges.
- [x] Parse optional terminal output as a JSON object before any state mutation.
- [x] Print the standard machine-readable step payload.
- [x] Verify coordination-only and command-bearing records, durable output, family
  preservation, and rejection without mutation.

## Resume Notes
Selected queue issue: #26. `run transition-step` delegates lifecycle and terminal-output
validation to `RunCoordinator.transition_step()`, while its CLI boundary rejects
malformed or non-object JSON before mutation. It does not execute commands or alter the
parent run or sibling steps. Resume with the next prioritized unblocked `agent-ready`
GitHub issue.
