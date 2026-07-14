# Plan 0095: CLI Run Watch Polling Command

## Status
Complete

## Goal
Give an operator a read-only `run watch RUN_ID --interval SECONDS` CLI
command that polls durable run history on an explicit interval and prints
each new entry exactly once in sequence order, continuing until the run
reaches a terminal status or the operator interrupts, without any new
persistence or transport.

## Tasks
- [x] Add the `run watch RUN_ID --interval SECONDS` parser surface, reusing
      the existing database/run validation behavior of read-only inspection
      commands and rejecting a non-positive interval before opening or
      mutating state.
- [x] Implement an in-process polling loop (`_watch_run`) that reuses
      `RunCoordinator.list_history`/`list_steps` only: each history entry is
      emitted once per session by tracking its durable sequence number, and a
      step blocked on a pending approval is surfaced once as a distinct
      `blocked` event rather than repeated every poll; the loop stops as soon
      as the run reaches a terminal status.
- [x] Reuse the worker loop's SIGINT/SIGTERM shutdown-signal pattern
      (`_install_worker_shutdown_signals`) so an operator interrupt stops the
      watch loop cleanly at its next poll boundary and restores the process's
      prior signal disposition.
- [x] Add focused CLI/runtime tests for ordering, no duplication across polls,
      terminal exit, interval validation, missing database/run rejection,
      the approval-blocked notice, and non-mutation of run/step/history/
      approval/artifact/usage/agent state.
- [x] Document the new command in DEVELOPMENT.md.

## Resume Notes
Selected active-milestone issue: #101 (Sprint 16, priority:1, agent-ready, no
blocker). This closes Sprint 16's primary exit-criteria gap for live run
observation.

`_watch_run` is a thin polling wrapper over the already-durable
`list_history`/`list_steps` read paths (Plans 0060/0062), so no new
persistence or transport was introduced, matching the milestone's scope
boundary (no push transport, event bus, websockets, metrics, or multi-run
dashboards). The pending-approval blocked-state check reuses the exact
next-queued-step/`ApprovalStatus.PENDING` condition `start_next_step` already
enforces at dispatch (`runtime.py`), so "blocked" in the watch view means
precisely what would raise `ApprovalRequiredError` on dispatch. Output is one
compact JSON object per line (not the pretty-printed block used by snapshot
commands like `run inspect`), since a live/streaming command's output is
naturally read line-by-line rather than as one document. Interruption reuses
`_install_worker_shutdown_signals`, the same SIGINT/SIGTERM-to-`should_continue`
adapter `worker run` already relies on, rather than introducing a second
signal-handling idiom.
