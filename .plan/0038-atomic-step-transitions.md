# Plan 0038: Atomic Durable Step Transitions

## Status
Complete

## Goal
Prevent competing coordinators from overwriting a newer durable step lifecycle state
during an explicit `RunCoordinator.transition_step()` call.

## Tasks
- [x] Add a SQLite compare-and-transition operation for one step.
- [x] Route explicit coordinator step transitions through the atomic store boundary.
- [x] Verify lifecycle, output, revision, conflict, and family-preservation behavior.

## Resume Notes
Selected queue issue: #29. `RunCoordinator.transition_step()` now persists through
`StateStore.transition_step()`, which compares the expected status and revision inside
one immediate write transaction. Competing transitions cannot overwrite one another;
the parent run and sibling steps remain unchanged. Sandbox completion, recovery,
cancellation, and implicit run transitions remain outside this focused change. Resume
with the next prioritized unblocked `agent-ready` GitHub issue.
