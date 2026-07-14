# Plan 0111: Delegation Child-Outcome Propagation

## Status
Complete

## Goal
Complete or fail a running parent delegation step only from its linked child
run's durable terminal outcome, preserving child identity and outcome evidence
with compare-and-swap-safe parent transitions.

## Tasks
- [x] Reconcile a running delegation step when its linked child succeeds,
      fails, or is cancelled; keep it pending while the child is active.
- [x] Persist child run id, status, agent identity when known, and terminal
      output on the parent step, with delegation-specific history provenance.
- [x] Complete the parent run when the successful delegation is final, or
      fail it explicitly for a failed/cancelled child.
- [x] Add focused runtime and worker/CLI-path coverage for wait, propagation,
      and compare-and-swap conflict behavior.
- [x] Refresh the code index and run proportional verification.

## Resume Notes
Selected active-milestone issue: #122 (Sprint 20 "Parent-child run
delegation", priority:1, `agent-ready`). Its dependency #121 is closed at
commit `4f18d57`; #123 and #124 remain blocked on this issue and are outside
this run's scope.

`RunCoordinator.execute_next_step` now treats a running delegation as a
reconciliation opportunity. Queued/running children still raise
`DelegationPendingError` without mutation. A terminal child produces durable
parent-step output containing its run id, status, output, and assigned agent
when known. Successful children succeed the step and either advance the
parent or complete it; failed/cancelled children fail the parent explicitly.
The terminal parent transition and history append share one CAS transaction,
and a non-final successful delegation uses the same checked state primitive.

The CLI now routes a running delegation back through reconciliation instead
of returning early solely because no queued step exists. Worker coverage
demonstrates a target agent completing the child followed by the parent agent
completing the delegation. Verification: 16 focused runtime delegation tests,
2 focused worker delegation tests, 1 focused CLI delegation test, full
`pytest` (786 passed), refreshed index (27 files, 1137 symbols, 6953
relationships), current `index check`, and clean `git diff --check`.
