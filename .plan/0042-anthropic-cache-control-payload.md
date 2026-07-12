# Plan 0042: Correct Anthropic cache-control payload

## Status
Complete

## Goal
Ensure native Anthropic Messages API requests contain only supported top-level fields.

## Tasks
- [x] Remove the invalid top-level `cache_control` field from `AnthropicAdapter.complete()`.
- [x] Preserve existing system, message, sampling, token, endpoint, and header behavior.
- [x] Update the exact-payload test to require that `cache_control` is absent.
- [x] Run focused and full test suites and refresh the repository index.

## Resume Notes
Issue #33 is complete. `AnthropicAdapter.complete()` no longer attempts prompt caching through an unsupported top-level request field. Block-level Anthropic prompt caching remains outside this plan's scope and should require a separately specified issue.
