# Plan 0057: Durable Provider Message Step

## Status
Complete

## Goal
Allow an operator to queue and inspect exactly one provider-neutral model input on a
durable step without invoking a provider.

## Tasks
- [x] Persist and reconstruct validated provider-message fields.
- [x] Extend `run add-step` and inspection JSON for model steps.
- [x] Reject missing and ambiguous execution inputs before state mutation.
- [x] Verify full test suite and update pre-existing tests for the retired
      command-less "coordination-only" step (steps now require exactly one of
      command or provider message).

## Resume Notes
Closed queue issue: #51. Durable steps now require exactly one command or provider
message. The activated `.venv` previously lacked pytest and the project entry point;
both installed successfully once given enough time over a slow network connection.
Running the full suite surfaced that the "exactly one" validation broke 67
pre-existing tests relying on the retired command-less coordination-only step; those
tests were updated to supply a command or provider message (or, for the three tests
that explicitly exercised the retired feature, rewritten to test the new rejection
and mixed command/message-step behavior instead). Full suite (348 passed), direct CLI
UAT (create, add-step with provider-message flags, inspect-step, inspect, rejection of
a step missing both command and message, persistence across a new process), index
rebuild, and `git diff --check` all pass. Provider execution and failure handling
remain out of scope (issues #52/#53).
