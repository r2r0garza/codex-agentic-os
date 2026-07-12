# Plan 0025: Run List Agent Filter

## Status
Complete

## Goal
Let operators narrow read-only durable run discovery to one exactly matching assigned
agent without changing validation, ordering, or summary output.

## Tasks
- [x] Add `run list --agent-id` with non-empty exact-match semantics.
- [x] Filter after typed durable validation while preserving run-ID order.
- [x] Verify matching, omitted, empty-result, invalid-value, and non-mutating behavior.

## Resume Notes
Selected queue issue: #13. `codex-agentic-os run list --agent-id AGENT_ID` now filters
typed durable summaries by exact assignment, excluding unassigned runs and preserving
stable run-ID order. Resume with the next prioritized unblocked `agent-ready` issue.
