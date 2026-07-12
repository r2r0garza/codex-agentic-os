# Automation Memory

- Run: 2026-07-12T18:15:06Z — retrospective run plus roadmap-horizon maintenance.
- Active milestone at selection: Sprint 2 "Reproducible sandbox execution context" (#2). All implementation issues were already closed, so this run performed no implementation.
- Retrospective: created and closed issue #49 after every user-visible exit criterion passed. Direct operator UAT confirmed exact Docker and Podman composition of mounts, environment, absolute workdir, image, and isolated (`--network none`) versus explicitly enabled (`--network bridge`) policy. Invalid image, mount, environment, and workdir regression cases preserve queued run/step state before claim. No remediation milestone was required; Sprint 2 was closed.
- Verification: full suite (336 passed); composed Docker/Podman command-construction UAT passed in both network modes; `run execute-next --help` confirmed the explicit opt-in and isolated default; index current (20 files, 470 symbols, 2572 relationships); `git diff --check` clean.
- Architecture/documentation: existing `SandboxSpec` / `ContainerSandbox` validation and deterministic rendering boundary remains intact; no decision change required. DEVELOPMENT already documents the completed operator workflow.
- Blocked review: no open `blocked` issues; nothing changed.
- Roadmap horizon: 3 open milestones (Sprint 2, Sprint 3, Sprint 4) before retrospective closure, then 2. Invoked `codex-agentic-os-plan-sprints` and created Sprint 5 "Auditable mixed-step run history" (#5, future, no issues), restoring exactly 3 open milestones: Sprint 3, Sprint 4, Sprint 5. No issue was created outside the active milestone.
- Resulting active queue: Sprint 3 has one unblocked `agent-ready` priority:3 issue, #46 (read-only agent inspection CLI), which is the next eligible implementation issue.
- Final target: `main`; retrospective issue and Sprint 2 milestone closed; Sprint 5 created; this MEMORY.md record pending commit/push; worktree otherwise clean.

---

- Run: 2026-07-12T18:05:11Z — implementation run plus roadmap-horizon maintenance.
- Active milestone at selection: Sprint 2 "Reproducible sandbox execution context" (#2). Selected and closed issue #47, the sole remaining unblocked `agent-ready` issue.
- Completed: added `run execute-next --network` as an explicit boolean opt-in mapped to the existing `SandboxSpec.network_enabled` field. `SandboxSpec`/`ContainerSandbox.command()` already implemented both network modes from issue #44's prior work; no `sandboxes.py` changes were needed. Help text states the opt-in and isolated default explicitly. Added Plan 0055, a DEVELOPMENT.md example, and refreshed the committed index.
- Implementation commit `839b190`; pushed to `origin/main`; issue #47 auto-closed by the commit's `Closes #47`; verification comment posted separately.
- Verification: focused sandbox/run CLI suite (155 passed); full suite (336 passed); index rebuilt to 20 files, 470 symbols, 2572 relationships and current; `git diff --check` clean; manual `run execute-next --help` confirms `--network` and its opt-in/isolated-default help text.
- Blocked review: no open `blocked` issues; nothing changed.
- Resulting queue: Sprint 2 has 0 open issues (2 closed) — retrospective-eligible on a future run, not run this pass (one issue per run). Sprint 3 "Observable durable agent identities" (#3) has its sole ready issue, #46 (read-only agent inspection CLI, priority:3), still open and unblocked.
- Roadmap horizon: 2 open milestones (Sprint 2, Sprint 3) before this run's post-work check, below the required 3. Created Sprint 4 "Durable model-backed step execution" (#4, 0 issues — future milestone, not yet active) via `codex-agentic-os-plan-sprints`, composing Sprint 1's provider adapters with Sprint 2's run/step CAS lifecycle to let a durable step persist a model response as output. Resulting horizon: 3 open milestones (Sprint 2, Sprint 3, Sprint 4) as required. No issues created outside the (still-forming) active milestone.
- Recommended next: run a Sprint 2 retrospective (all issues closed) before or alongside implementing Sprint 3 issue #46; #46 remains the next eligible implementation issue if the retrospective is deferred.
- Final target: `main`; implementation and this MEMORY.md update both pushed; worktree clean.

---

- Run: 2026-07-12T17:25:41Z — implementation run.
- Active milestone: Sprint 2 "Reproducible sandbox execution context" (#2). Selected issue #44, the oldest of two unblocked `agent-ready` priority:3 issues assigned to the milestone.
- Completed: added optional validated `SandboxSpec.working_dir` and `run execute-next --workdir PATH`. Docker and Podman render `--workdir` after mounts and environment variables and before the image; omission preserves the prior command. Empty, whitespace-only, and relative paths fail before a queued step is claimed. Added Plan 0054, DEVELOPMENT guidance, and refreshed the committed index.
- Verification: focused sandbox/run CLI suite (153 passed); full suite (334 passed); index rebuilt to 20 files, 466 symbols, 2558 relationships and current; `git diff --check` clean.
- Blocked review: no open `blocked` issues; nothing changed.
- Resulting queue after closure: Sprint 2 has one remaining unblocked `agent-ready` issue, #47 (explicit sandbox network opt-in, priority:3). It is the next eligible issue; retrospective is not yet eligible because #47 remains open.
- Final target: `main`; implementation, issue closure, and push pending in this run.

---

- Run: 2026-07-12T17:13:30Z — implementation plus required milestone retrospective.
- Active milestone at selection: Sprint 1 "Operator-ready provider workflow" (#1). Selected and closed issue #45, the sole unblocked `agent-ready` issue; created and closed retrospective issue #48.
- Completed: added read-only `provider credentials` output in default registry order with provider kind, `api_key_env`, and `configured`; credential-free defaults are ready, while unset or empty named variables are not. Values are never emitted, and tests prohibit network and state access. Added Plan 0053, DEVELOPMENT guidance, and refreshed the committed index.
- Implementation commit `f727845` pushed to `origin/main`; issue #45 auto-closed and received verification evidence.
- Verification: focused provider tests (5 passed); full suite (323 passed); operator UAT across all 7 defaults with configured/unset/credential-free states and sentinel non-disclosure; provider/chat offline suite (36 passed); index rebuilt to 20 files, 462 symbols, 2536 relationships and current; `git diff --check` clean.
- Retrospective #48 passed every Sprint 1 exit criterion with direct test, command, documentation, architecture, and operator evidence; no remediation required. Retrospective closed and milestone #1 closed.
- Blocked review: no open `blocked` issues; nothing changed.
- New active milestone: Sprint 2 "Reproducible sandbox execution context" (#2), with two ready priority:3 issues: #44 and #47. Recommended next: #44, oldest at equal priority.
- Final target: `main`; implementation pushed. This MEMORY update is the remaining durable record to commit and push.

---

- Run: 2026-07-12T17:05:00Z — implementation run.
- Milestone restructuring noted: the repository now uses ordered GitHub milestones as
  vertical sprints (Sprint 1 "Operator-ready provider workflow", Sprint 2 "Reproducible
  sandbox execution context", Sprint 3 "Observable durable agent identities"), all open,
  numbers 1–3, created 2026-07-12T16:41Z. The lowest-numbered open milestone (Sprint 1)
  is now the active sprint boundary; issue selection is scoped to its assigned issues
  only, not the flat repository-wide queue used by prior runs.
- Active milestone: Sprint 1 (milestone #1), objective: an operator can discover
  provider defaults, determine credential readiness without exposing secrets, and send
  a controlled system-guided single-turn request through every supported adapter
  family.
- Selected issue: #43, optional provider-neutral `chat send --system TEXT` (the older
  of the two `agent-ready` priority:3 issues assigned to Sprint 1; #45 remains).
- Completed: added `--system TEXT` to `chat send`, reusing the adapter-level
  system-message mapping already implemented in `chat.py` (Anthropic top-level
  `system` field, Google `systemInstruction`, OpenAI-compatible `system` role
  message) — no adapter changes needed. An empty or whitespace-only `--system` value
  is rejected via `ValueError` before the provider spec is built or the adapter is
  invoked, mirroring the existing empty-message check. Omitting `--system` leaves the
  existing single-message payload unchanged. Extended `.plan/0047-chat-send-cli.md`
  resume notes and added a `DEVELOPMENT.md` usage example. Refreshed the committed
  index.
- Implementation commit: `91f21fb`; pushed to `origin/main`; issue #43 auto-closed by
  the commit's `Closes #43`; verification comment posted separately.
- Verification: `pytest -q` (321 passed); `codex-agentic-os index build` then
  `codex-agentic-os index check` (current — 20 files, 459 symbols, 2525
  relationships); `git diff --check` clean; manual `chat send --help` and
  empty-`--system` rejection checked by hand.
- Blocked review: `gh issue list --label blocked` returned no results; nothing to
  re-evaluate.
- Resulting queue: Sprint 1 has 1 remaining unblocked `agent-ready` issue — #45,
  provider credential readiness reporting (priority:3). Recommended next: #45, the
  only remaining Sprint 1 issue; likely candidate for a near-term replenishment run to
  keep Sprint 1's queue in the 3–7 target band, or direct implementation if still
  agent-ready.
- Final target state: `main`, implementation pushed to `origin/main`; worktree clean
  before this durable MEMORY.md update.
