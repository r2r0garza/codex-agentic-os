# Plan 0087: Specify Executable Plan Draft Materialization Contract

## Status
Complete

## Goal
Extend the Sprint 13 plan proposal/draft contract so each proposed step
carries a concrete executable payload compatible with the existing
queued-step validation contract, and a deterministic, collision-free
materialized step identity, closing the specification gap Plan 0086 (issue
#89) hit.

## Tasks
- [x] Extend `PlanStepProposal` with `step_id`, `command`, `timeout`,
      `sandbox_policy`, and `message` fields mirroring `RunStep`'s
      executable-input shape.
- [x] Update `PLAN_PROPOSAL_SYSTEM_PROMPT` to require a command step's
      `command`/`sandbox_policy` (and forbid `message`) or a provider step's
      `message` (and forbid `command`/`timeout`/`sandbox_policy`).
- [x] Update `_parse_plan_proposal` to accept `plan_id`, validate each
      step's executable payload by reusing `_validate_command`,
      `_validate_sandbox_policy`, and `_validate_message` (the exact rules
      `add_step` already enforces), and materialize `step_id` as
      `f"{plan_id}-step-{index + 1}"` — deterministic, never model-supplied.
- [x] Persist the executable payload via `_plan_step_proposal_payload` and
      reconstruct it on read via `_plan_step_proposal`, reusing the same
      validators `_step()` already uses for stored `RunStep` records.
- [x] Update the CLI's `_plan_draft_payload`/`_plan_step_proposal_payload`
      to print the new fields, following `_step_payload`'s exact
      field-omission convention (drop `message` when absent, drop
      `sandbox_policy` when absent and normalize its `kind` to `.value`).
- [x] Update existing `run plan`/`run inspect-plan`/`propose_plan`/`get_plan`
      tests for the new required shape; add malformed-payload cases (missing
      command, missing sandbox policy, missing message, mismatched fields
      per execution kind) and a step-id determinism/collision-freedom test
      and an after-restart reconstruction test.
- [x] Update DEVELOPMENT.md's `run plan`/`run inspect-plan` sections.

## Resume Notes
Selected active-milestone issue: #91 (priority:1, sole `agent-ready` issue),
filed directly by the operator to unblock #89 after Plan 0086 recorded the
contract gap.

**Executable payload.** Each proposed step now carries exactly the input
`add_step` requires: a command step has `command` (non-empty argv) and a
persisted `sandbox_policy` (required, not optional — matching the worker's
real dispatch path, which requires a persisted policy for command steps and
exposes no override flag); a provider step has a complete `message`. Reusing
`_validate_command`/`_validate_sandbox_policy`/`_validate_message` directly
(rather than duplicating their rules) guarantees a persisted draft step is
always acceptance-ready without a future accept implementation needing to
invent or guess anything.

**Step identity.** `step_id` is materialized as `f"{plan_id}-step-{index +
1}"`, never proposed by the model. Because `plan_id` is already unique
(`propose_plan` rejects a duplicate before dispatching) and the position
index is unique within one draft, every materialized id is collision-free
within its draft by construction — satisfying the scope's "collision-free
materialized step identities" clause directly, without needing the
"or an equivalent deterministic identity rule" fallback. Collision-checking
a materialized id against already-existing `step` records (e.g. from manual
`add_step` calls or another accepted plan) is necessarily an
acceptance-time concern and remains #89's job, which this issue explicitly
excludes implementing.

**Plan-decision history identity.** The scope also asked for a durable
identity so a future accept/reject decision can cite its plan and be
reconstructed across multiple drafts. `plan_id` already satisfies this:
`propose_plan` rejects a duplicate plan id before dispatching (so it is
unique), and `get_plan(plan_id)` deterministically retrieves exactly one
draft even when a run has several. No new schema (e.g. a `run_history`
`plan_id` column with no writer yet) was added for this — #89, when it
implements accept/reject, can cite `plan_id` directly in whatever
`decision`-kind record or history entry it introduces, using the identity
this issue already guarantees. Adding an unused column ahead of a concrete
writer would be speculative schema the project's change-discipline guidance
explicitly discourages.

**Not implemented (explicitly out of scope, matching the issue).** Accept/
reject itself, plan editing, automatic/partial acceptance, iterative
re-planning, conditional/branching plans. `RunCoordinator.add_step`, manual
step creation, provider-message execution, sandbox dispatch, approval,
eligibility, and history behavior for runs without plan drafts are
unchanged — the full pre-existing suite (555 tests) still passes unmodified
in behavior, plus 10 new/updated plan-focused tests.

Issue #89 remains open and should now be re-evaluated: its blocker
(Plan 0086) is resolved by this contract, so it can lose `blocked` and
regain `agent-ready` once verified against this issue's shape.
