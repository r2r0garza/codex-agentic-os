# Plan 0027: Run Inspect-step CLI

## Status
Complete

## Goal
Allow operators to inspect one durable step directly by its globally unique identifier without loading its parent run or sibling steps.

## Tasks
- [x] Add `run inspect-step STEP_ID` through the existing typed coordinator lookup.
- [x] Print the standard JSON-compatible step shape through a read-only state store.
- [x] Verify populated and terminal steps plus missing and malformed state without mutation.

## Resume Notes
Selected queue issue: #14. `codex-agentic-os run inspect-step` opens the existing state
database read-only, validates one step through `RunCoordinator.get_step()`, and prints
the same field and enum encoding used by run inspection. Resume with the next prioritized
unblocked `agent-ready` issue.
