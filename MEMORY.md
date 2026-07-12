# Automation Memory

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

---

- Run: 2026-07-12T13:35:50Z — implementation run.
- Selected issue: #39, chat CLI command wiring `chat.py` provider adapters.
- Completed: added `codex-agentic-os chat send --provider KIND [--model]
  [--base-url] [--api-key-env] [--temperature] [--max-tokens] MESSAGE`, building a
  `ProviderSpec` that falls back to the matching `DEFAULT_PROVIDER_SPECS` entry for
  `model`/`base_url`/`api_key_env` when a flag is omitted, then prints
  `adapter_for(spec).complete(...)` as JSON. Empty message and unknown `--provider`
  are rejected via argparse/`parser.error` before any network call. Extended
  `main()`'s exception handling to also catch `RuntimeError` so adapter/transport
  failures surface as a clean CLI error (also fixes the previously-uncaught
  `ContainerSandbox` "backend is not installed" path on `run execute-next`). Added
  Plan 0047, `tests/test_chat_cli.py` (compatible/Anthropic/Google success paths,
  malformed-input rejection, adapter-error surfacing — all via an injected fake
  `codex_agentic_os.chat.urlopen`, no live network calls), a DEVELOPMENT.md example,
  and refreshed the index. No live API key was available, so only the
  offline/injected-transport path was verified, per DEVELOPMENT.md's
  provider-credential policy.
- Implementation commit: `4f8ea3b`; pushed to `origin/main`; issue #39 auto-closed
  by the commit's `Closes #39`; verification comment posted separately.
- Verification: `pytest -q` (288 passed); clean index build (18 files, 414 symbols,
  2334 relationships); `codex-agentic-os index check` current; `git diff --check`
  clean.
- Blocked review: no open issues labeled `blocked`; nothing to re-evaluate.
- Resulting queue: 4 unblocked `agent-ready` issues — #26, #35, #40, and #41 (all
  priority:3). Below the 5-10 target band; recommend backlog replenishment soon.
  Recommended next: #26, the oldest priority:3 issue.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.

---

- Run: 2026-07-12T12:35:50Z — implementation run.
- Selected issue: #38, validate run agent references against the durable agent registry.
- Completed: `RunCoordinator.create()`, `claim()`, and `claim_next()` now reject
  unknown agent ids before mutation while preserving unassigned creation. Existing
  runtime/CLI fixtures register their intended identities; focused registered and
  unregistered success/rejection coverage was added. Updated Plan 0045,
  DEVELOPMENT.md, and the committed code index.
- Implementation commit: `d5c02b3`; pushed to `origin/main`; issue #38 closed with
  verification results.
- Verification: `pytest -q` (281 passed); clean index build (18 files, 413 symbols,
  2311 relationships); `codex-agentic-os index check` current; `git diff --check`
  clean.
- Blocked review: no open issues labeled `blocked`; nothing to re-evaluate.
- Resulting queue: 5 unblocked `agent-ready` issues — #39 (priority:2), #26, #35,
  #40, and #41 (priority:3). Recommended next: #39, the only priority:2 issue.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.
