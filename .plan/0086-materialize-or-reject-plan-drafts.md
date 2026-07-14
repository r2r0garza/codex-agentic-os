# Plan 0086: Materialize or Reject Plan Drafts

## Status
Blocked

## Goal
Add explicit CAS-safe accept/reject decisions for a durable plan draft, with
acceptance atomically creating executable queued steps and rejection recording
durable provenance without creating steps.

## Tasks
- [x] Select Sprint 13 issue #89 and inspect the plan-draft contract, existing
      queued-step validation, transactional persistence, approval-decision CAS
      pattern, CLI surface, and durable history schema.
- [ ] Specify the executable payload carried by each proposed step. A command
      step needs an argv and persisted sandbox policy; a provider step needs a
      provider message (provider/content and optional model parameters). The
      current draft stores only `objective` and `execution_kind`.
- [ ] Specify collision-free materialized step identities and plan-decision
      identity in run history.
- [ ] Add one atomic persistence boundary that checks the draft/run snapshots,
      rejects existing step identities, writes every queued step in stable
      order, transitions the plan, and appends decision history.
- [ ] Add coordinator and CLI accept/reject operations with deciding-agent and
      expected-revision provenance.
- [ ] Add focused success, rejection, stale/competing-decision, no-partial-write,
      and existing-lifecycle regression tests.
- [ ] Update DEVELOPMENT.md, rebuild/check the index, run the full suite, and
      run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #89 (priority:2), the sole `agent-ready`
issue in Sprint 13 "Operator-accepted model plan decomposition" at run start.

Implementation is blocked by an internal contract gap between Plans 0084/0085
and the acceptance criteria. `PlanStepProposal` and the persisted draft contain
only a natural-language `objective` plus `execution_kind` (`command` or
`provider`). `RunCoordinator.add_step`, however, deliberately requires exactly
one concrete command argv or complete `ProviderMessage`; command execution also
requires an explicit persisted sandbox policy. Acceptance is expressly not a
plan-editing operation, so it has no authorized input from which to supply
those missing fields. Treating an objective as shell text, inventing a no-op
command, or silently converting command work to provider work would violate
the durable execution and safety contracts and would not produce genuinely
executable steps.

Two adjacent details also need an explicit contract before implementation:
drafts do not carry proposed step ids, and `run_history` has no `plan_id`
field even though more than one draft may be attached to a run. These are
tractable once the primary executable-payload shape is specified, but should
be decided together so the acceptance transaction can be atomic and
reconstructable.

No runtime, state, CLI, documentation, test, or index source was changed in
this run. Issue #89 should remain in Sprint 13 but lose `agent-ready` and gain
`blocked` until its draft/materialization contract explicitly closes these
gaps. Issue #90 remains blocked on #89.
