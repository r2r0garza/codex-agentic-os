# Plan 0022: Queued Step Cancellation

## Status
Complete

## Goal
Allow callers to remove one queued unit of work from an active durable run without
changing the parent run or sibling steps.

## Tasks
- [x] Add coordinator validation and persistence for cancelling exactly one queued step.
- [x] Preserve the parent run, sibling steps, and durable step positions.
- [x] Verify active-parent success cases and rejection without mutation.

## Resume Notes
Selected queue issue: #15. `RunCoordinator.cancel_step(step_id)` now cancels one queued
step only when its parent run is queued or running. The parent, siblings, and durable
positions remain unchanged, so later appends continue after the highest stored position.
Running, terminal, missing, malformed, and orphaned steps are rejected without mutation.
Resume with the next prioritized unblocked `agent-ready` GitHub issue.
