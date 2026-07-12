# Plan 0026: Run Claim CLI

## Status
Complete

## Goal
Allow an operator or worker to atomically claim one queued, unassigned durable run by identifier.

## Tasks
- [x] Add `run claim RUN_ID --agent-id AGENT_ID` through the existing atomic coordinator operation.
- [x] Print the standard run payload with steps in durable position order.
- [x] Verify successful persistence and rejection without mutation.

## Resume Notes
Selected queue issue: #17. `codex-agentic-os run claim` delegates assignment, lifecycle,
and concurrency validation to `RunCoordinator.claim()` and prints the claimed run with
its ordered steps. Resume with the next prioritized unblocked `agent-ready` issue.
