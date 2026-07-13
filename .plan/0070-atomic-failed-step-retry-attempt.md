# Plan 0070: Atomic Failed-Step Retry Attempt

## Status
Complete

## Goal
Let the runtime atomically create exactly one new queued attempt for a
retry-eligible failed step, returning its run to a non-terminal executable
status, while the original failed step and its durable history stay
byte-for-byte unchanged.

## Tasks
- [x] Add `StateStore.retry_failed_step`: one `BEGIN IMMEDIATE` transaction
      that CAS-validates the failed step and failed run, inserts a new queued
      step carrying the same command/message/timeout/objective/approval
      requirement, reopens the run to `queued` without touching the original
      step, and appends one `step_retried` history entry linking both attempts
      (`retried_step_id` history column).
- [x] Add `RunCoordinator.retry_step`, which rejects non-`FAILED` and
      `uncertain` steps using the existing `failure_kind`/`retry_eligible`
      classification before any mutation, then delegates to the store.
- [x] Cover success, uncertain/non-failed rejection, stale-revision rejection,
      concurrent contention with one winner, and byte-identical original-step
      preservation at both the state and runtime layers.
- [x] Run focused and full verification, refresh the index, update
      DEVELOPMENT runtime usage guidance, and record the durable run.

## Resume Notes
Selected active-milestone issue: #68. Builds on #67's read-only
`failure_kind`/`retry_eligible` classification. Excludes a CLI command
(reserved for #69), automatic/background retries, backoff, retry budgets,
workflow branching, and compensation of external side effects. The original
failed step's revision never changes (FAILED is terminal), so exactly-one-
winner safety is enforced via the run's CAS revision, not the step's.

Known follow-up (not in scope for #68): a run reopened by retry can never
reach `succeeded` through the existing `complete_step_from_result`/
`complete_step_from_chat_response` "all steps succeeded" check, because the
superseded original failed step is permanent and never transitions to
`succeeded`. Whoever builds on this (issue #69 or later) should decide
whether run-completion logic should exclude steps superseded by a later
retry attempt. Resume with the next prioritized unblocked `agent-ready`
issue in Sprint 8.
