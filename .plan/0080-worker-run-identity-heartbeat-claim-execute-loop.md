# Plan 0080: Worker Run CLI Identity Heartbeat and Claim-Execute Loop

## Status
Complete

## Goal
Add a foreground `worker run` CLI path that keeps one durable agent identity
alive, repeatedly claims eligible runs, and executes their queued steps in
order through the existing durable lifecycle, without inventing any new
persistence or dispatch semantics.

## Tasks
- [x] Add a `src/codex_agentic_os/worker.py` module with
      `register_or_resume_agent` (register a new agent id, or heartbeat an
      already-registered one) and `run_worker` (heartbeat cadence plus a
      claim-execute loop), both taking injectable `clock`/`sleeper`/
      `should_continue` for deterministic tests instead of relying on
      wall-clock signals.
- [x] Run selection: prefer a `queued` run already assigned to this agent id
      (from a prior `run create --agent-id` or a prior worker session);
      otherwise atomically claim the next unassigned eligible run via the
      existing `RunCoordinator.claim_next`.
- [x] Execute a claimed run's queued steps in order via the existing
      `RunCoordinator.execute_next_step`, stopping the inner loop once the
      run reaches a terminal status (mirrors the automatic run completion
      already implemented by `complete_step_from_result` /
      `complete_step_from_chat_response`).
- [x] Wire the worker's command-step dispatch through the same
      persisted-sandbox-policy-only resolver used by `run execute-next`
      (extracted as a shared `_persisted_sandbox_resolver` /
      `_provider_adapter_resolver` helper in `cli.py` to avoid duplicating
      the closures); the worker never supplies an ad hoc executor, so a
      command step without a persisted sandbox policy fails through the
      existing explicit error path.
- [x] Add the `codex-agentic-os worker run --agent-id --heartbeat-interval
      --poll-interval [--label] [--state-db]` CLI command, validating
      positive intervals before any registration/heartbeat mutation.
- [x] Add focused tests: CLI argument validation without mutation,
      register-or-resume identity behavior, heartbeat refresh cadence with
      an injected clock/sleeper, preferring an already-assigned run over
      claiming an unassigned one, claim-next selection, claim-execute-
      complete iteration for mixed command/provider steps, idle polling
      without busy-spinning, and end-to-end CLI wiring (including a real
      claim-execute-to-completion run driven through `main()`).
- [x] Run the full suite, rebuild/check the index, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #82, the sole `agent-ready` issue in
Sprint 12 "Autonomous worker loop" (the other three issues, #83-#85, are
blocked on this one).

Implementation complete. `run_worker`'s default `sleeper` resolves
`time.sleep` lazily inside the function body (rather than as a bound default
parameter) specifically so tests can monkeypatch `codex_agentic_os.worker.
time.sleep` to deterministically end an otherwise-infinite loop; a bound
default would have captured the real `time.sleep` at import time and been
unaffected by the monkeypatch. Approval-required and unresolved-context-
reference dispatch errors are intentionally left to propagate unchanged
(the worker does not catch them) — skip/idle policy for those cases is
explicitly issue #83's scope, not this one's.
