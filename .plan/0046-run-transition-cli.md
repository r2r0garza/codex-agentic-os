# Plan 0046: Operator Run Transition CLI

## Status
Complete

## Goal
Expose explicit, validated durable run lifecycle transitions without sandbox execution.

## Tasks
- [x] Add `run transition RUN_ID STATUS` for coordinator-supported lifecycle edges.
- [x] Parse optional terminal output as a JSON object before any state mutation.
- [x] Print the standard run payload with steps in durable position order.
- [x] Verify valid terminal paths and rejection without mutation.

## Resume Notes
Selected queue issue: #25. `run transition` delegates lifecycle and terminal-output
validation to `RunCoordinator.transition()`, while its CLI boundary rejects malformed
or non-object JSON before mutation. It adds no aliases, retries, automatic step
transitions, or claim behavior. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.
