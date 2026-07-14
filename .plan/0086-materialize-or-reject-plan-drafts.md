# Plan 0086: Materialize or Reject Plan Drafts

## Status
Complete

## Goal
Add explicit CAS-safe accept/reject decisions for a durable plan draft, with
acceptance atomically creating executable queued steps and rejection recording
durable provenance without creating steps.

## Tasks
- [x] Select Sprint 13 issue #89 and inspect the plan-draft contract, existing
      queued-step validation, transactional persistence, approval-decision CAS
      pattern, CLI surface, and durable history schema.
- [x] Specify the executable payload carried by each proposed step. A command
      step needs an argv and persisted sandbox policy; a provider step needs a
      provider message (provider/content and optional model parameters). The
      current draft stores only `objective` and `execution_kind`.
- [x] Specify collision-free materialized step identities and plan-decision
      identity in run history.
- [x] Add one atomic persistence boundary that checks the draft/run snapshots,
      rejects existing step identities, writes every queued step in stable
      order, transitions the plan, and appends decision history.
- [x] Add coordinator and CLI accept/reject operations with deciding-agent and
      expected-revision provenance.
- [x] Add focused success, rejection, stale/competing-decision, no-partial-write,
      and existing-lifecycle regression tests.
- [x] Update DEVELOPMENT.md, rebuild/check the index, run the full suite, and
      run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #89 (priority:2), the sole `agent-ready`
issue in Sprint 13 "Operator-accepted model plan decomposition" at run start.

Resumed for issue #89 after issue #91 / Plan 0087 closed the executable-payload
and deterministic step-identity gaps. Acceptance must still reject a proposed
`step_id` that already exists in the live `step` kind before any mutation. The
decision transaction will cite the already-unique `plan_id` in durable run
history, CAS-check both the draft and attached run snapshots, allocate stable
step positions inside the same transaction, and write either the complete
accepted step set or no steps.

The prior run was blocked by an internal contract gap between Plans 0084/0085
and the acceptance criteria: the persisted draft lacked the executable fields
`RunCoordinator.add_step` requires. Issue #91 / Plan 0087 resolved that gap by
persisting a validated command plus sandbox policy or a complete provider
message for every proposed step.

This run added the remaining plan-decision identity directly to `run_history`
and migrates existing databases with a nullable `plan_id` column. Accepted and
rejected decisions cite the plan id plus an optional registered deciding agent.
The dedicated transaction CAS-checks the plan and attached run, rejects any
pre-existing materialized step id, allocates positions while holding the write
transaction, and commits either the entire accepted step set or none.

Verification after the source/index refresh: full suite `577 passed`; committed
index current (23 files, 866 symbols, 5035 relationships); `git diff --check`
clean. Issue #90 remains blocked on #89 until this implementation is committed,
pushed, and the issue is closed.
