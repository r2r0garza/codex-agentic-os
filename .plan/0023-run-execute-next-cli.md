# Plan 0023: Run Execute-next CLI

## Status
Complete

## Goal
Execute exactly one queued durable command step through an explicitly selected container sandbox from the operator CLI.

## Tasks
- [x] Add `run execute-next` with required Docker or Podman selection and an optional image override.
- [x] Delegate dispatch, execution, and durable result recording to `RunCoordinator.execute_next_step()`.
- [x] Report an empty queue without mutation through a machine-readable execution marker.
- [x] Verify conservative sandbox defaults, single-step execution, results, and recoverable failures.

## Resume Notes
Selected queue issue: #5. `codex-agentic-os run execute-next RUN_ID` attempts at most
one queued command through Docker or Podman, retains the established sandbox defaults,
and supports an optional image override. An empty queue returns the unchanged standard
run payload plus `execution.attempted: false`; executor exceptions intentionally leave
the run and step running for explicit recovery. Resume with the next prioritized
unblocked `agent-ready` GitHub issue.
