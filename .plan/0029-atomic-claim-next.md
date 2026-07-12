# Plan 0029: Atomic Claim-next

## Status
Complete

## Goal
Allow one worker to atomically select and claim the next queued, unassigned durable run.

## Tasks
- [x] Select the first eligible run in stable identifier order inside one write transaction.
- [x] Expose typed coordinator claiming with validation and an empty-queue result.
- [x] Verify eligibility filtering, deterministic selection, and competing coordinators.

## Resume Notes
Selected queue issue: #21. `RunCoordinator.claim_next(agent_id)` delegates selection
and assignment to `StateStore.claim_next_run()` under one immediate SQLite transaction.
Assigned, running, and terminal runs are skipped; eligible runs are selected by stable
identifier order, and an empty queue returns `None` without mutation. Resume with the
next prioritized unblocked `agent-ready` GitHub issue.
