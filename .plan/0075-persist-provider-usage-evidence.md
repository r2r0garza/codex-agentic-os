# Plan 0075: Persist Provider Usage Evidence

## Status
Complete

## Goal
Persist normalized provider usage evidence with successful provider-message
step output so operators can inspect it after process restart, including an
explicit unavailable marker when the adapter supplies no usable counts.

## Tasks
- [x] Serialize `ChatResponse.usage` into successful provider step output
      without changing the existing content, model, raw, or completion behavior.
- [x] Preserve available and unavailable usage evidence through state-store
      reload.
- [x] Prove CLI inspection exposes only the normalized usage fields and does
      not introduce request, system-prompt, credential, or environment data.
- [x] Run focused and full verification, refresh the committed code index, and
      complete the durable sprint record.

## Resume Notes
Selected active-milestone issue: #75 (Sprint 10, priority:2, agent-ready, no
remaining blocker). Issue #74 established that `ChatResponse.usage` is always
present and either contains normalized counts plus the provider's raw usage
block or an explicit unavailable reason. This issue persists that evidence;
the aggregate `run usage` CLI remains reserved for #76.

Implementation complete. Every successful provider step now persists a stable
`usage` object with `available`, `input_tokens`, `output_tokens`, `raw`, and
`unavailable_reason`; the existing response `content`, `model`, and `raw`
fields and run-completion behavior are unchanged. Available and explicit
unavailable evidence both survive a new `StateStore` process, and CLI coverage
proves the added output contains no provider credential, request content, or
system prompt. Focused runtime/CLI suites, the full suite (472 passed), a
three-process SQLite UAT, incremental index rebuild/check, and
`git diff --check` pass.
