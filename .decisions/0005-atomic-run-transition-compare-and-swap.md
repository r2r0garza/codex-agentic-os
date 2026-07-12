# Decision 0005: Compare-and-swap durable run transitions

## Status
Accepted

## Context
`RunCoordinator.transition()` read the current run with `self.get()`, validated the
lifecycle edge in Python, and then called `StateStore.put()` — an unconditional
insert-or-replace that always increments the revision from whatever it currently reads.
Between the read and the write there is no lock: two coordinators racing to transition
the same run (for example, both observing `QUEUED` and both moving it to `RUNNING`)
could both succeed, with the second silently overwriting the first's revision instead of
failing. `claim_run`, `release_run_claim`, and `claim_next_run` already avoided this by
resolving their business check inside one `BEGIN IMMEDIATE` transaction; `transition()`
did not have an equivalent.

## Decision
Add `StateStore.transition_run(run_id, *, expected_status, expected_revision, status,
payload)`. It opens one `BEGIN IMMEDIATE` transaction, re-reads the run's current status
and revision, and only writes when both match the caller-supplied expectations;
otherwise it raises `StateConflictError` and leaves the row untouched. A missing run
raises `KeyError` before any write is attempted.

`RunCoordinator.transition()` keeps its existing lifecycle-edge and terminal-output
validation against the snapshot from `self.get()`, then passes that same snapshot's
status and revision as the expected values to `transition_run()`. If a competing writer
already changed the run since the snapshot was read, the expected values won't match the
transaction's fresh read and the call fails with `ValueError` ("run transition
conflict") instead of silently clobbering the newer state.

## Consequences
- A valid, uncontested transition behaves exactly as before: one call, one new
  revision, the same lifecycle and output contract.
- Two coordinators racing on the same run can no longer both succeed or overwrite each
  other; the loser gets a clear `ValueError` and can re-read and retry.
- The initial snapshot read (`self.get()`) is still outside the transaction, so the
  compare-and-swap only guards the write itself — this matches the existing
  `claim_run`/`release_run_claim` pattern and is sufficient because the transactional
  compare is what prevents mutation, not the read.
- `cancel()`, `start_next_step()`, and step transitions are unchanged; the equivalent
  atomicity gap for step transitions is a separate, already-queued issue (#29).
