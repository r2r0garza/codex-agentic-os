# Automation Memory

- Run: 2026-07-12T18:22:04Z — replenishment run.
- Active milestone: Sprint 4 "Durable model-backed step execution" (#4). It had no issues and its explicit exit criteria had uncovered implementation work, so no code was implemented this run.
- Created three milestone-scoped issues: #51 (queue and inspect durable provider-message steps, priority:1, `agent-ready`), #52 (execute durable model steps through provider adapters, priority:2, blocked on #51), and #53 (preserve run state across provider failures and mixed steps, priority:3, blocked on #51/#52).
- Evidence: VISION and milestone contract reviewed; committed code index was current (20 files, 479 symbols, 2635 relationships); relevant runtime, chat-adapter, CLI, persistence, and sandbox boundaries inspected. The queue maps directly to missing-message rejection, successful durable send, provider-failure semantics, and mixed command/model regression criteria.
- Verification: GitHub confirms exactly three open Sprint 4 issues with one ready issue and two concretely dependency-blocked issues; repository code was not changed; `codex-agentic-os index check` passed before replenishment.
- Blocked review: #52 and #53 remain correctly blocked by the explicit predecessor contracts; no blocker was resolved and no labels changed.
- Roadmap horizon: 3 open milestones before and after (Sprint 4, Sprint 5, Sprint 6), so no planning run was needed.
- Final target: `main`; next eligible issue is #51. GitHub issue creation complete; MEMORY commit/push pending; worktree dirty only for this durable record.

---

- Run: 2026-07-12T18:20:00Z — implementation, retrospective, and roadmap-horizon maintenance.
- Active milestone at selection: Sprint 3 "Observable durable agent identities" (#3). Selected and closed its sole unblocked `agent-ready` issue, #46 (read-only agent inspection CLI, priority:3).
- Completed: added `AgentRegistry.get()` and `agent inspect AGENT_ID [--state-db PATH]`. Inspection opens the state database read-only, emits the standard agent JSON payload, supports legacy `last_seen: null`, rejects missing databases, unknown agents, and empty identifiers cleanly, and preserves record revision/liveness. Added Plan 0056, DEVELOPMENT guidance, and refreshed the committed index.
- Implementation commit `34e77ed` pushed to `origin/main`; issue #46 closed with verification evidence.
- Verification: focused agent suite (36 passed); full suite (346 passed); index rebuilt/current (20 files, 479 symbols, 2635 relationships); `git diff --check` clean. Direct operator UAT passed for registration, heartbeat, stable listing, repeated byte-identical read-only inspection, legacy inspection, unknown identity rejection, and creation/inspection of a run owned by the registered identity.
- Retrospective: created and closed issue #50 after every Sprint 3 exit criterion passed. No architecture decision changed and no remediation was required; Sprint 3 was closed.
- Blocked review: no open `blocked` issues; nothing changed.
- Roadmap horizon: 3 open milestones (Sprint 3, Sprint 4, Sprint 5) before closure, then 2. Invoked `codex-agentic-os-plan-sprints` and created Sprint 6 "Operator approval-gated execution" (#6, future, no issues), restoring exactly 3 open milestones: Sprint 4, Sprint 5, Sprint 6. No issue was created outside the active milestone.
- Resulting active queue: Sprint 4 has no issues yet and requires a replenishment run derived from its durable model-backed step execution exit criteria; there is no eligible implementation issue until replenishment.
- Final target: `main`; issue #46 and retrospective #50 closed; Sprint 3 closed; Sprint 6 created; implementation pushed. This MEMORY update is the remaining durable record to commit and push.

---

- Run: 2026-07-12T18:15:06Z — retrospective run plus roadmap-horizon maintenance.
- Active milestone at selection: Sprint 2 "Reproducible sandbox execution context" (#2). All implementation issues were already closed, so this run performed no implementation.
- Retrospective: created and closed issue #49 after every user-visible exit criterion passed. Direct operator UAT confirmed exact Docker and Podman composition of mounts, environment, absolute workdir, image, and isolated (`--network none`) versus explicitly enabled (`--network bridge`) policy. Invalid image, mount, environment, and workdir regression cases preserve queued run/step state before claim. No remediation milestone was required; Sprint 2 was closed.
- Verification: full suite (336 passed); composed Docker/Podman command-construction UAT passed in both network modes; `run execute-next --help` confirmed the explicit opt-in and isolated default; index current (20 files, 470 symbols, 2572 relationships); `git diff --check` clean.
- Architecture/documentation: existing `SandboxSpec` / `ContainerSandbox` validation and deterministic rendering boundary remains intact; no decision change required. DEVELOPMENT already documents the completed operator workflow.
- Blocked review: no open `blocked` issues; nothing changed.
- Roadmap horizon: 3 open milestones (Sprint 2, Sprint 3, Sprint 4) before retrospective closure, then 2. Invoked `codex-agentic-os-plan-sprints` and created Sprint 5 "Auditable mixed-step run history" (#5, future, no issues), restoring exactly 3 open milestones: Sprint 3, Sprint 4, Sprint 5. No issue was created outside the active milestone.
- Resulting active queue: Sprint 3 has one unblocked `agent-ready` priority:3 issue, #46 (read-only agent inspection CLI), which is the next eligible implementation issue.
- Final target: `main`; retrospective issue and Sprint 2 milestone closed; Sprint 5 created; durable run record commit `08aabb1` pushed to `origin/main`; worktree clean after recording.

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
