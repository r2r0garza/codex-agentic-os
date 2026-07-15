# Plan 0124: Review Policy-Gated Network Execution End to End

## Status
Complete

## Goal
Prove Sprint 23's policy gate end to end: a network-enabled step is
automatically held, approved, executed through the normal worker/dispatch
path, and its policy decision is reconstructable from durable history; rule
changes made after a step is already pending, approved, rejected, or
terminal never retroactively alter that step's recorded decision.

## Tasks
- [x] Add focused regressions proving a later, higher- or lower-precedence
      rule created after a step is pending, approved, rejected, or succeeded
      does not rewrite that step's `policy_rule_id`/`policy_reason` or add a
      second `step_policy_gated` history entry.
- [x] Add a focused regression proving a manually flagged
      `approval_required` step is unaffected by a matching policy rule (no
      `policy_rule_id` is attached and no `step_policy_gated` entry is
      recorded), preserving pre-Sprint-23 manual approval behavior exactly.
- [x] Add a focused regression reconstructing a held-then-approved step's
      policy decision from a fresh `StateStore`/`RunCoordinator` over the
      same durable database, standing in for a process restart.
- [x] Add `scripts/policy-gated-network-review.sh`: a repository-owned,
      Docker- and `jq`-based executable review that creates a
      network-enabled command step, a matching `sandbox_network_access`
      policy rule, and a real `worker run` process; waits for the automatic
      hold; approves and executes the step through the normal
      worker/dispatch path; and, using only fresh CLI processes against the
      durable database, reconstructs the triggering rule id and reason from
      `run history` after the worker exits.
- [x] Document the review script in DEVELOPMENT.md next to the existing
      `policy create|list|inspect` documentation.
- [x] Refresh the index and verify the full suite.

## Resume Notes
Selected active-milestone issue: #138 (Sprint 23 "Declarative execution
policy gates", priority:2, `agent-ready`), the sole unblocked ready issue
after #136 and #137 closed.

Non-retroactivity is already structurally enforced by
`RunCoordinator._apply_execution_policy_gate` (Plan 0123): it returns
immediately once `step.approval_status is not None`, before any rule is
matched, and dispatch only ever calls the gate for a step still in
`StepStatus.QUEUED`. A rejected step transitions straight to
`StepStatus.FAILED`; a succeeded step is no longer queued; an approved step
keeps its `approval_status` set, so the next dispatch skips evaluation
entirely. The new regressions exercise this guarantee directly rather than
re-deriving it: each creates a second, competing rule after the step's
decision is recorded and asserts the step's policy fields and history are
byte-for-byte unchanged, then (where applicable) drives the step to
completion to prove the stale rule was never consulted.

`scripts/policy-gated-network-review.sh` follows the existing
`scripts/dashboard-approval-review.sh` and
`scripts/tool-call-history-review.sh` conventions: `set -eu`, a dependency
preflight (`docker`, `jq`, the repository `.venv`), a `trap cleanup` that
terminates the background worker, and JSON assertions via `jq -e` rather
than string matching. It combines `run inspect-step` (for `policy_rule_id`/
`policy_reason`, which `_step_payload` carries) with `run approvals` (for
`approval_status`, which `_step_payload` intentionally omits per its
Sprint-6 comment) to observe the held state, matching how
`dashboard-approval-review.sh` already combines `run inspect` and
`run approvals`. The reviewed step is a plain command step, so the review
requires no live provider credentials — network enablement only affects the
container's network namespace, not any outbound provider call.

Verification: activated `.venv`; 8 new focused runtime tests covering the
pending/approved/rejected/terminal non-retroactivity matrix, manual-approval
compatibility, and restart reconstruction, run alongside the existing 60
policy tests (68 passed); full `pytest` passed 916 (up from 910); executed
`scripts/policy-gated-network-review.sh` against local Docker, which passed;
index rebuilt to 28 files / 1395 symbols / 8114 relationships and `index
check` reported current; `git diff --check` passed.
