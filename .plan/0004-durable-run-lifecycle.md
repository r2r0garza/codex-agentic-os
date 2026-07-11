# Plan 0004: Durable Run Lifecycle

## Status
Active

## Goal
Turn the runtime declarations and durable state store into a small provider-neutral execution core with explicit, resumable run state.

## Tasks

- [x] Add a typed run lifecycle coordinator with validated transitions and durable revision tracking.
- [x] Define durable step records for ordered units of work within a run.
- [x] Connect sandbox execution results to run and step completion without coupling the runtime to Docker or Podman.
- [ ] Expose read-only run inspection through the CLI.

## Resume Notes

The first three tasks are complete. `RunCoordinator.complete_step_from_result` accepts a structural, backend-neutral execution result, persists its command, exit code, stdout, and stderr on the running step, and maps zero/nonzero exits to succeeded/failed lifecycle states. A failure terminates the run with a compact failure summary; the last successful step succeeds the run. Resume by exposing read-only run and ordered-step inspection through the CLI. Keep inspection non-mutating and use the existing typed coordinator views.
