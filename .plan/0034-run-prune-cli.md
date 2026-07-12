# Plan 0034: Terminal Run Prune CLI

## Status
Complete

## Goal
Expose explicit operator cleanup of one terminal durable run through the CLI, built on
the atomic prune coordinator introduced by Plan 0030.

## Tasks
- [x] Add `codex-agentic-os run prune RUN_ID` delegating to `RunCoordinator.prune()`.
- [x] Print machine-readable confirmation with the removed run identifier and step count.
- [x] Reject missing databases, missing runs, and active (queued/running) runs without
      deletion.

## Resume Notes
Selected queue issue: #19. The `prune` subcommand reuses the existing `run` argument
group's database-existence and identifier handling, checks for a missing run explicitly
before delegating (matching the `claim`/`add-step` pattern), and prints
`{"pruned": {"run_id": ..., "step_count": ...}}` instead of the standard run payload
since the run no longer exists after a successful prune. Resume with the next
prioritized unblocked `agent-ready` GitHub issue.
