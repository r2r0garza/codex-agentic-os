# Plan 0031: Claim-next Worker CLI

## Status
Complete

## Goal
Let a worker atomically claim the next eligible queued run without knowing its identifier in advance.

## Tasks
- [x] Add `run claim-next --agent-id AGENT_ID` through the existing atomic `RunCoordinator.claim_next` operation.
- [x] Print the standard run-and-ordered-steps payload when a run is claimed, or `claim.attempted: false` when none is eligible.
- [x] Verify successful selection, no-work output, validation, ordered steps, and durable read-back.

## Resume Notes
Selected queue issue: #22. `codex-agentic-os run claim-next` delegates eligible-run
selection and assignment to `RunCoordinator.claim_next()`, printing the standard ordered
run payload on success or `{"claim": {"attempted": false}}` when no queued, unassigned
run exists. Empty agent identifiers and missing state databases fail before any mutation.
Resume with the next prioritized unblocked `agent-ready` GitHub issue.
