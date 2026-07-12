# Automation Memory

- Run: 2026-07-12T16:20:26Z — backlog-replenishment run.
- Trigger: exactly 2 unblocked `agent-ready` issues remained (#43 and #44, both
  priority:3), at the ≤2 threshold — no issue was implemented.
- Reviewed: active `.venv`; current queue specifications; provider, agent-registry,
  sandbox, and CLI surfaces; recent relevant plans and deferred-scope notes. The
  stale committed index was rebuilt and verified (20 files, 453 symbols, 2503
  relationships).
- Created 3 bounded priority:3 issues with acceptance criteria, tests, dependencies,
  and appropriate area labels: #45 provider credential readiness (providers/cli),
  #46 read-only agent inspection (runtime/cli), and #47 explicit sandbox network
  opt-in (sandbox/cli).
- Verification: `.venv` activated (Python 3.12.13); `codex-agentic-os index build`
  and `codex-agentic-os index check` succeeded. Queue-only run; tests not run.
- Blocked review: no open issues labeled `blocked`; nothing to re-evaluate.
- Resulting queue: 5 unblocked `agent-ready` issues — #43, #44, #45, #46, and #47
  (all priority:3). Recommended next: #43, oldest at the highest available priority.
- Final target state: `main`; source unchanged; refreshed `.code-index/` artifacts
  and this MEMORY.md update are the durable changes to commit and push.

---

- Run: 2026-07-12T16:04:43Z — implementation run.
- Selected issue: #42, read-only provider defaults listing for the CLI.
- Completed: added `codex-agentic-os provider list`, printing every
  `DEFAULT_PROVIDER_SPECS` entry via `ProviderSpec.to_dict()` in existing registry
  order, with no network or state-database access. Credential output stays limited to
  `api_key_env` variable names; secret values are never read or printed. Added Plan
  0052, a DEVELOPMENT.md usage example, and focused CLI coverage for ordering, field
  serialization, credential-value absence (with live env vars set), and no-network
  access. Refreshed the committed index.
- Implementation commit: `eaf37bf`; pushed to `origin/main`; issue #42 auto-closed by
  the commit's `Closes #42`; verification comment posted separately.
- Verification: `pytest -q` (316 passed); `codex-agentic-os index build` then
  `codex-agentic-os index check` (current — 19 files, 448 symbols, 2489
  relationships); `git diff --check` clean; manual `provider list`/`--help`
  invocation checked by hand.
- Blocked review: `gh issue list --label blocked` returned no results; nothing to
  re-evaluate.
- Resulting queue: 2 unblocked `agent-ready` issues — #43 and #44 (both priority:3).
  At the ≤2 threshold; next run should be backlog replenishment. Recommended next
  implementation candidate once replenished: #43, the older of the two.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.

---

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
