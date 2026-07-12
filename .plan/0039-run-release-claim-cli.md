# Plan 0039: Run Claim Release CLI

## Status
Complete

## Goal
Let an operator release an exact queued run claim through the existing atomic
coordinator boundary.

## Tasks
- [x] Add `run release RUN_ID --agent-id AGENT_ID`.
- [x] Print the standard run-with-ordered-steps payload after release.
- [x] Verify success and every rejection path without state mutation.

## Resume Notes
Selected queue issue: #30. The command delegates ownership and queued-state checks to
`RunCoordinator.release_claim()`, preserving its atomic store operation. Bulk release,
forced ownership changes, running-run release, and automatic reassignment remain out of
scope. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
