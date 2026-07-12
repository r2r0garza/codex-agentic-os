# Automation Memory

- Run: 2026-07-12T09:31:59Z — implementation run.
- Selected issue: #33, invalid Anthropic top-level `cache_control` payload.
- Completed: removed the unsupported field from `AnthropicAdapter.complete()`,
  updated the exact-payload regression test with an explicit absence assertion,
  added Plan 0042, and refreshed the deterministic repository index. Public
  behavior beyond removal of the invalid field is unchanged.
- Implementation commit: `3ba4d68886e8d0934067aec1814db23403edb024`;
  pushed to `origin/main`; issue #33 closed with verification results.
- Verification: `pytest -q tests/test_chat.py` (19 passed); `pytest -q` (236
  passed); incremental index build (17 files, 374 symbols, 2084 relationships);
  `index check` current; `git diff --check` clean.
- Blocked review: no open issues labeled `blocked`.
- Resulting queue: 6 unblocked `agent-ready` issues — #37 (priority:1), #34 and
  #36 (priority:2), #25, #26, and #35 (priority:3). Recommended next: #37, the
  only remaining priority:1 issue.
- Final target state: `main`, implementation pushed to `origin/main`; worktree
  clean after the durable MEMORY.md commit.

---

- Run: 2026-07-12T09:05:00Z — backlog-replenishment run.
- Trigger: only 2 unblocked `agent-ready` issues remained (#25, #26), at the
  ≤2 threshold; per protocol, analyzed repo state and created issues without
  implementing one.
- Analysis: reviewed `src/codex_agentic_os/{chat,sandboxes,cli,state,runtime,
  providers}.py`, `.github/workflows/*.yml`, `.plan/`, and open-issue bodies
  for #25/#26 to avoid duplication.
- Created 4 issues (labels created: `area:sandbox`, `area:ci`):
  - #33 (priority:1, area:providers, bug): `AnthropicAdapter.complete()` sends
    an invalid top-level `cache_control` field in the Messages API request
    body (Anthropic only accepts `cache_control` on individual content
    blocks); the current behavior is locked in by
    `tests/test_chat.py::test_anthropic_adapter_posts_native_payload_and_reads_text_blocks`.
  - #34 (priority:2, area:sandbox, enhancement): `ContainerSandbox` has no
    bind-mount support, so `run execute-next` cannot expose repository files
    inside the container; scoped to add `SandboxSpec` mounts, command
    rendering, and a repeatable `--mount` CLI flag.
  - #35 (priority:3, area:ci, bug): the hourly heartbeat workflow scans
    `.plan/*.md` for `- [ ]` unchecked checkboxes, but all 41 plan files use
    `[x]` and the real queue is GitHub issues labeled `agent-ready`
    (per README) — the step is permanently dead.
  - #36 (priority:2, area:runtime/area:cli, enhancement): the `"agent"`
    `StateStore` kind is declared and documented but never read or written;
    scoped to add a minimal `agent register`/`agent list` CLI.
- No implementation commit this run; no issue closed.
- Verification: none required (no code changes); confirmed no open `blocked`
  issues exist (empty review, nothing to re-evaluate).
- Resulting queue: 6 unblocked `agent-ready` issues — #25, #26 (priority:3),
  #33 (priority:1), #34, #36 (priority:2), #35 (priority:3). Within the 5-10
  target band. Recommended next run: #33 (priority:1, only issue at that
  priority — real correctness bug, small bounded fix).
- Final target state: `main`, matches `origin/main`; worktree clean (no
  changes made other than this MEMORY.md update, which will be committed).

---

- Run: 2026-07-12T08:32:29Z — implementation run.
- Selected issue: #32, OpenRouter default API endpoint.
- Completed: added canonical `OPENROUTER_DEFAULT_BASE_URL`, reused it in the
  default provider registry and compatible-adapter fallback, preserved explicit
  overrides and optional credentials, added Plan 0041, updated Decision 0004,
  and refreshed the index. Pre-existing README.md and untracked DEVELOPMENT.md
  changes were preserved and excluded from the commit.
- Implementation commit: `67d895d73d209112b64e0ff98ff551ea0cd68ff0`;
  pushed to `origin/main`; issue #32 closed.
- Verification: `pytest -q tests/test_chat.py tests/test_foundation.py` (22
  passed); `pytest -q` (236 passed); incremental index build (17 files, 374
  symbols, 2084 relationships); `index check` current; `git diff --check` clean.
- Blocked review: no open issues labeled `blocked`.
- Resulting queue: 2 unblocked `agent-ready` issues — #25 and #26 (both
  priority:3; #25 older). Recommended next run: backlog replenishment because
  the queue is at the ≤2 threshold; analyze current state and create/prioritize
  focused issues without implementing one.
- Final target state: `main`, pushed to `origin/main`; worktree retains unrelated
  user changes (`README.md` modified, `DEVELOPMENT.md` untracked).

---

- Run: 2026-07-12T08:05:15Z — implementation run.
- Selected issue: #31, coordination-only step creation CLI.
- Completed: made `run add-step`'s trailing command positional optional
  (`nargs="*"`, normalized to `command=None`), preserving command-bearing
  creation and existing timeout-without-command rejection in
  `RunCoordinator.add_step()`/`_validate_command()` (already supported
  command-less steps). Added Plan 0040, README CLI example and status line,
  refreshed index.
- Implementation commit: `f675afb2e263c757b4aec99a9f9fd795f5dbafdc`; pushed to
  `origin/main`; issue #31 closed (a follow-up comment corrected an initially
  mistyped commit hash in the close comment).
- Verification: `pytest -q tests/test_run_cli.py` (89 passed, 3 new); `pytest -q`
  (230 passed); incremental index build (17 files, 369 symbols, 2069
  relationships); `index check` current; `git diff --check` clean; manual CLI
  smoke test of objective-only, command-bearing, and rejected
  timeout-without-command step creation with durable read-back.
- Blocked review: no open issues labeled `blocked`.
- Resulting queue after closing #31: 3 unblocked `agent-ready` issues — #32
  (priority:2), #26 and #25 (priority:3, #25 older). Total open-issue count (3)
  is below the target 5-10 band; next backlog-replenishment run (triggered when
  ≤2 unblocked agent-ready issues remain) should also consider adding issues to
  restore that range. Recommended next: #32 (priority:2, only issue at that
  priority).
- Final target state: `main`, pushed to `origin/main`, clean worktree.

## Follow-up
- Run time (UTC): 2026-07-12T08:39:00Z
- User authorized the preserved documentation changes. Reviewed, committed, and
  pushed `README.md` plus new `DEVELOPMENT.md` in
  `528fe34c0d3783851a1641569200e3677b35d32d`.
- Final repository state: `main` matches `origin/main`; worktree clean.

---

- Run: 2026-07-12T01:38:29-06:00 — implementation run.
- Selected issue: #30, operator run claim release CLI.
- Completed: added `run release RUN_ID --agent-id AGENT_ID`, standard ordered payload,
  rejection/non-mutation coverage, Plan 0039, README status, and refreshed index.
- Implementation commit: `6b79a85fa904d5b9931e89ae788ae75c6238557f`; pushed to
  `origin/main`; issue #30 closed.
- Verification: `pytest -q tests/test_run_cli.py` (86 passed); `pytest -q` (227 passed);
  incremental index build (17 files, 366 symbols, 2042 relationships); `index check`
  current; `git diff --check` clean.
- Blocked review: no open issues labeled `blocked`.
- Resulting queue after closing #30: 4 unblocked `agent-ready` issues — #31, #32,
  #25, #26. Recommended next: #31 (priority:2, oldest at that priority).
- Final target state: `main`, pushed to `origin/main`, clean worktree; issue #30 closed.
