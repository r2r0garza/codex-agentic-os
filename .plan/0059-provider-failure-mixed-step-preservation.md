# Plan 0059: Provider Failure and Mixed-Step Preservation

## Status
Complete

## Goal
Complete provider-message steps as durably failed on adapter resolution or transport
errors, without orphaning the running claim, while leaving command-step sandbox
execution semantics unchanged.

## Tasks
- [x] Move adapter resolution and request construction after the step's running
      transition so any exception occurs while the claim is inspectable.
- [x] Add `RunCoordinator.fail_step_from_error()` to persist a failed step and run
      from a caught adapter exception, mirroring `complete_step_from_result()`.
- [x] Catch `ValueError`, `RuntimeError`, and `NotImplementedError` around adapter
      resolution and `adapter.complete()` in `execute_next_step()`; leave command-step
      executor exceptions unhandled, preserving the existing uncertain-outcome/recovery
      contract for sandbox execution.
- [x] Verify focused runtime/CLI provider-failure and mixed command/model execution
      tests, the full suite, index rebuild, and `git diff --check`.

## Resume Notes
Closed queue issue: #53. `execute_next_step()` now starts the step running before
resolving the adapter or sending the request, and routes `ValueError`/`RuntimeError`/
`NotImplementedError` from either into `fail_step_from_error()`, which durably fails the
step (`{"error": ..., "error_type": ...}`) and cascades the run to failed exactly like a
nonzero command result. Command-step executor exceptions are untouched: they still leave
the step and run running for explicit `recover_running_step()`, since a sandbox
subprocess's side effects may be unknown. Added runtime tests for transport failure,
resolver failure, and ordered mixed command/model execution across three steps, plus a
CLI test proving the operator-facing `execute-next` payload reports a failed step/run
without exit code 2. Full suite (353 passed), index rebuild (20 files, 513 symbols, 2798
relationships), and `git diff --check` all pass.
