# Plan 0017: Atomic Running-step Recovery

## Status
Complete

## Goal
Ensure explicit recovery of an uncertain running command cannot fail its durable step without also failing its run.

## Tasks
- [x] Commit recovered failed-step and failed-run updates in one transaction.
- [x] Preserve interrupted and timed-out recovery metadata and optional detail.
- [x] Prove an injected mid-batch failure rolls back both updates.

## Resume Notes
Selected queue issue: #8. `RunCoordinator.recover_running_step()` now prepares the
failed step and run records and commits them together through `StateStore.put_many()`.
Both revisions advance exactly once on success, while a failure during either write
leaves the original running records intact. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.
