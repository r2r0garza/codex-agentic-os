# Plan 0076: Read-Only Run Usage CLI Summary

## Status
Complete

## Goal
Give operators a read-only `run usage RUN_ID` command that presents each
provider step's normalized usage evidence in durable order plus a run-level
token aggregate, without exposing anything beyond what is already safe to
expose.

## Tasks
- [x] Add the `run usage` subcommand, reusing the existing read-only
      `StateStore` path and missing-database/unknown-run rejection used by
      `inspect`/`history`/`approvals`/`staleness`.
- [x] Build a per-step usage view (`step_id`, `position`, `status`,
      `provider`, `model`, `usage`) over provider steps only, omitting command
      steps from the list entirely.
- [x] Mark usage explicitly unavailable (no fabricated token counts) for
      provider steps that have not yet succeeded or whose adapter reported no
      usage block.
- [x] Compute a run-level aggregate over available usage records, reporting
      unavailable-step counts alongside available totals rather than treating
      missing evidence as zero.
- [x] Run focused and full verification, refresh the committed code index,
      and complete the durable sprint record.

## Resume Notes
Selected active-milestone issue: #76 (Sprint 10, priority:3, agent-ready, no
remaining blocker after #75 closed). Issue #75 established that every
successful provider step durably persists a stable five-field `usage` object;
this issue exposes that evidence through a dedicated read-only CLI summary
without touching command-step execution or introducing cost/budget math.

Implementation complete. `codex-agentic-os run usage RUN_ID` prints provider
steps in durable position order with step id, status, provider, resolved
model (preferring the response's served model over the persisted request
override), and a `usage` block (`available`, `input_tokens`, `output_tokens`,
`raw`, `unavailable_reason`). Command steps are omitted from the list. The
aggregate reports `steps_with_usage_available`, `steps_with_usage_unavailable`,
and summed `input_tokens`/`output_tokens` (null when no step has available
usage). The command opens the state store read-only, rejects unknown runs and
missing databases without creating one, and mutates no run/step/history
record.

Verification: activated `.venv`; focused `run usage` CLI tests passed (6
new tests covering ordering/aggregation, unavailable-usage non-fabrication,
command-only runs, unknown run, missing database, and no-mutation read-only
behavior); full suite 478 passed (up from 472); a three-process SQLite UAT
registered an agent, claimed and completed a provider step with usage in a
second process, and read the aggregate back from a third process with no
credential, system-prompt, or request-content leakage and an unchanged step
revision after the read; committed index rebuilt/current (20 files, 674
symbols, 4021 relationships); `git diff --check` clean. No README/DEVELOPMENT
change: this dedicated CLI summary is the same "read-only run usage
workflow" already anticipated by Plan 0075's guidance, not a new documented
surface class.
