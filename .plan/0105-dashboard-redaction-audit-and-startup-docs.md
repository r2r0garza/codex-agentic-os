# Plan 0105: Dashboard Redaction Audit and Paired Startup Documentation

## Status
Complete

## Goal
Confirm the read-only dashboard never reconstructs or displays a field the
Sprint 17 HTTP API redacts, add a regression proving it, and document the
paired `codex-agentic-os api serve` plus dashboard dev server startup
workflow in DEVELOPMENT.md.

## Tasks
- [x] Audit `dashboard/lib/api.ts` and every component that renders a
      `RunStep`/`StepUsage` for declared command argv, provider
      `message.content`/`system`, captured `output.stdout`/`stderr`/`content`/
      `raw`, and usage `raw`.
- [x] Add a regression in `run-detail.test.tsx` that renders a run whose API
      response carries a sentinel value in every field the API redacts and
      asserts none of them appear in the rendered output.
- [x] Document the API-plus-dashboard startup path and operator-facing
      verification commands in DEVELOPMENT.md.
- [x] Run frontend verification, the full Python suite, index freshness
      check, and `git diff --check`; record the run, commit, push, and close
      the issue.

## Resume Notes
Selected active-milestone issue: #114 (Sprint 18, priority:2, `agent-ready`).
Its only dependency, #113, is closed in commit `d4cf4bd`. Sprint 18 issue
#115 remains correctly `blocked` on this issue.

Audit finding: no dashboard file reads or renders `RunStep.command`,
`RunStep.message.content`/`system`, `RunStep.output.stdout`/`stderr`/
`content`/`raw`, or `StepUsage.usage.raw`. `run-detail.tsx` only derives a
step's "command"/"provider" kind label from whether `message` is present,
never from its content. No change to `lib/api.ts` types or any component was
needed; the audit is captured as a regression instead.

Added a `run-detail.test.tsx` case that feeds `RunDetail` a bundle carrying a
distinct sentinel string in `command`, `message.content`, `message.system`,
`output.stdout`, `output.stderr`, `output.content`, `output.raw`, and
`usage.steps[].usage.raw`, then asserts the sentinel never appears anywhere
in the rendered DOM. This guards the presentation boundary against a future
change that starts reading one of these fields, independent of whether the
live API actually redacts it.

Documented the paired startup workflow in DEVELOPMENT.md immediately after
the existing curl-based API UAT recipe: start `codex-agentic-os api serve`
on loopback, copy `dashboard/.env.example` to `.env`, run `pnpm dev`, and
verify in the browser that declared/captured fields never appear as
plaintext and that no mutation controls exist.

Live UAT: registered an agent, created run `run-114` with a command step
(`echo TOP-SECRET-COMMAND-OUTPUT`) and an approval-required provider step
(`message` containing `TOP-SECRET-PROMPT`), executed the command step
through a real Docker sandbox so captured stdout existed, then served it
through a real `codex-agentic-os api serve` process and a real `pnpm dev`
dashboard instance (this repository's `.claude/launch.json` `api`/
`dashboard` configurations). Confirmed via the browser's own network
inspector that the JSON the dashboard actually receives already contains
`"<redacted>"` for `command`, `message.content`, `message.system`,
`output.stdout`, and `output.stderr` (the real Docker invocation command
itself, with only passthrough-safe argv, stays visible per Decision 0008),
and confirmed the rendered page shows none of the sentinel text and no
`"<redacted>"` placeholder literal either — safe absence, not reconstruction.
The pending approval, ordered steps, lifecycle history, and usage-unavailable
state all rendered correctly. Removed the temporary state database, log
files, and the local `dashboard/.env` (gitignored) after the review.

Verification: `pnpm test` 23 passed (up from 22, +1 net), `pnpm typecheck`
clean, `pnpm build` clean. Full Python suite unaffected: `720 passed`;
`codex-agentic-os index check` reports current (27 files, 1072 symbols, 6388
relationships) since no Python source changed; `git diff --check` clean.
