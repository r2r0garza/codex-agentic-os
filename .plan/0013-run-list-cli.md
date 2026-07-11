# Plan 0013: Run List CLI

## Status
Complete

## Goal
Provide deterministic, read-only discovery of durable runs through the operator CLI.

## Tasks
- [x] Add typed durable run listing in stable run identifier order.
- [x] Add `run list` with JSON summaries and read-only database access.
- [x] Verify empty, missing, malformed, ordered, and non-mutating behavior.

## Resume Notes
Selected queue issue: #2. The plan is complete. `codex-agentic-os run list` opens an
existing state database read-only and emits run summaries ordered by run identifier.
It does not include steps, create missing databases, or mutate revisions. Resume with
the next prioritized unblocked `agent-ready` GitHub issue.
