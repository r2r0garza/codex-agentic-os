# Plan 0028: Fix Execute-next Invocation

## Status
Complete

## Goal
Keep the operator `run execute-next` command aligned with the established coordinator
execution boundary by passing exactly one container sandbox executor.

## Tasks
- [x] Verify the CLI passes one executor to `RunCoordinator.execute_next_step()`.
- [x] Add an explicit regression assertion at the coordinator boundary.
- [x] Verify focused CLI behavior, the full suite, and repository-index freshness.

## Resume Notes
Selected queue issue: #20. The reported duplicate executor argument was not present in
the committed source: the CLI passes the run identifier and one `ContainerSandbox`.
The execute-next regression test now observes the real coordinator boundary explicitly,
asserting one invocation with one container executor while continuing to cover durable
success and failure results. Resume with the next prioritized unblocked `agent-ready`
GitHub issue.
