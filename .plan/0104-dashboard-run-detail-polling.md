# Plan 0104: Dashboard Run Detail Polling

## Status
Complete

## Goal
Let an operator select a durable run in the read-only dashboard and inspect its
ordered steps, lifecycle history, pending approvals, and provider usage while
the view refreshes by bounded polling and fails closed to an explicit API error.

## Tasks
- [x] Extend the typed dashboard client and same-origin proxy routes for run
      detail, history, approvals, and usage using GET requests only.
- [x] Add run selection plus polling list/detail views that discard stale data
      after a failed refresh and expose no mutation controls.
- [x] Add focused tests for detail rendering, polling refresh, unreachable API
      behavior, and the read-only endpoint boundary.
- [x] Run frontend verification, the full Python suite, index freshness check,
      and `git diff --check`; record the run, commit, push, and close the issue.

## Resume Notes
Selected active-milestone issue: #113 (Sprint 18, priority:1,
`agent-ready`). Its only dependency, #112, is closed in commit `d2e954f`.
Sprint 18 issues #114 and #115 remain blocked on the ordered dashboard slices.

The committed Python code index is current at 27 files, 1072 symbols, and 6388
relationships. This issue is expected to touch only TypeScript frontend files,
which are outside the current index configuration.

Implemented a typed four-contract detail bundle, a GET-only allowlisted dynamic
proxy, non-overlapping list/detail polling, and a browser detail view for steps,
history, pending approvals, and usage. A failed poll replaces the prior ready
state instead of leaving stale data visible. The only UI action is navigation
between the run list and detail; there are no mutation controls or calls.

Verification: `pnpm test` 22 passed, `pnpm typecheck` clean, and `pnpm build`
clean. The full Python suite remains `720 passed`; `codex-agentic-os index
check` reports current (27 files, 1072 symbols, 6388 relationships), and `git
diff --check` is clean.
