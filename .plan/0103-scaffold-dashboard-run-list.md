# Plan 0103: Scaffold Read-Only Dashboard and Render API Run List

## Status
Complete

## Goal
Scaffold the committed Next.js + shadcn/ui dashboard app under `dashboard/`
and render the Sprint 17 read-only API's run list with each run's status,
establishing the frontend foundation the rest of Sprint 18 builds on.

## Tasks
- [x] Scaffold `dashboard/` via `pnpm dlx shadcn@latest init --preset b0
      --template next --name dashboard -y`, merge its ignore rules into
      the repository-root `.gitignore` (scoped under `dashboard/`), and
      delete the scaffold's nested `.git/` directory.
- [x] Install the complete shadcn component set and `@tanstack/react-table`
      into `dashboard/` per DEVELOPMENT.md.
- [x] Add a typed client (`lib/api.ts`) and render a read-only run list
      (`components/run-list.tsx`, used by `app/page.tsx`) covering
      loading, error, empty, and populated states without any mutation
      affordance.
- [x] Discover via live browser verification that a direct browser-side
      fetch from the dashboard's origin to the API's origin (different
      ports) is blocked by the API's absent CORS headers, since issue
      #112 explicitly excludes API-server changes. Added a same-origin
      Next.js route handler (`app/api/v1/runs/route.ts`) that proxies
      `GET /api/v1/runs` to an operator-configured `API_BASE_URL`
      server-side (unaffected by CORS) and forwards the backend's status
      and body verbatim; the client now fetches the relative
      `/api/v1/runs` path. Added `dashboard/.env.example` documenting
      `API_BASE_URL`.
- [x] Set up Vitest + React Testing Library per the bundled Next.js 16
      testing guide (`node_modules/next/dist/docs/.../testing/vitest.md`)
      and add focused tests for the API client's data mapping, the proxy
      route's forwarding/error behavior, and the run-list component's
      loading/empty/error/populated states.
- [x] Run focused frontend verification (`pnpm test`, `pnpm typecheck`,
      `pnpm build`), the full Python suite, `codex-agentic-os index
      check`, and `git diff --check`; update the durable run record,
      commit, push, and close the issue.

## Resume Notes
Selected active-milestone issue: #112 (Sprint 18, priority:1,
agent-ready, no stated dependency) — the only unblocked issue; #113/#114/
#115 remain `blocked` pending this slice.

`.code-index/` only tracks Python source under `src/`/`tests/`, so no
index rebuild was needed for the new TypeScript frontend; `index check`
stayed current at 27 files, 1072 symbols, 6388 relationships throughout.

Frontend verification: `pnpm test` 13 passed, `pnpm typecheck` clean,
`pnpm build` clean. Pre-existing ESLint errors in shadcn-generated
`components/ui/carousel.tsx` and `hooks/use-mobile.ts` (React Compiler
`set-state-in-effect` rule) are scaffold-provided files this issue never
edited and are out of scope. Full Python suite: `720 passed`
(unchanged, no Python source touched). `git diff --check` clean.

A real end-to-end UAT seeded a temporary state database with two runs
(one unassigned/queued, one agent-assigned/queued) via the CLI, started
`codex-agentic-os api serve` on `127.0.0.1:8099`, started the dashboard
dev server against it, and drove it in a real browser: the populated
table showed both runs with correct run id/objective/status/agent, an
empty database showed the explicit "No runs yet" state with no
fabricated rows, and stopping the API server produced the explicit
"Unable to reach the API" error state. This is what surfaced the CORS
gap fixed above — a direct cross-origin client fetch reproduced
`net::ERR_FAILED` before the proxy route existed.
