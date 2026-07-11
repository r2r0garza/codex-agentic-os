# Plan 0004: Durable Run Lifecycle

## Status
Active

## Goal
Turn the runtime declarations and durable state store into a small provider-neutral execution core with explicit, resumable run state.

## Tasks

- [x] Add a typed run lifecycle coordinator with validated transitions and durable revision tracking.
- [ ] Define durable step records for ordered units of work within a run.
- [ ] Connect sandbox execution results to run and step completion without coupling the runtime to Docker or Podman.
- [ ] Expose read-only run inspection through the CLI.

## Resume Notes

The first task is complete. `RunCoordinator` creates queued runs and permits only queued → running/cancelled and running → succeeded/failed/cancelled transitions. Records remain provider-neutral and persist through `StateStore`, with terminal output allowed only for succeeded or failed runs. Resume with durable ordered step records; do not connect execution adapters until their lifecycle contract is explicit.
