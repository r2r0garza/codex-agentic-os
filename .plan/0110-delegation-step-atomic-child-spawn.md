# Plan 0110: Delegation Step Declaration and Atomic Child-Run Spawn

## Status
Complete

## Goal
Add a durable child-run delegation step declaration and dispatch path that
atomically creates one linked child run when the step is executed, leaving
the child claimable and executable through the entirely unchanged existing
run lifecycle.

## Tasks
- [x] Add a `DelegationSpec` (child objective, optional target agent) as a
      third mutually exclusive step execution input alongside command and
      provider message, with declaration-time validation.
- [x] Add `RunCoordinator.add_step(..., delegation=...)` and persist the
      declaration in the step's durable payload.
- [x] Add `StateStore.dispatch_delegation_step`, a single atomic transaction
      that CAS-checks the queued step, inserts the new linked child run
      (`{step_id}-child`), transitions the parent step to `running` with the
      child run id recorded, and transitions the parent run `queued ->
      running` only when it was still queued.
- [x] Wire dispatch through `RunCoordinator.execute_next_step`: a queued
      delegation step is dispatched via the new atomic path instead of the
      command/provider executor branches; a running delegation step makes a
      second dispatch attempt raise `DelegationPendingError` instead of the
      generic "already running" conflict. `start_next_step` now rejects
      delegation steps outright, since its two-phase dispatch/complete model
      does not fit an operation that must spawn and link atomically.
- [x] Add `AgentRun.parent_run_id`/`parent_step_id` so a spawned child run
      durably identifies its parent from either side, and thread a shared
      `_base_run_payload` helper through every run-payload rewrite site so
      that linkage (and existing `agent_id`) survive the child run's own
      lifecycle transitions.
- [x] Update `worker.py` to treat `DelegationPendingError` like the existing
      approval/context-reference blocked-run signals, so a parked delegation
      step never crashes the poll loop.
- [x] Add CLI `run add-step --delegate-objective/--delegate-target-agent`
      and an `execute-next` branch that dispatches a delegation step without
      sandbox/adapter arguments.
- [x] Cover declaration validation, atomic spawn/link, optional target-agent
      assignment, competing-dispatch CAS safety, worker-loop compatibility,
      and CLI create/dispatch/inspect flows with focused tests.

## Resume Notes
Selected active-milestone issue: #121 (Sprint 20 "Parent-child run
delegation", priority:1, `agent-ready`, no stated dependency). Sprint 20 was
replenished with #121-#124 in a prior run whose `MEMORY.md` record was never
committed; treated GitHub as authoritative. #122 (parent completion from
child outcome), #123 (interruption recovery), and #124 (inspection/
validation/two-agent review) remain correctly `blocked` on #121 and are
explicitly out of this issue's scope, matching its stated exclusions
("parent terminal-outcome propagation beyond leaving the parent step waiting
on the child," "interruption-recovery hardening beyond normal atomic
transaction coverage," "arbitrary-depth delegation trees").

The child run id is deterministic (`{step_id}-child`) rather than
operator-supplied, mirroring how plan-proposal step ids are derived
deterministically (`{plan_id}-step-{n}`) elsewhere in this module — it keeps
dispatch idempotent-by-construction (a second dispatch of the same step
targets the same child id and is rejected by the state layer's duplicate-run
check) without adding a new CLI argument.

`StateStore.dispatch_delegation_step` intentionally does everything in one
`BEGIN IMMEDIATE` transaction rather than reusing `start_next_step` followed
by a second write: the acceptance criterion is that dispatch "atomically
creates the child run linked to the parent step," and a two-transaction
window would leave a step `running` with no child and no supported way to
retry it (recoverable interruption handling between spawn and linkage is
explicitly issue #123's scope, not this one's). The method mirrors
`start_next_step`'s existing two-branch shape (run-payload write only when
the run was still `queued`) so a delegation step that is not a run's first
step does not spuriously rewrite the run row.

Every existing "rebuild this step/run payload from the typed view" call site
that already preserved `command`/`message`/`sandbox_policy`/etc. across a
lifecycle rewrite (`cancel`, `transition_step`, `recover_running_step`,
`_decision_payload` for steps; `transition`, `cancel`, `start_next_step`,
`complete_step_from_result`, `complete_step_from_chat_response`,
`fail_step_from_error`, `recover_running_step`, `reject_step` for runs) now
also preserves `delegation`/`delegated_run_id` and
`parent_run_id`/`parent_step_id` respectively — without this, the first time
a child run executed its own first step (a completely ordinary operation)
would have silently dropped its own parent linkage, since every one of those
sites previously rebuilt a fresh payload from only the fields it knew about.
Introduced `RunCoordinator._base_run_payload` to make this systematic rather
than eight near-identical manual edits.

A delegation step's `approval_required` gate is honored explicitly inside
the new dispatch path (`ApprovalRequiredError`), since it bypasses
`start_next_step`, which is where that gate normally lives for command and
provider steps.

`AgentRun`/`RunStep` gained new optional fields (`parent_run_id`,
`parent_step_id`, `delegation`, `delegated_run_id`) that flow through
`payloads.py`'s existing `asdict`-based JSON builders unchanged; updated
five pre-existing CLI tests that asserted exact run-payload dict equality
to include the two new (`null` for non-child runs) keys, and one that
asserted the old two-way "exactly one of command or provider message" error
text, now three-way. `_redact_step_for_http` needed no change: delegation's
`child_objective`/`target_agent_id`/`delegated_run_id` are non-sensitive
declared metadata like a step's own `objective`, not command argv or
provider message content/output.

Verification: activated `.venv`; full `pytest` 778 passed (up from 760, +18
new/changed cases: 12 in `test_runtime.py`, 3 in `test_state.py`, 1 in
`test_worker.py`, 2 in `test_run_cli.py`, plus 5 pre-existing CLI tests
updated for the new payload shape); `codex-agentic-os index build` (source
changed) then `index check` current at 27 files, 1129 symbols, 6841
relationships; `git diff --check` clean. No dashboard/TypeScript files
changed (out of this issue's CLI-scoped acceptance criteria).
