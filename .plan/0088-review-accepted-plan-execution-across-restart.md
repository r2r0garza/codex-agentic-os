# Plan 0088: Review Accepted Model Plan Execution Across Restart

## Status
Complete

## Goal
Prove the Sprint 13 operator flow end to end — objective to provider-proposed
plan, read-only review, explicit acceptance, worker execution, and durable
reconstruction across process restart — with automated, reproducible
coverage, closing the milestone's end-to-end exit criterion.

## Tasks
- [x] Select Sprint 13 issue #90 and inspect the existing plan proposal,
      inspection, and accept/reject coverage (Plans 0084-0087) for gaps
      against the full objective -> draft -> accept -> execute -> restart
      chain.
- [x] Add one CLI end-to-end test chaining `run create`, `run plan`,
      `run inspect-plan`, a pre-acceptance `run execute-next` proving no
      draft step is eligible, `run accept-plan`, two `run execute-next`
      dispatches through the unchanged sandbox/adapter paths, and post-restart
      reconstruction via fresh `RunCoordinator`/`StateStore` connections plus
      `run inspect-plan`/`run inspect`/`run history`.
- [x] Add a regression test covering the reject path: propose, reject,
      simulate a restart, and confirm the draft stays reconstructable while
      the run has no queued or executable steps.
- [x] Document the reproduction path in DEVELOPMENT.md, pointing at the two
      committed tests rather than duplicating a manual walkthrough.
- [x] Run the full suite, rebuild/check the index, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #90 (priority:3, sole `agent-ready` issue in
Sprint 13 "Operator-accepted model plan decomposition" at run start), the
milestone's last open issue.

All runtime/CLI primitives the issue needed already existed from Plans
0084-0087 (`run plan`, `run inspect-plan`, `run accept-plan`/`run
reject-plan`, `run execute-next`, `run inspect`, `run history`); this issue's
gap was purely end-to-end proof, not new product surface. Added
`test_cli_end_to_end_operator_review_reconstructs_plan_execution_after_restart`
in `tests/test_run_cli.py`, which drives the full CLI operator flow using the
existing `PLAN_PROPOSAL_CONTENT` fixture (one command step with a persisted
Docker sandbox policy, one provider step): create a run, propose a plan
through a fake offline adapter, inspect the draft read-only, call
`run execute-next` before any decision and assert `{"attempted": false}` with
an empty step list (no draft step can execute before explicit acceptance),
accept the plan with a registered deciding agent, execute both materialized
steps through the unchanged `ContainerSandbox`/adapter-resolver dispatch path
(no persisted-policy override, matching the worker's real path), then open
fresh `RunCoordinator`/`StateStore` connections and fresh CLI invocations to
reconstruct the objective, accepted draft (with `decision_agent_id`), both
executed steps, the terminal `succeeded` run status, and the full
`created -> plan_accepted -> run_started -> step_started -> step_succeeded ->
step_started -> step_succeeded -> run_succeeded` history sequence with
`plan_id`/`agent_id` provenance on the decision entry.

Added `test_cli_rejected_plan_remains_reconstructable_with_no_executable_steps_after_restart`
as the regression case Plan 0090's acceptance criteria required: propose,
reject with a registered agent, reconstruct via a fresh connection (`list_steps`
stays empty), confirm `run inspect-plan` shows `rejected` with its
`decision_agent_id` after the simulated restart, confirm `run execute-next`
still reports no attempt, and confirm `run history` reconstructs exactly
`created -> plan_rejected` with the decision's `plan_id`.

`run accept-plan --agent-id`/`run reject-plan --agent-id` require a
registered agent identity (`_require_registered_agent` in
`RunCoordinator._reviewable_plan_decision`), unlike `run claim`/`run add-step
--agent-id`, which accept any unchecked identifier per the existing
DEVELOPMENT.md note; both new tests register the deciding agent via
`run agent register` first. Steps materialized from an accepted plan carry a
persisted `sandbox_policy`, so `run execute-next` must omit `--sandbox`
entirely (per-invocation sandbox flags conflict with a persisted policy) —
the executor is swapped via `ContainerSandbox.execute` monkeypatching
instead, exactly as `RunCoordinator.add_step`-created steps with a persisted
policy already do elsewhere in the suite.

No runtime or CLI source changed — the milestone's product surface was
already complete; this issue closes its remaining "demonstrate end to end"
gap with committed, executable proof plus a documentation pointer.

Full suite, rebuilt/checked index, and `git diff --check` are recorded in the
run's verification evidence.
