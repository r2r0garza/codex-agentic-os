# Plan 0081: Worker Skip Ineligible Work and Idle Deterministically

## Status
Complete

## Goal
Make `run_worker` treat an approval-required or context-unresolved step as
non-dispatchable work rather than an uncaught failure: leave the blocked step
and run untouched, move on to another assigned or claimable run, and idle for
exactly one `poll-interval` sleep when no eligible work remains instead of
busy-spinning on the same blocked run.

## Tasks
- [x] Catch `ApprovalRequiredError` and `ContextReferencesUnresolvedError` from
      `RunCoordinator.execute_next_step` inside the worker's per-run step
      loop instead of letting them propagate; both are raised by
      `start_next_step` before any durable mutation, so no cleanup is needed.
- [x] Track a per-invocation set of run ids known to be blocked this poll
      cycle and exclude them when selecting the next assigned/claimable run,
      so the worker moves on to other eligible work immediately (no sleep)
      when it exists.
- [x] Broaden `_claim_eligible_run`'s "already assigned to this agent" match
      from `RunStatus.QUEUED` only to any non-terminal status, so a run that
      was blocked mid-execution (already `RUNNING` from an earlier succeeded
      step) remains visible to the worker on a later poll cycle instead of
      being silently abandoned.
- [x] When no unblocked run is selectable, sleep exactly one `poll-interval`
      via the injected sleeper and clear the blocked-run set so a step that
      becomes eligible out of band (an operator approval, or an earlier
      context step finishing) is retried on the next cycle.
- [x] Add focused tests: approval-blocked run skipped in favor of another
      eligible run, unresolved-context-reference run skipped without
      mutation, deterministic idle (bounded sleep calls, no exception) when
      the only eligible run is blocked, and resuming a blocked run on a later
      poll cycle after out-of-band approval.
- [x] Update the `worker run` DEVELOPMENT.md section to describe the new
      skip/idle behavior and drop the "does not yet skip" caveat.
- [x] Run the full suite, rebuild/check the index, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #83, tied with #84 on `priority:2`; broken
by oldest creation time (#83 created one second before #84). #84 and #85
remain open in Sprint 12.

Implementation complete. `execute_next_step` already raises both gate errors
strictly before any `put_many`/`transition_step` call, so catching them in
the worker requires no explicit rollback. The blocked-run exclusion set is
scoped to one `run_worker` invocation's poll cycle: it accumulates run ids
that failed to dispatch, is consulted by `_claim_eligible_run`, and is
cleared the moment the worker actually sleeps (either because it found no
eligible run, or because a claimed run's inner loop ended without
progressing or blocking) — so every distinct known-blocked run is tried
at most once per idle sleep, not on every iteration, and any given run is
retried again after the very next sleep. Broadening the "assigned run" match
to include `RunStatus.RUNNING` was necessary because a run that already
completed one or more steps before hitting a blocked step is `RUNNING`, not
`QUEUED`, and the original filter would have made it permanently invisible
to the worker after the first block — `claim_next` never touches an
already-claimed `RUNNING` run, so nothing else would have picked it back up
either.
