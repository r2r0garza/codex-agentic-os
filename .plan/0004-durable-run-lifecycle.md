# Plan 0004: Durable Run Lifecycle

## Status
Active

## Goal
Turn the runtime declarations and durable state store into a small provider-neutral execution core with explicit, resumable run state.

## Tasks

- [x] Add a typed run lifecycle coordinator with validated transitions and durable revision tracking.
- [x] Define durable step records for ordered units of work within a run.
- [ ] Connect sandbox execution results to run and step completion without coupling the runtime to Docker or Podman.
- [ ] Expose read-only run inspection through the CLI.

## Resume Notes

The first two tasks are complete. `RunCoordinator` now appends durable, position-ordered steps to non-terminal runs and validates the same queued → running/cancelled and running → succeeded/failed/cancelled lifecycle shape used by runs. Step revisions and optional succeeded/failed output persist through `StateStore`; existing databases are upgraded to accept the new `step` record kind without losing records. Resume by connecting sandbox results to run and step completion through the provider-neutral coordinator, without embedding Docker or Podman types in the lifecycle records.
