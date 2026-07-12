# Plan 0024: Run List Status Filter

## Status
Complete

## Goal
Let operators narrow read-only durable run discovery to one or more lifecycle statuses
without changing validation, ordering, or summary output.

## Tasks
- [x] Add repeatable `run list --status` choices backed by the existing run lifecycle.
- [x] Apply union filtering after typed durable validation while preserving run-ID order.
- [x] Verify omitted, repeated, empty-result, invalid-choice, and non-mutating behavior.

## Resume Notes
Selected queue issue: #11. `codex-agentic-os run list --status <status>` now accepts
repeatable lifecycle filters with union semantics. Filtering occurs after every durable
run has been converted to its typed representation, so malformed records retain the
existing error behavior and output remains ordered by run identifier. Resume with the
next prioritized unblocked `agent-ready` GitHub issue.
