# Automation Memory

- Run: 2026-07-12T10:33:45Z — implementation run.
- Selected issue: #34, container bind-mount support.
- Completed: added validated host/container mount pairs to `SandboxSpec`, rendered
  repeatable `--volume` arguments after resource limits and before the image, and
  wired strict repeatable `run execute-next --mount HOST:CONTAINER` parsing before
  step execution can mutate state. Added zero/one/multiple and malformed-input
  coverage, Plan 0044, a DEVELOPMENT example, and refreshed the index.
- Implementation commit: `bd72155`; pushed to `origin/main`; issue #34 closed
  with verification results.
- Verification: `pytest -q tests/test_sandboxes.py tests/test_run_cli.py` (107
  passed); `pytest -q` (248 passed); clean index build (17 files, 383 symbols,
  2121 relationships); `index check` current; `git diff --check` clean.
- Blocked review: no open issues labeled `blocked`.
- Resulting queue: 4 unblocked `agent-ready` issues — #36 (priority:2), #25,
  #26, and #35 (priority:3). Recommended next: #36, the only priority:2 issue.
- Final target state: `main`, implementation pushed to `origin/main`; worktree
  clean after the durable MEMORY.md commit.

---

- Run: 2026-07-12T10:14:00Z — implementation run.
- Selected issue: #37, `run add-step` parsing incompatible with Python 3.11.
- Completed: removed the trailing `step_command` `nargs="*"` positional
  (interleaved with `--objective`/`--timeout`/`--state-db`, argparse resolves
  it inconsistently across 3.11 vs 3.12 regardless of `add_argument()` order);
  now parse `add-step` with `parser.parse_known_args()` and manually assemble
  the trailing command from leftover tokens (stripping one leading `--`).
  Every other subcommand still hard-rejects leftover tokens. Added Plan 0043,
  Decision 0007, 3 new regression tests, and refreshed the index.
- Implementation commit: `594003d`; pushed to `origin/main`; issue #37
  auto-closed by the commit's `Closes #37`; verification comment posted
  separately.
- Verification: `pytest -q tests/test_run_cli.py` — 92 passed (Python
  3.11.15), 89 passed (Python 3.12.13); `pytest -q` full suite — 239 passed
  under both versions; incremental index build (17 files, 377 symbols, 2098
  relationships); `index check` current; `git diff --check` clean; pushed CI
  run succeeded (https://github.com/r2r0garza/codex-agentic-os/actions/runs/29188821906),
  same workflow/job that failed pre-fix.
- Blocked review: no open issues labeled `blocked`.
- Resulting queue: 5 unblocked `agent-ready` issues — #34 and #36
  (priority:2), #25, #26, and #35 (priority:3). Recommended next: #34 or #36
  (oldest priority:2 issues; #34 created first).
- Final target state: `main`, pushed to `origin/main`; worktree clean after
  the durable MEMORY.md commit.

---

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
