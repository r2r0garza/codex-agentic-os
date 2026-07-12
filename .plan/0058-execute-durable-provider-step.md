# Plan 0058: Execute Durable Provider Step

## Status
Complete

## Goal
Execute a queued provider-message step exactly once through its configured adapter and
durably persist the normalized response through the existing run lifecycle.

## Tasks
- [x] Dispatch command and provider-message steps through their existing boundaries.
- [x] Preserve optional provider-message values in adapter resolution and chat requests.
- [x] Persist successful normalized responses and retain command execution behavior.
- [x] Verify injected-adapter runtime and CLI behavior, contention, and the full suite.

## Resume Notes
Selected queue issue: #52. `execute_next_step()` now resolves persisted model inputs
through an injected adapter boundary, builds the provider-neutral chat request, and
persists normalized content/model/raw output. The CLI selects the built-in provider
configuration and requires a sandbox only for command steps. Focused runtime/CLI tests
(217 passed), full suite (349 passed), contention coverage, index rebuild/check, Python
compilation, and `git diff --check` pass. Provider-failure terminal semantics remain
assigned to issue #53.
