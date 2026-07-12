# Plan 0040: Coordination-Only Step Creation CLI

## Status
Complete

## Goal
Let an operator append a durable queued step with only an identifier and objective,
exposing `RunCoordinator.add_step()`'s existing support for command-less steps through
`run add-step`.

## Tasks
- [x] Make `step_command` optional on `run add-step` (`nargs="*"`), defaulting to an
      empty list that is normalized to `command=None`.
- [x] Preserve command-bearing step creation and positive-timeout validation.
- [x] Verify objective-only creation, mixed ordered objective-only/command-bearing
      steps, timeout-without-command rejection, durable read-back, and existing
      non-mutation rejection paths.

## Resume Notes
Selected queue issue: #31. `RunCoordinator.add_step()` and `_validate_command()` already
rejected a timeout without a command and accepted `command=None`; only the CLI's
positional `step_command` argument forced at least one command token. No durable step
schema or execution behavior changed. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.
