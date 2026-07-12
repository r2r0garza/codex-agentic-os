# Decision 0006: Compare-and-swap explicit durable step transitions

## Status
Accepted

## Context
`RunCoordinator.transition_step()` validated a step snapshot and then used an
unconditional upsert, allowing a competing coordinator to overwrite a newer revision.

## Decision
Persist explicit step lifecycle transitions with a SQLite `BEGIN IMMEDIATE`
compare-and-swap on the snapshot's status and revision. Preserve existing lifecycle and
output validation before persistence, and report a stale write as a transition conflict.

## Consequences
- Only one competing transition from a shared prior revision can commit.
- Successful transitions still advance exactly one revision and preserve terminal output.
- Parent runs and sibling steps remain outside the explicit step-transition mutation.
- Multi-record completion, recovery, cancellation, and dispatch retain their existing
  specialized atomic boundaries.
