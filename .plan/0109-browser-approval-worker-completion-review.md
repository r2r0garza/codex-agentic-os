# Plan 0109: Browser Approval Worker Completion Review

## Status
Complete

## Goal
Provide a reproducible browser review that approves a pending command step
through the dashboard and proves a real worker observes the durable decision,
executes the step once in Docker, and completes the run.

## Tasks
- [x] Add a separate repository-owned mutable review harness that creates
      isolated state, keeps a real worker active at an approval gate, and serves
      the loopback API and dashboard.
- [x] Assert the post-approval durable run, step, and history outcome while the
      dashboard remains available for final browser inspection.
- [x] Document the exact command, confirmation interaction, expected evidence,
      loopback boundary, and cleanup behavior.
- [x] Exercise the harness in a real browser and run proportional regression,
      index freshness, and whitespace verification.
- [x] Record the run, commit, push, close the issue, and perform blocked review.

## Resume Notes
Selected active-milestone issue: #119 (Sprint 19 "Web approval and
intervention controls", priority:3, `agent-ready`). Its only dependency, #118,
is closed in commit `be2b53c`; #119 is the sole open issue in the active
milestone and therefore the only eligible implementation slice for this run.

The Sprint 18 `dashboard-operator-review.sh` deliberately hashes the database
before serving and requires no mutation afterward. This issue needs the
opposite proof, so it will add a separate harness rather than weakening that
read-only review's contract.

Added `scripts/dashboard-approval-review.sh`. It recreates only an isolated
`/tmp` database, registers a dedicated worker, and queues an unguarded Docker
preflight command followed by an approval-required Docker command. The real
worker executes the preflight so the run reaches `running` through normal
dispatch, then remains alive at the approval gate while the API and dashboard
serve on explicit loopback addresses. The harness verifies the API's actual
listening socket with `lsof`, waits for the browser decision, and requires the
durable approved request, succeeded step/run, exactly one start and success for
the guarded step, and terminal `run_succeeded`. DEVELOPMENT.md distinguishes
this mutable proof from the retained Sprint 18 database-hash proof.

Live browser review against ports 8084/3004: the detail view showed the
preflight succeeded and `approved-command-step` queued/pending; clicking
Approve opened “Approve this step?” with no mutation yet; clicking “Confirm
approve” cleared the pending request and added `step_approved`; the real worker
then ran the Docker step exactly once; polling rendered the run and both steps
`succeeded` plus `step_started`, `step_succeeded`, and `run_succeeded` history.
The harness printed its durable-evidence pass and browser logs contained no
warnings or errors.

Verification: `sh -n scripts/dashboard-approval-review.sh`; live harness with
real Docker/API/dashboard/worker and in-app browser; dashboard `pnpm test` (43
passed), `pnpm typecheck`, and `pnpm build`; full `pytest` (760 passed);
`codex-agentic-os index check` current; `git diff --check` clean. No indexed
Python source changed.
