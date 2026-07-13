# Plan 0071: CLI Failed-Step Retry

## Status
Complete

## Goal
Let an operator explicitly retry an eligible failed step from the CLI, trace both
attempts after restart, and complete the retried run through existing execution paths.

## Tasks
- [x] Add a CAS-explicit `run retry-step` command using the runtime retry operation.
- [x] Present durable retry lineage from both the failed and new attempts.
- [x] Exclude only durably superseded failed attempts from terminal run completion.
- [x] Cover rejection, approval, execution, restart reconstruction, and history paths.
- [x] Run focused and full verification, refresh the index, and record the durable run.

## Resume Notes
Selected active-milestone issue: #69. The implementation is limited to the explicit
CLI surface, lineage inspection, and the completion condition required for a retried
attempt to reach a terminal run outcome. Automatic retry, scheduling, and approval
bypass remain out of scope.

Implementation complete for #69. Fresh-process CLI verification preserved the original
failed attempt, linked the queued retry in both inspection directions, executed the
same persisted command successfully after external state changed, and reconstructed
the terminal `succeeded` outcome through durable history.
