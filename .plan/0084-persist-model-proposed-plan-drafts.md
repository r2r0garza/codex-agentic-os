# Plan 0084: Persist Model-Proposed Plan Drafts for Operator Acceptance

## Status
Complete

## Goal
Add a bounded `run plan` CLI slice that dispatches an existing run's
objective through a configured provider adapter and durably persists the
proposed ordered steps as a plan draft attached to the run, without queuing
any executable steps. A malformed or unparseable proposal must fail
explicitly, preserve the raw provider evidence, and leave the run's step
queue unchanged.

## Tasks
- [x] Add `PlanStepProposal`/`PlanDraft` typed views and `PlanProposalError`
      to `runtime.py`, and reuse the existing `plan` `StateStore` kind
      (already reserved, previously unused) rather than adding new schema.
- [x] Add `RunCoordinator.propose_plan`: builds a `ProviderMessage` from the
      run's objective (or an explicit override) and a fixed planning system
      prompt, dispatches it through the existing `ChatAdapterResolver`
      boundary, validates the minimal accepted shape
      (`{"steps": [{"objective": str, "execution_kind": "command"|"provider"}, ...]}`),
      and persists a `draft` plan record on success or an `invalid` plan
      record (with raw evidence) plus a raised `PlanProposalError` on
      malformed output. Rejects a missing/terminal run and a duplicate plan
      id before dispatching, so no network call is made when the request is
      already invalid.
- [x] Add `codex-agentic-os run plan <run_id> <plan_id> --provider ... [--model]
      [--objective] [--temperature] [--max-tokens]`, reusing
      `_provider_adapter_resolver()` exactly like `run add-step` and
      `run execute-next`. Prints the persisted draft (or lets the blanket
      `ValueError`/`RuntimeError` handler surface a malformed-proposal
      failure that names the recorded plan id).
- [x] Add focused `RunCoordinator.propose_plan` tests: successful draft
      persistence with ordered steps and evidence, objective
      default/override, every malformed-shape case (non-JSON, non-object,
      missing/empty `steps`, non-object step, missing/blank objective,
      invalid `execution_kind`) preserving raw evidence and queuing no
      steps, duplicate plan id rejected without a second dispatch, and
      missing/terminal run rejected without dispatching.
- [x] Add focused CLI tests against an injected/offline adapter
      (`codex_agentic_os.cli.adapter_for` monkeypatched, following the
      existing `run execute-next` provider-message test pattern): draft
      creation and JSON payload shape, objective default vs. override,
      malformed-proposal failure surfaced with the recorded plan id and
      preserved evidence, and missing-run rejection before any adapter
      resolution.
- [x] Document `run plan` in DEVELOPMENT.md next to the other run-step
      commands.
- [x] Rebuild/check the index, run the full suite, and run `git diff
      --check`.

## Resume Notes
Selected active-milestone issue: #87 (priority:1), the sole `agent-ready`
issue in Sprint 13 "Operator-accepted model plan decomposition" at run
start (#88/#89/#90 are `blocked` on it).

The `plan` `StateStore` kind already existed in the schema
(`state.py`'s `KINDS`) but was otherwise unused anywhere in the codebase —
it reads as though it was reserved early for exactly this feature. Reusing
it (keyed by an operator-supplied `plan_id`, mirroring how `step_id`/`run_id`
are already caller-provided) avoided any `state.py` schema change. It also
sets up `Sprint 13`'s next issue (#89, CAS-safe accept/reject) for free:
`StateStore.put_many`'s existing generic `expected=[(kind, key, status,
revision), ...]` compare-and-swap already works for any kind, including
`plan`, so materializing a draft into queued `step` records and transitioning
the draft to `accepted`/`rejected` can reuse it directly without new
persistence primitives.

Scope was kept to exactly #87: draft creation and durable persistence only.
No read-only inspection CLI (#88), no accept/reject (#89), and no
plan-editing surface — those are explicitly out of scope per the issue and
the milestone's scope boundary.
