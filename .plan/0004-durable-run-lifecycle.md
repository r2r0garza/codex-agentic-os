# Plan 0004: Durable Run Lifecycle

## Status
Complete

## Goal
Turn the runtime declarations and durable state store into a small provider-neutral execution core with explicit, resumable run state.

## Tasks

- [x] Add a typed run lifecycle coordinator with validated transitions and durable revision tracking.
- [x] Define durable step records for ordered units of work within a run.
- [x] Connect sandbox execution results to run and step completion without coupling the runtime to Docker or Podman.
- [x] Expose read-only run inspection through the CLI.

## Resume Notes

The plan is complete. `codex-agentic-os run inspect <run-id>` opens the configured SQLite state database in read-only mode and emits the typed run plus its steps in durable position order as JSON. Missing databases and runs fail without creating or modifying state. Resume by creating a new focused plan for the next execution-core capability rather than extending the completed lifecycle plan implicitly.
