# Plan 0091: Handle No-Eligible Capability Routing Failures

## Status
Complete

## Goal
Fail a capability-routed provider step definitively and cleanly when no
configured provider (under the effective routing policy) satisfies its
required capability at dispatch time, mirroring existing definite-failure
semantics, while proving fixed-provider steps continue to execute unchanged
— closing Sprint 14 issue #95.

## Tasks
- [x] Catch `ProviderRoutingPolicy.resolve`'s `ValueError` in
      `RunCoordinator.execute_next_step` (`src/codex_agentic_os/runtime.py`)
      instead of letting it propagate before the step ever dispatches.
- [x] Dispatch the step to `RUNNING` via the existing `start_next_step` path
      even when no route resolved (`provider_route=None`), then fail it
      through the existing `fail_step_from_error` helper — reusing the same
      transition and history-recording path adapter errors already use, so
      no new state-transition or history code was needed.
- [x] Preserve `required_capability` provenance on the `step_started` history
      entry from the step's own message rather than only from a resolved
      `ProviderRoute`, so a failed-routing dispatch still records which
      capability was requested (`resolved_provider`/`resolved_model`/
      `routing_reason` remain `null`, unchanged for fixed-provider steps).
- [x] Add focused runtime tests for no-eligible-provider definitive failure
      (explicit reason, `StepFailureKind.DEFINITE`, `retry_eligible is True`,
      run failure, restart reconstruction) and for a fixed-provider step on a
      separate run continuing to dispatch normally alongside a
      capability-routing failure.
- [x] Add a CLI-level regression test using `--provider-preference` to force
      a no-eligible-provider dispatch end to end through `run execute-next`.
- [x] Document the failure behavior in DEVELOPMENT.md's `run history` section.
- [x] Rebuild/check the index, run the full suite, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #95 (priority:3, sole open/`agent-ready`
issue in Sprint 14 "Capability-based provider routing"; unblocked once #94
closed).

The gap: `execute_next_step` called `routing_policy.resolve(...)` *before*
`start_next_step`, so a `ValueError` from resolution propagated out of
`execute_next_step` uncaught — the step stayed `QUEUED` forever (no failure
recorded, no explicit reason, and — for `worker.run_worker`, whose
`except (ApprovalRequiredError, ContextReferencesUnresolvedError)` clause
does not catch a bare `ValueError` — an unhandled exception that would have
crashed the poll loop). This is reachable in practice: a capability valid at
step-creation time (checked against `_KNOWN_PROVIDER_CAPABILITIES`, derived
from `DEFAULT_PROVIDER_SPECS`) can still have no eligible provider at
dispatch under a narrower operator-supplied `--provider-preference` policy,
since that policy restricts resolution to only the listed providers in the
listed order.

Fix: `execute_next_step` now catches the `ValueError` from `resolve()`,
still calls `start_next_step(run_id, provider_route=None)` to dispatch the
step to `RUNNING` (recording its requested `required_capability` with no
resolved route), and immediately calls `fail_step_from_error` with the
caught error — the exact path already used for adapter-call failures after
dispatch. `RunStep.failure_kind`/`retry_eligible` already classify any
failed provider-message step with a string `error`/`error_type` output as
`StepFailureKind.DEFINITE` with no new classification code required, so
"remains retry-eligible under existing definite-failure semantics" came for
free. `worker.run_worker` needed no change: `execute_next_step` now returns
a terminal `(step, run)` tuple instead of raising, which the existing loop
already handles as an ordinary terminal outcome.

The only other production change: `start_next_step` and `transition_step`
previously derived the `step_started` history entry's `required_capability`
solely from a resolved `ProviderRoute` (`None` when `provider_route is
None`). Changed both to read it from the step's own `message
.required_capability` instead, so a failed-routing dispatch still records
the capability it attempted — regressions checked: fixed-provider steps
still show `required_capability: null` (their message truly has none), and
successful capability routes are unchanged (`message.required_capability`
always equals `provider_route.required_capability` when a route resolves).

Full suite `607 passed` (up from 604, +3 new: one runtime no-eligible-
provider test covering explicit reason/definite-failure/retry-eligibility/
restart reconstruction, one runtime regression test proving a fixed-provider
step on a separate run is unaffected, one CLI end-to-end test forcing the
no-eligible-provider path via `--provider-preference`). Rebuilt index (923
symbols, 5316 relationships, was stale after the source changes) and
confirmed current. `git diff --check` clean.
