# Plan 0069: Failed-Step Retry Eligibility Inspection

## Status
Complete

## Goal
Let operators distinguish definite command/provider failures from uncertain recovered
outcomes through the existing read-only run and step inspection surfaces.

## Tasks
- [x] Add pure runtime failure classification derived from durable failure markers.
- [x] Expose failure kind and retry eligibility for failed steps in CLI inspection.
- [x] Cover definite command/provider failures, uncertain recovery, and non-failed steps.
- [x] Run focused and full verification, refresh the index, and record the durable run.

## Resume Notes
Selected active-milestone issue: #67. This plan is limited to computed read-only
classification; it does not create attempts, mutate failed runs, or add retry commands.
`RunStep.failure_kind` and `retry_eligible` derive the view from existing output and
execution-input markers; no persistence schema changed. Resume with the next prioritized
unblocked `agent-ready` issue in Sprint 8.
