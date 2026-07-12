# Plan 0035: Run List Unassigned Filter

## Status
Complete

## Goal
Let operators discover durable runs without an assigned agent while preserving typed
validation and deterministic read-only listing behavior.

## Tasks
- [x] Add `run list --unassigned` with intersection semantics for status filters.
- [x] Reject conflicting use with `--agent-id` without mutation.
- [x] Verify stable ordering, empty results, malformed records, and durable non-mutation.

## Resume Notes
Selected queue issue: #23. `codex-agentic-os run list --unassigned` filters the existing
typed run stream after validation, combines with repeated status filters by intersection,
and rejects simultaneous exact-agent filtering. Resume with the next prioritized
unblocked `agent-ready` GitHub issue.
