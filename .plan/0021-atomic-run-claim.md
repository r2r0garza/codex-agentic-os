# Plan 0021: Atomic Run Claim

## Status
Complete

## Goal
Allow one worker to claim a queued, unassigned durable run without allowing a competing coordinator to overwrite the assignment.

## Tasks
- [x] Assign the agent identifier and advance the run revision in one SQLite write transaction.
- [x] Reject missing, assigned, non-queued, and empty-agent claims without mutation.
- [x] Prove separate coordinators preserve the first committed assignment.

## Resume Notes
Selected queue issue: #12. `RunCoordinator.claim()` validates the agent identifier and
delegates the conditional assignment to `StateStore.claim_run()` under one immediate
SQLite transaction. A successful claim preserves the queued lifecycle and objective,
advances the revision once, and competing claims cannot replace the winning agent.
Resume with the next prioritized unblocked `agent-ready` GitHub issue.
