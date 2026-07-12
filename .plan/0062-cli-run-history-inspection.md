# Plan 0062: CLI Run History Inspection

## Status
Complete

## Goal
Give an operator a read-only `run history` CLI command that presents one run's
durable lifecycle history in stable sequence order, reusing the history read
contract from Plans 0060/0061 without any new persistence.

## Tasks
- [x] Add a read-only `run history <run_id>` subcommand to the CLI parser.
- [x] Reject a missing run explicitly without creating a database or mutating state.
- [x] Emit JSON history entries (run/step id, sequence, transition, status, agent,
      execution kind) in durable sequence order.
- [x] Add focused CLI tests plus an end-to-end mixed command/provider reconstruction
      across separate process (fresh `RunCoordinator`/`StateStore`) invocations.
- [x] Run the full suite, refresh/check the index, and run `git diff --check`.

## Resume Notes
Selected queue issue #57. `RunCoordinator.list_history()` (Plan 0060) already
validates run existence and raises `KeyError`, and `StateStore.list_run_history()`
already returns entries ordered by sequence with only non-sensitive fields, so this
command is a thin read-only CLI wrapper matching the existing `run inspect`/`run
list` read-only pattern. The CLI checks `coordinator.get(run_id)` explicitly before
calling `list_history()` so a missing run raises the standard CLI `ValueError`
(`run does not exist: ...`, exit code 2) rather than the coordinator's `KeyError`.

Verification: 5 new `run history` CLI tests (stable order, mixed command/provider
reconstruction across separate `main()` invocations against the same database file,
missing-run rejection, missing-database rejection, no-mutation) plus the full suite
(366 passed, up from 361) all pass; index rebuilt/current; `git diff --check` clean.
Live CLI UAT against a real repository-local SQLite database confirmed stable JSON
history output and explicit missing-run/missing-database rejection without writes.
