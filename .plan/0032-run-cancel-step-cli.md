# Plan 0032: Run Cancel-step CLI

## Status
Complete

## Goal
Allow operators to cancel one queued durable step without changing its active parent run or sibling steps.

## Tasks
- [x] Add `run cancel-step STEP_ID` through the existing coordinator operation.
- [x] Print the standard parent run payload with steps in durable position order.
- [x] Verify queued and running parents plus rejection without mutation.

## Resume Notes
Selected queue issue: #18. `codex-agentic-os run cancel-step` delegates lifecycle and
record validation to `RunCoordinator.cancel_step()` and prints the unchanged parent run
with all steps in durable position order. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.
