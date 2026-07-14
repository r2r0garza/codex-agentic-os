# Plan 0083: Worker Shutdown and Mixed-Run Operator Review

## Status
Complete

## Goal
Handle SIGINT/SIGTERM cleanly in the foreground `worker run` CLI so the
process exits without a traceback or a corrupted state database, and add
end-to-end operator evidence that a mixed command/provider run can be
delegated to the worker and reconstructed from fresh CLI reads without a
manual `run execute-next`.

## Tasks
- [x] Add `_install_worker_shutdown_signals` in `cli.py`, installing SIGINT
      and SIGTERM handlers that set a flag instead of raising, and return a
      `should_continue` callable plus a `restore` callable.
- [x] Wire the `worker` CLI dispatch to pass `should_continue` into
      `run_worker` and restore the prior signal disposition in a `finally`
      block regardless of outcome.
- [x] Confirm (by design, not new code) that a step already executing when a
      signal arrives still runs to completion and is recorded durably before
      the worker's existing between-step `should_continue` check stops the
      loop — no forced mid-call interruption is introduced.
- [x] Confirm (by design, not new code) that a step left `running` by a hard
      process kill the worker cannot catch (`SIGKILL`) is already rejected
      by `execute_next_step`'s existing "run already has a running step"
      guard on the next dispatch attempt, so it is never silently completed
      or duplicated; it remains reachable through the existing `run recover`
      and `run reassign-claim` commands.
- [x] Add focused regression tests: real SIGINT and SIGTERM delivered to the
      running test process during `worker run` exit cleanly with the normal
      JSON summary and no exception; a step left `running` by a simulated
      hard kill is rejected (not duplicated or silently completed) on a
      later `worker run` invocation, and `run recover` reconciles it
      afterward; an end-to-end mixed command/provider run delegated
      entirely to `worker run` (no manual `run execute-next`) is
      reconstructable from separate `run inspect`/`run history` calls.
- [x] Update the `worker run` DEVELOPMENT.md section: drop the "does not yet
      handle interruption cleanly" caveat and describe the shutdown and
      recovery contract.
- [x] Run the full suite, rebuild/check the index, and run `git diff
      --check`.

## Resume Notes
Selected active-milestone issue: #85 (priority:3), the sole `agent-ready`
issue in Sprint 12 after #82/#83/#84's closure.

`run_worker` already accepted an injectable `should_continue` callable
(added in Plan 0080 for deterministic tests), and its inner/outer loops
already only check it between steps — never mid-dispatch — so no change to
`worker.py` was needed. The entire fix is confined to `cli.py`'s `worker`
dispatch: install real OS signal handlers that flip the same kind of flag
the tests already use, instead of letting Python's default SIGINT
disposition raise `KeyboardInterrupt` (which would otherwise propagate past
`main`'s blanket `except (ValueError, RuntimeError)` and print a raw
traceback) or SIGTERM's default disposition (which terminates the process
outright with no summary at all).

The "genuinely in-flight uncertain step" acceptance criterion required no
new runtime behavior either: `execute_next_step`'s pre-existing
`if any(step.status is StepStatus.RUNNING ...)` guard already raises before
touching a leftover running step, and `run recover` /
`run reassign-claim` (Sprints 8-9) already exist to reconcile it. This
plan's scope was wiring the signal handlers plus regression coverage
proving these existing contracts hold together as one clean shutdown story.
