# Plan 0123: Apply Execution Policy Before Step Claim

## Status
Complete

## Goal
Evaluate durable finite-criterion policy rules before queued step dispatch,
route the deterministic first match through the existing approval gate, and
record reconstructable, non-sensitive policy provenance before execution.

## Tasks
- [x] Evaluate only enabled persisted rules against durable execution kind,
      declared tool names, and sandbox network access.
- [x] Resolve multiple matches by lowest `(precedence, rule_id)` ordering.
- [x] Persist pending approval state and the triggering rule id/reason before
      command, provider, tool, or delegation dispatch.
- [x] Preserve manual approval behavior, approved policy decisions, and
      unmatched-step dispatch behavior.
- [x] Extend durable history and read payloads with safe policy provenance.
- [x] Cover runtime matching/precedence/history and worker non-execution.

## Resume Notes
Selected active-milestone issue: #137 (Sprint 23 "Declarative execution
policy gates", priority:1, `agent-ready`), the sole unblocked ready issue.

Policy evaluation is attached to the queued-step dispatch boundary shared by
direct coordinator calls and the worker loop. The match is persisted while
the step remains queued, using both the step revision and the evaluated rule-id
snapshot as compare-and-swap guards; a concurrently created rule therefore
forces dispatch to retry against the new rule set. The existing
`ApprovalRequiredError` then stops a matched dispatch. Once approved or
rejected, the non-pending decision is never reevaluated, so later rule
creation cannot rewrite an already-decided approval.

The durable provenance fields contain only the immutable rule identifier and
operator-authored reason. No command arguments, tool arguments, provider
payload, sandbox environment, or output is copied into policy history.
