# Automation Memory

- Run: 2026-07-12T15:37:27Z — implementation run.
- Selected issue: #41, explicit heartbeat/liveness tracking for registered agents.
- Completed: `Agent` now exposes a durable ISO-8601 UTC `last_seen`; registration
  initializes it, and injected-clock `AgentRegistry.heartbeat()` refreshes it while
  preserving the record and rejecting unknown ids without mutation. Added `agent
  heartbeat AGENT_ID [--state-db PATH]`, JSON output coverage for register/list/
  heartbeat, Plan 0051, a DEVELOPMENT.md example, and refreshed the committed index.
  Legacy agent payloads without `last_seen` remain readable as `None`; automatic
  heartbeats, expiry, staleness, and liveness-based eligibility remain out of scope.
- Implementation commit: `9081360`; pushed to `origin/main`; issue #41 auto-closed
  by the commit's `Closes #41`; verification comment posted separately.
- Verification: `pytest -q tests/test_runtime.py tests/test_agent_cli.py` (89 passed);
  `pytest -q` (313 passed); clean index build then `codex-agentic-os index check`
  (current — 19 files, 448 symbols, 2485 relationships); `git diff --check` clean.
- Blocked review: no open issues labeled `blocked`; nothing to re-evaluate.
- Resulting queue: 3 unblocked `agent-ready` issues — #42, #43, and #44 (all
  priority:3). Recommended next: #42, the oldest priority:3 issue; the queue is below
  the 5–10 target band but above the backlog-only threshold.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.

---

- Run: 2026-07-12T15:36:07Z — implementation run.
- Selected issue: #40, environment variable passthrough to container sandbox execution.
- Completed: added a validated `env: tuple[tuple[str, str], ...]` field to
  `SandboxSpec` (`src/codex_agentic_os/sandboxes.py`), rendered as repeatable
  `--env KEY=VALUE` container-engine arguments positioned after mounts and before
  the image. `run execute-next --env KEY=VALUE` (repeatable, via new `_parse_env`
  in `cli.py`) rejects malformed values (missing `=`, empty key, or empty value)
  with a `parser.error` before any queued step is claimed, mirroring the existing
  `--mount` pattern. Added Plan 0050, a DEVELOPMENT.md example, `test_sandboxes.py`
  env-rendering/rejection coverage, and `test_run_cli.py` CLI success/rejection
  coverage. Refreshed the committed index.
- Implementation commit: `f4c38ca`; pushed to `origin/main`; issue #40 auto-closed
  by the commit's `Closes #40`; verification comment posted separately.
- Verification: `pytest -q` (308 passed); `pytest -q tests/test_sandboxes.py
  tests/test_run_cli.py` (142 passed); `codex-agentic-os index build` then
  `codex-agentic-os index check` (current — 19 files, 442 symbols, 2441
  relationships); `git diff --check` clean.
- Blocked review: `gh issue list --label blocked` returned no results; nothing to
  re-evaluate.
- Resulting queue: 4 unblocked `agent-ready` issues — #41, #42, #43, and #44 (all
  priority:3). At the target-band floor; recommend backlog replenishment soon.
  Recommended next: #41, the oldest priority:3 issue.
- Final target state: `main`, implementation pushed to `origin/main`; worktree
  clean before this durable MEMORY.md update.

---

- Run: 2026-07-12T14:35:58Z — backlog-replenishment run.
- Trigger: only 2 unblocked `agent-ready` issues remained (#40 and #41, both
  priority:3), at the ≤2 threshold — no issue was implemented.
- Reviewed: the current code index manifest/freshness, provider/chat/agent-registry/
  sandbox implementation surfaces, DEVELOPMENT.md, plans 0045 and 0047, existing
  queue issue specifications, and deferred-scope notes.
- Created 3 bounded priority:3 issues with acceptance criteria, tests, dependencies,
  and appropriate area labels:
  - #42 — read-only `provider list` CLI for `DEFAULT_PROVIDER_SPECS` discovery
    (area:providers/cli).
  - #43 — optional provider-neutral `chat send --system TEXT`, reusing existing
    adapter system-message mappings (area:providers/cli).
  - #44 — validated `SandboxSpec.working_dir` and `run execute-next --workdir`
    support (area:sandbox/cli).
- Verification: `.venv` activated at the repository-local path; Python 3.12.13;
  `codex-agentic-os index check` current. This queue-only run changed no source and
  did not run the test suite.
- Blocked review: no open issues labeled `blocked`; nothing to re-evaluate.
- Resulting queue: 5 unblocked `agent-ready` issues — #40, #41, #42, #43, and #44
  (all priority:3). Recommended next: #40, the oldest issue at the highest available
  priority.
- Final target state: `main`; source worktree unchanged; this MEMORY.md update is the
  only repository change and will be committed/pushed as the durable run record.

---

- Run: 2026-07-12T14:03:36Z — implementation run.
- Selected issue: #35, replace dead plan-checklist scan in hourly heartbeat workflow.
- Completed: `.github/workflows/hourly-agentic-os.yml`'s heartbeat job no longer greps
  `.plan/*.md` for `- [ ]` checkboxes (a format none of the 41 plan files use anymore).
  It now runs `gh issue list --repo "$GITHUB_REPOSITORY" --state open --label
  agent-ready --json number,title,labels` through a `--jq` filter that drops issues
  also labeled `blocked` and prints a count plus `#number title` lines (or a
  "none found" message). Added the `issues: read` permission the new `gh` call needs;
  `ci.yml` and both workflows' triggers are unchanged. Added Plan 0049 and updated the
  README's heartbeat-workflow description. No `codex_agentic_os` source changed, so
  the committed index was unaffected.
- Implementation commit: `c0c7a7d`; pushed to `origin/main`; issue #35 auto-closed by
  the commit's `Closes #35`; verification comment posted separately.
- Verification: workflow YAML parses via `python3 -c "import yaml; yaml.safe_load(...)"`;
  dry-ran the new step's `gh issue list`/`--jq` command locally against live repo state
  (correctly printed 3 unblocked agent-ready issues at the time); `pytest -q` (297
  passed); `codex-agentic-os index check` (current, no source changed); `git diff
  --check` clean.
- Blocked review: `gh issue list --label blocked` returned no results; nothing to
  re-evaluate.
- Resulting queue: 2 unblocked `agent-ready` issues — #40 and #41 (both priority:3).
  At the ≤2 threshold; next run should be backlog replenishment. Recommended next
  implementation candidate once replenished: #40, the older of the two.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.

---

- Run: 2026-07-12T13:34:58Z — implementation run.
- Selected issue: #26, operator step transition CLI.
- Completed: added `run transition-step STEP_ID STATUS [--output JSON]`, delegating
  lifecycle/output validation and atomic persistence to
  `RunCoordinator.transition_step()`. The command prints the standard step payload,
  never executes command-bearing records, and rejects malformed/non-object JSON before
  mutation. Added Plan 0048, DEVELOPMENT.md examples, focused coordination-only and
  command-bearing coverage, family-preservation/rejection tests, and refreshed the
  committed index.
- Implementation commit: `c3c7147`; pushed to `origin/main`; issue #26 auto-closed by
  the commit's `Closes #26`; verification comment posted separately.
- Verification: `pytest -q tests/test_run_cli.py` (120 passed); `pytest -q` (297
  passed); incremental index build (19 files, 436 symbols, 2415 relationships);
  `codex-agentic-os index check` current; `git diff --check` clean.
- Blocked review: no open issues labeled `blocked`; nothing to re-evaluate.
- Resulting queue: 3 unblocked `agent-ready` issues — #35, #40, and #41 (all
  priority:3). Recommended next: #35, the oldest priority:3 issue.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.
