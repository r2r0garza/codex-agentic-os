# Plan 0085: Expose Read-Only Plan Draft Inspection

## Status
Complete

## Goal
Add a read-only `run inspect-plan <plan_id>` CLI view for the durable plan
draft persisted by `run plan` (Plan 0084), so an operator can review a
proposed ordered step list — and distinguish a reviewable draft from an
absent or malformed one — before any acceptance decision exists.

## Tasks
- [x] Add `RunCoordinator.get_plan(plan_id) -> PlanDraft | None`, mirroring
      the existing `get_step` read accessor and reusing the existing
      `_plan_draft` typed-view builder — no new schema or parsing logic.
- [x] Add `codex-agentic-os run inspect-plan <plan_id> [--state-db]`,
      wired into the same `read_only=True` `StateStore` set as `inspect`,
      `inspect-step`, `list`, `history`, `approvals`, `staleness`, and
      `usage`. Reuses `_plan_draft_payload` — the exact JSON shape `run
      plan` already prints on success — so a `draft` status is fully
      reviewable and an `invalid` status surfaces its recorded `error` and
      raw evidence with no new redaction rules invented.
- [x] Reject a missing plan id with an explicit `ValueError` (consistent
      with `inspect`/`inspect-step`'s missing-record behavior) and a
      missing state database with the pre-existing "does not exist"
      guard — neither path creates a database, draft, run, or step.
- [x] Add focused `RunCoordinator.get_plan` tests: a reviewable draft
      returned in stable step order, an invalid/malformed draft with its
      recorded error and evidence, and `None` for an absent plan id — the
      draft/invalid cases also assert no run or step state changed.
- [x] Add focused CLI tests: a reviewable draft's printed JSON payload and
      no mutation of run/step/plan state, an invalid draft's printed
      status/error/evidence, a missing plan id's explicit failure, and a
      missing database's explicit failure without creating the file.
- [x] Document `run inspect-plan` in DEVELOPMENT.md next to `run plan` and
      `run inspect`.
- [x] Rebuild/check the index, run the full suite, and run `git diff
      --check`.

## Resume Notes
Selected active-milestone issue: #88 (priority:2), tied with #89 on
priority; #88 is the older of the two by creation timestamp
(`2026-07-14T01:17:32Z` vs. `2026-07-14T01:17:34Z`), so it was selected per
the tie-break rule. #90 remains `blocked` on #88/#89.

No new persistence primitive was needed: the `plan` `StateStore` kind and
its `_plan_draft` typed-view builder already existed from Plan 0084, so
this issue was purely a read accessor plus a CLI wiring exercise —
`get_plan` on the coordinator and `inspect-plan` on the CLI, both following
established `get_step`/`inspect-step` conventions exactly.

The issue's scope note ("do not expose credentials or provider request
bodies beyond the raw evidence contract established for malformed
proposals") is satisfied by literally reusing `_plan_draft_payload`, the
same function `run plan` already uses for its own success output — no new
exposure surface was introduced, and none was needed.

Scope was kept to exactly #88: read-only inspection only. No accept/reject
(#89) and no end-to-end restart review (#90) — both remain for later runs
per the milestone's scope boundary.
