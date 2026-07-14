# Plan 0107: CAS-Backed Web Mutation API Endpoints

## Status
Complete

## Goal
Expose loopback-only operator HTTP mutation endpoints for approve, reject,
cancel, and eligible failed-step retry that delegate to the existing durable
coordinator operations, preserving their CAS semantics, eligibility
validation, and durable history provenance exactly.

## Tasks
- [x] Add `POST` routes under the existing loopback API: step approve,
      step reject, step retry, and run cancel.
- [x] Open a writable state connection only for the duration of one mutation
      call, derived from the read-only coordinator's own database path;
      every `GET` route keeps using the caller's read-only coordinator
      unchanged.
- [x] Delegate to `RunCoordinator.approve_step`, `.reject_step`, `.cancel`,
      and `.retry_step` rather than duplicating lifecycle decision logic.
- [x] Return the refreshed, HTTP-redacted run detail on success; return
      structured JSON errors (404 unknown run/step, 400 malformed input,
      409 ineligible/stale) on failure, with no partial mutation either way.
- [x] Cover success, contention/staleness, ineligibility, malformed input,
      unknown run/step, cross-run step mismatch, and unsupported-method
      cases with offline `tests/test_api.py` coverage.
- [x] Verify the full suite, index freshness, and whitespace checks.

## Resume Notes
Selected active-milestone issue: #117 (Sprint 19 "Web approval and
intervention controls", priority:1, `agent-ready`, no stated dependency).
GitHub already showed issues #117-#119 created 2026-07-14T18:08-18:09Z — a
replenishment pass whose `MEMORY.md` record was never committed (mirroring
the same gap the 2026-07-14T15:10:00Z run recorded for #112-#115). Treated
GitHub as authoritative and selected #117; #118 (dashboard controls) and
#119 (browser demo) remain correctly `blocked` on it.

`src/codex_agentic_os/api.py` previously served only `GET` routes against a
read-only `RunCoordinator`; every other method returned a structured 405
(`_reject_mutation`). Renamed the private handler from
`_ReadOnlyAPIRequestHandler` to `_APIRequestHandler` (it is no longer purely
read-only) and added a real `do_POST` router. Existing `GET`-only routes
(`/runs`, `/runs/{id}`, `/history`, `/approvals`, `/usage`) still reject
`POST` with the established 405 contract; only four new paths accept it:
`POST /runs/{run_id}/steps/{step_id}/approve`, `.../reject`,
`.../retry`, and `POST /runs/{run_id}/cancel`. `PUT`/`PATCH`/`DELETE`/`HEAD`
remain rejected on every path, including the new ones.

Each mutation handler opens a fresh `RunCoordinator(StateStore(path,
read_only=False))` built from `self.coordinator.store.path` — the same
path the CLI's `api serve` already passes as `--state-db` — so `build_server`'s
public signature did not need to change and every existing call site
(production `cli.py` and ~90 pre-existing tests) kept working unmodified.
Every `StateStore` method opens and closes its own short-lived SQLite
connection per call (no connection is held across requests), so a
short-lived writable connection alongside the long-lived read-only one is
safe and matches the existing pattern.

`retry` requires the caller to supply `expected_step_revision` and
`expected_run_revision` in the JSON body (mirroring the CLI's
`--expected-step-revision`/`--expected-run-revision`, required because
`RunCoordinator.retry_step` takes them explicitly rather than re-reading
current state atomically); the new retried step's id is generated
server-side (`{step_id}-retry-{uuid4 hex[:12]}`) since a web mutation button
has no id input, unlike the CLI's explicit `new_step_id` argument. `approve`,
`reject`, and `cancel` need no body — those coordinator methods already
re-read current state immediately before their compare-and-swap write, so a
second, stale caller naturally gets a clean `ValueError` (ineligible/terminal
state) without any extra revision parameter. All coordinator `KeyError`s map
to 404, all coordinator `ValueError`s (both ineligibility and genuine
`StateConflictError`-derived CAS conflicts) map to 409, and malformed/missing
JSON body fields map to 400 before any coordinator call — decided this way
because the milestone groups "ineligibility" and "contention/staleness" as
the same durable-state-moved-since-you-looked boundary, and the coordinator
does not itself distinguish them with a typed exception.

Every mutation's success response reuses `_run_payload` plus
`_redact_step_for_http` — the same call the `GET` run-detail route makes —
so Decision 0008's declared-input/captured-output redaction applies
identically to mutation responses; a dedicated test seeds a mixed run (one
completed command step with sensitive captured stdout/stderr alongside one
pending-approval step) and asserts the approved response still redacts the
unrelated step's `command`/`output.stdout`/`output.stderr`.

`No authentication, authorization roles, or multi-operator identity` is an
explicit Sprint 19 scope boundary, so the mutation routes never accept or
require an `agent_id` (unlike the CLI's optional `--agent-id` on
approve/reject); this is intentionally narrower than full CLI parity.

Added 21 new tests: success (approve/reject/cancel/retry, one per class),
ineligibility (already-decided step, terminal-run cancel, non-retry-eligible
rejected step), a genuine CAS conflict (retry raced against a prior direct
retry), malformed/missing retry revisions (5 cases) and invalid JSON body,
unknown run (4 routes) and unknown step (3 routes), a step id that exists
but belongs to a different run, unsupported methods on all four new routes
(parametrized GET/PUT/PATCH/DELETE), an unrecognized mutation-shaped path,
and the redaction-parity case above. Updated
`test_http_api_route_inventory_exposes_no_mutation_handler` (renamed
references only) to assert `PUT`/`PATCH`/`DELETE`/`HEAD` stay rejected while
`do_POST` is now a distinct real handler.

No CLI changes were needed: `cli.py`'s `api serve` command already builds
its coordinator from `arguments.state_db` and passes it straight to
`build_server`, so mutation routes are live automatically. Updated the `api`
and `api serve` argparse help text (no longer "read-only") and the module
docstring to describe the mutation contract.

Verification: activated `.venv`; full `pytest` 760 passed (up from 720, all
40 new/changed cases in `test_api.py`, 88 passed there); `codex-agentic-os
index build` (source changed) then `index check` current at 27 files, 1101
symbols, 6596 relationships; `git diff --check` clean. No frontend files
changed, so no dashboard verification was needed for this issue (dashboard
UI controls are #118, out of scope here).
