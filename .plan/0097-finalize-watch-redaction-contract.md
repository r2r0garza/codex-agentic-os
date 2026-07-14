# Plan 0097: Finalize Run Watch Redaction Contract

## Status
Complete

## Goal
Prove that `run watch`'s per-entry output and pending-approval blocked view
are a stable, machine-readable, redacted contract that never exposes raw
command arguments, resolved environment values, provider request bodies,
terminal output, or credentials, and that the blocked view is read-only.

## Tasks
- [x] Review `_watch_run`, `_watch_blocked_step`, and `_history_payload`
      against the milestone's redaction and approval-blocked exit criteria.
- [x] Add a focused regression test driving a command step (with sensitive
      command arguments and `SandboxPolicy.env_passthrough`), a completed
      step's captured terminal output, and a pending-approval provider step
      (with a sensitive message body) through `run watch`, asserting none of
      those values appear anywhere in watch output and that every emitted
      `history`/`blocked` entry's keys stay within the documented allowlist.
- [x] Run focused and full verification, refresh the committed index, and
      perform a live CLI UAT of a pending-approval run watched and
      interrupted cleanly.

## Resume Notes
Selected active-milestone issue: #103 (Sprint 16, priority:3, agent-ready;
its stated blockers #101/#102 are closed). This closes Sprint 16's remaining
exit-criteria gap: an explicit regression proving the watch output contract
never leaks sensitive execution inputs.

No runtime or CLI behavior changed. `RunHistoryEntry` (Plans 0060/0062) never
stored raw command arguments, environment values, provider bodies, or
terminal output in the first place, and `_watch_blocked_step` (Plan 0095)
only ever surfaces `step_id`/`position`/`objective`/`reason` — so the
redaction guarantee was already structural. This plan adds the regression
test the milestone's acceptance criteria call for, proving that guarantee
end to end through the public CLI rather than leaving it implicit. Full
suite: `672 passed` (up from 671, +1 net); rebuilt and confirmed the index
(24 files, 1012 symbols, 5966 relationships); `git diff --check` clean; live
CLI UAT watched a run blocked on a pending approval and confirmed the single
redacted `blocked` notice, then interrupted the watcher cleanly with SIGINT.
