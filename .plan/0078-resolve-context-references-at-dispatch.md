# Plan 0078: Resolve Provider Context References at Dispatch Eligibility

## Status
Complete

## Goal
Teach dispatch to evaluate a provider-message step's declared context
references against current durable step state immediately before execution,
keeping a step with any unresolved reference queued and ineligible without
mutating it, and record durable, redacted history evidence when resolution
lets dispatch proceed.

## Tasks
- [x] Add a `ContextReferencesUnresolvedError` raised from `start_next_step`
      (and inherited by `execute_next_step`) when any declared context
      reference has not succeeded, mirroring the approval gate's
      queued-but-ineligible semantics without mutating run or step state.
- [x] Resolve references from the current durable step records at dispatch
      time (no cached copy from step declaration).
- [x] Persist a `context_step_ids` column on `run_history` and thread it
      through `StateStore.transition_step`/`put_many` so a `step_started`
      entry for a context-referencing step records the resolved reference
      ids (ids only, no referenced output).
- [x] Preserve existing approval-gated and command-step dispatch behavior.
- [x] Add focused runtime, state, and CLI tests: successful dispatch with
      auditable history, cancelled/failed unresolved references without
      mutation, dispatch-time freshness, approval composition, and a CLI
      deterministic error report without mutation.
- [x] Run the full suite, rebuild/check the index, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #79. Native multi-message provider payload
mapping of resolved context remains out of scope for #80.

Implementation complete. `start_next_step` checks declared context
references against fresh step status before any mutation; unresolved
references raise `ContextReferencesUnresolvedError` (a `ValueError`
subclass), which the CLI's existing generic error handling reports
deterministically (exit code 2) without corrupting state. Resolved
`step_started` history entries carry `context_step_ids` in declared order.
Focused runtime/state/CLI tests, the full suite, a fresh-process CLI/SQLite
UAT (both the ineligible-cancelled-reference and eligible-then-dispatched
paths), index rebuild and check, and `git diff --check` all pass.
