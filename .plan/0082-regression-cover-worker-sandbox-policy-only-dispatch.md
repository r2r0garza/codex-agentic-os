# Plan 0082: Regression-Cover Worker Sandbox-Policy-Only Dispatch

## Status
Complete

## Goal
Confirm and lock in, with focused regression tests, that `worker run` command
dispatch executes exclusively through a command step's persisted
`sandbox_policy` with no ad hoc sandbox flags supplied by the worker itself —
closing issue #84 without changing runtime behavior, since the contract was
already established by #82's `_persisted_sandbox_resolver` wiring and #83's
skip/idle handling.

## Tasks
- [x] Verify (read-only) that `worker run`'s argparse parser exposes no
      per-invocation sandbox, image, mount, env, workdir, or network override
      flags, and that `cli.py`'s `worker` dispatch passes only
      `sandbox_resolver=_persisted_sandbox_resolver()` (never an `executor`)
      into `run_worker`.
- [x] Verify (read-only) that `execute_next_step` already fails a command
      step with no persisted `sandbox_policy` through its existing explicit
      `ValueError` path (`next command step requires a sandbox: …`) rather
      than accepting any worker-supplied fallback, and that this propagates
      through `cli.py`'s blanket `(ValueError, RuntimeError)` handler into a
      deterministic `SystemExit(2)`.
- [x] Add `tests/test_worker.py::test_run_worker_command_step_without_persisted_policy_fails_without_ad_hoc_executor`
      proving a step with no persisted policy raises without ever invoking
      the injected `sandbox_resolver`, and leaves the step `QUEUED`.
- [x] Add to `tests/test_worker_cli.py`: a `--help` regression asserting no
      sandbox override flags; a real-CLI-path test for a missing persisted
      policy; a real-CLI-path test for a missing persisted `env_passthrough`
      variable; and a real-CLI-path test proving a present `env_passthrough`
      variable is resolved by name only (captured via a patched
      `ContainerSandbox.execute`) and never appears in durable `run inspect`
      output.
- [x] Run the full suite, rebuild/check the committed index, and run
      `git diff --check`.

## Resume Notes
Selected active-milestone issue: #84 (priority:2, the sole `agent-ready`
issue in Sprint 12 after #83's closure). #85 remains correctly `blocked` on
#84.

No source in `src/codex_agentic_os/` changed — `worker.py` and `cli.py`
already satisfied every acceptance criterion in #84 as a side effect of
#82's `_persisted_sandbox_resolver` extraction and #83's dispatch-error
propagation. This run's scope was entirely regression-test coverage proving
those properties hold, per the issue's own triage comment. `env_passthrough`
resolution and the `ValueError` fallback-rejection path were previously
covered only for `run execute-next` in `tests/test_run_cli.py`; the new
`worker run`-path tests close that gap since the two commands share
`_persisted_sandbox_resolver` but dispatch through different CLI branches.
