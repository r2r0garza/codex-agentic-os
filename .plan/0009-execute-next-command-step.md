# Plan 0009: Execute Next Command Step

## Status
Complete

## Goal
Execute the next durable command step through an injected sandbox boundary while
preserving the existing dispatch and result-recording lifecycle.

## Tasks

- [x] Add a coordinator operation that executes and records the next queued command step.

## Verification

- Pass the durable command and timeout to an injected executor.
- Record successful and nonzero results through the existing completion boundary.
- Reject coordination-only steps without changing durable state.
- Preserve running state when execution raises before returning a result.

## Resume Notes

The plan is complete. `RunCoordinator.execute_next_step()` validates that the earliest
queued step has a command, starts it through the existing ordered dispatcher, invokes
an injected `SandboxExecutor`, and records its result. Executor exceptions intentionally
leave the run and step running so a later recovery capability can reconcile uncertain
execution instead of fabricating a terminal result. Resume by creating a focused plan
for explicit recovery of interrupted or timed-out running steps.
