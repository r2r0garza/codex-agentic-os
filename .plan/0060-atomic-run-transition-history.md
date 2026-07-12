# Plan 0060: Atomic Run Transition History

## Status
Complete

## Goal
Give an operator a durable, ordered history of a run's creation, claim, claim
release, and explicit lifecycle transitions, each identifying the run, transition,
resulting status, responsible agent when known, and execution kind when applicable,
without exposing run payload values (objective, output, credentials).

## Tasks
- [x] Add a `run_history` SQLite table (`run_id`, `sequence`, `transition`, `status`,
      `agent_id`, `execution_kind`) created alongside `state_records` in
      `StateStore.initialize()`.
- [x] Add `RunHistoryEntry` typed record and `StateStore.list_run_history()`.
- [x] Append one `created` entry inside `StateStore.insert()` for `kind == "run"`.
- [x] Append one `claimed` entry inside `StateStore.claim_run()` and
      `StateStore.claim_next_run()`.
- [x] Append one `claim_released` entry inside `StateStore.release_run_claim()`.
- [x] Append one `transitioned` entry inside `StateStore.transition_run()`, threading
      an optional `execution_kind` through `RunCoordinator.transition()`; pass
      `execution_kind="provider_message"` from `complete_step_from_chat_response()`.
- [x] Add `RunCoordinator.list_history()` as the typed runtime read contract.
- [x] Add focused `StateStore`/`RunCoordinator` tests for persistence, ordering,
      restart, and no-phantom-entry contention behavior.
- [x] Run full `pytest`, rebuild the index, and `git diff --check`.

## Resume Notes
Closes queue issue #55. Every history append happens inside the same
`BEGIN IMMEDIATE` transaction as the state mutation it records, after all
compare-and-swap and validation checks pass and before `commit()`, so a rejected or
losing attempt appends nothing. `claim_next_run()` records the same `claimed`
transition as `claim_run()` since both represent a run becoming claimed, just through
different run selection. Implicit multi-record mutations (`cancel()`,
`complete_step_from_result()`, `fail_step_from_error()`, `recover_running_step()`,
`start_next_step()`) are unchanged and out of scope per the issue.

Verification: focused `test_state.py`/`test_runtime.py` history tests (ordering,
per-run isolation, restart reconstruction, losing-claim/stale-transition no-phantom
behavior, concurrent-claim contention, `execution_kind` threading) all pass; full
suite 360 passed (up from 353); incremental index rebuild (20 files, 527 symbols, 2887
relationships) and `codex-agentic-os index check` both current; `git diff --check`
clean. Live CLI UAT (`run create` → `agent register` → `run claim` → `run
transition`) against a real SQLite database, followed by reading
`StateStore.list_run_history()` from a fresh process, confirmed durable ordered
entries with the expected transition/status/agent fields.
