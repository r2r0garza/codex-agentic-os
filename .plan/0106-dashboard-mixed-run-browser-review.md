# Plan 0106: Dashboard Mixed-Run Browser Review

## Status
Complete

## Goal
Provide a reproducible browser-capable operator review that observes a real
worker-executed mixed run through the read-only dashboard, including a visibly
pending approval and explicit provider usage evidence.

## Tasks
- [x] Add a repository-owned review harness that creates isolated durable state,
      runs the command step through a real worker sandbox, and leaves the ordered
      provider step pending approval.
- [x] Start the loopback API and dashboard against that state and document the
      exact review commands, expected browser observations, and read-only checks.
- [x] Exercise the harness in a real browser and verify the frontend and Python
      suites, index freshness, and whitespace checks.
- [x] Record the run, commit, push, close the issue, and perform blocked review.

## Resume Notes
Selected active-milestone issue: #115 (Sprint 18, priority:3,
`agent-ready`). Its only dependency, #114, is closed in commit `07b0fab`.
This is the only open issue in Sprint 18 and the only eligible implementation
slice for this run.

The existing dashboard already renders the five required read models and has
unit coverage for polling, API failures, read-only proxy allowlisting, and
redacted-field absence. This issue adds reproducible real-process evidence
rather than another mocked component fixture.

Added `scripts/dashboard-operator-review.sh`. It activates `.venv`, creates a
fresh isolated database, registers and runs a real worker, executes the first
step through Docker, verifies the second step has a pending approval, then
stops the worker and serves the durable state through the loopback API and
dashboard. On shutdown it verifies the database SHA-256 digest did not change
while the read-only surfaces were running. DEVELOPMENT.md records the exact
launch command, expected browser evidence, cleanup, and supported local
overrides.

Live browser review: `dashboard-review` rendered as running with ordered
`command-step` succeeded and `approval-step` queued; lifecycle history showed
worker provenance; “Publish the reviewed result” was visibly pending; provider
usage was explicitly unavailable; and the detail page had one navigation
button, no forms, and no approve/reject/cancel/retry labels. Ctrl-C stopped the
servers and the harness reported an unchanged database hash.

Verification: `sh -n scripts/dashboard-operator-review.sh`; `pnpm test` (23
passed), `pnpm typecheck`, and `pnpm build`; full `pytest` (720 passed);
`codex-agentic-os index check` current at 27 files, 1072 symbols, and 6388
relationships; `git diff --check` clean. No indexed Python source changed.
