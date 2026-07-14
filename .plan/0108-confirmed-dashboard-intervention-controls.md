# Plan 0108: Confirmed Dashboard Intervention Controls

## Status
Complete

## Goal
Let the dashboard show contextual approve, reject, cancel, and failed-step
retry controls with explicit confirmation, calling only the #117 mutation
API, and refresh from durable API state after every attempt rather than
guessing the outcome locally.

## Tasks
- [x] Extend `lib/api.ts`'s `RunStep` type with `failure_kind`/`retry_eligible`
      and add `approveStep`/`rejectStep`/`cancelRun`/`retryStep` client
      functions that `POST` to the dashboard's own same-origin proxy.
- [x] Add a `POST` handler to `app/api/v1/runs/[...segments]/route.ts` that
      forwards only the four mutation-shaped paths to the backend via a new
      `proxyMutationPost` in `proxy.ts`; every other path stays rejected
      without forwarding, matching the existing `GET` handler's boundary.
- [x] Render approve/reject only next to a pending approval, cancel only next
      to an active (`queued`/`running`) run, and retry only on a failed step
      whose `retry_eligible` is `true`.
- [x] Require an explicit confirmation dialog (`components/ui/alert-dialog.tsx`)
      before any mutation fires; disable competing controls while one mutation
      is in flight.
- [x] After every mutation attempt (success or failure), reload the run detail
      bundle from the API and render the durable result; show a clean error
      banner with the API's structured message on failure/staleness.
- [x] Cover contextual visibility, confirmation gating, success refresh,
      stale/ineligible failure refresh, and absence of ineligible controls
      with dashboard tests; verify `pnpm test`, `pnpm typecheck`, `pnpm build`.

## Resume Notes
Selected active-milestone issue: #118 (Sprint 19 "Web approval and
intervention controls", priority:2, `agent-ready`, unblocked once #117
closed in commit `f4a63bf`). #119 ("Demonstrate browser approval completing a
worker run") remains correctly `blocked` on this issue.

`usePollingLoad` (`hooks/use-polling-load.ts`) previously returned only a
`LoadState<T>`; there was no way to force an immediate reload outside the
polling interval, which mutation success/failure both need ("reload ... after
every mutation attempt", not wait out the remaining interval). It now returns
`{ state, refresh }`: an internal `pollNowRef` captures the in-effect `poll`
closure, and `refresh()` invokes it directly (clearing any pending timeout
first, so a manual refresh does not race a concurrently firing scheduled
poll). `components/run-list.tsx` was updated to destructure `{ state }` since
it has no mutation surface; `components/run-detail.tsx` uses both.

`RunStep.retry_eligible` mirrors `payloads.py`'s `_step_payload`, which only
sets that field when `step.status is StepStatus.FAILED` — the dashboard type
marks it optional (`retry_eligible?: boolean`) and the retry column only
renders when `step.status === "failed" && step.retry_eligible === true`,
never inferring eligibility from any other field. Cancel eligibility uses the
run's own `status` (`"queued"` or `"running"`) rather than a server-supplied
flag, mirroring `RunCoordinator.cancel`'s own transition-table check.

Every mutation client function (`approveStep`, `rejectStep`, `cancelRun`,
`retryStep`) shares a new `postJson` helper parallel to the existing
`fetchJson`, and `retryStep` sends `expected_step_revision`/
`expected_run_revision` from the run detail's own already-loaded step/run
revisions — the dashboard has no separate revision-entry UI, matching the
CLI's required-flag contract from #117 exactly. `approve`/`reject`/`cancel`
send an empty JSON body, matching the four routes' documented contract.

The `[...segments]/route.ts` `POST` handler validates path shape itself
(`{run_id}/cancel` at 2 segments, `{run_id}/steps/{step_id}/{approve,reject,
retry}` at 4 segments) before ever calling `proxyMutationPost`, so an
unrecognized or GET-only path (e.g. posting to `/history`) 404s without
forwarding — mirrored by a dedicated route test. `proxyMutationPost` follows
`proxyReadOnlyGet`'s exact response-passthrough shape (status, body, content
type) so error responses from the backend (400/404/409) reach the browser
byte-for-byte, including the CAS-conflict/ineligibility message text the
mutation-error banner displays.

`ConfirmMutationButton` (local to `run-detail.tsx`) wraps
`AlertDialogTrigger`/`AlertDialogAction` with its own controlled `open` state,
because `AlertDialogAction` in this codebase's shadcn wrapper is a plain
`Button`, not a primitive `Close` — the confirm handler must both perform the
mutation and close the dialog itself (`setOpen(false)` in a `finally`), unlike
`AlertDialogCancel`, which already wraps `AlertDialogPrimitive.Close`. A
run-detail-level `mutatingId` state disables every other control's trigger
while one mutation is in flight, preventing a second dashboard click (or a
second browser tab) from double-submitting; a `mutationError` state renders a
destructive `Alert` banner with the API's message and is cleared at the start
of the next attempt.

Live UAT (no unit-test substitute): built an isolated state database with a
queued provider step requiring approval on a `running` run (`agent register`,
`run create`, `run add-step --approval-required`, `run transition running`),
served it through `codex-agentic-os api serve` and the dashboard dev server
(reusing the repository's existing `.claude/launch.json` `api`/`dashboard`
preview configs, port 8099/3010), and drove the real browser: clicked
Approve, confirmed via the dialog, and watched the pending-approval card
clear and lifecycle history gain a `step_approved` entry, confirmed durably
via `run inspect` (step revision incremented to 2 outside the browser).
Clicked Cancel run, confirmed, and watched the run and its queued step move
to `cancelled` with `step_cancelled`/`run_cancelled` history entries, and the
Cancel run control itself disappear once the run reached a terminal status.
Replayed a direct `POST .../approve` against the now-decided step via `curl`
and confirmed the API's existing 409 contract (`"step is not pending
approval"`) unchanged. No browser console errors during the session.

Updated `DEVELOPMENT.md`'s dashboard section: corrected the now-inaccurate
"no approve/reject/cancel/retry controls" claims left over from Sprint 18 (the
review harness itself still never mutates a run, but the detail view now
genuinely offers the controls), and added a short "Dashboard mutation
controls" subsection documenting the eligibility/confirmation/refresh
contract for future readers. No change to `scripts/dashboard-operator-review.sh`
was needed — it asserts nothing about mutation controls itself, only Ctrl-C
cleanup and the database hash check.

Verification: `pnpm test` 43 passed (up from 23, +20 net new/changed cases;
new coverage in `lib/api.test.ts`, `hooks/use-polling-load.test.ts`,
`app/api/v1/runs/[...segments]/route.test.ts`, and `components/run-detail.test.tsx`),
`pnpm typecheck` clean, `pnpm build` clean. Full `pytest` remained 760 passed
(no Python source changed); `codex-agentic-os index check` current
(dashboard/TypeScript files are not indexed); `git diff --check` clean.
