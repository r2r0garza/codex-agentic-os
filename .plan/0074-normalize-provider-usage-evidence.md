# Plan 0074: Normalize Provider Usage Evidence

## Status
Complete

## Goal
Give the chat/runtime boundary a small provider-neutral usage-evidence shape
so every adapter reports normalized input/output token counts, or an
explicit unavailable marker, without changing existing content/model/raw
behavior.

## Tasks
- [x] Add a frozen `ChatUsage` value (`available`, `input_tokens`,
      `output_tokens`, `raw`, `unavailable_reason`) to `chat.py` and attach it
      to `ChatResponse` with a default that marks usage unavailable.
- [x] Extract usage from the OpenAI-compatible `usage` block
      (`prompt_tokens`/`completion_tokens`), the Anthropic `usage` block
      (`input_tokens`/`output_tokens`), and the Google `usageMetadata` block
      (`promptTokenCount`/`candidatesTokenCount`).
- [x] Degrade explicitly to `ChatUsage(available=False, unavailable_reason=...)`
      when a response carries no usage block or the expected counts are
      missing/malformed, without failing the request.
- [x] Cover all three families and their unavailable paths with offline
      transport tests in `tests/test_chat.py`.
- [x] Export `ChatUsage` from the package root alongside the existing chat
      types.

## Resume Notes
Selected active-milestone issue: #74 (Sprint 10, priority:1, agent-ready, no
blockers). This issue only normalizes usage evidence at the adapter
boundary; it does not persist usage into durable step output (#75) or add
`run usage` (#76), both blocked on this one.

Implementation complete. `ChatResponse.usage` is always present. Existing
stub adapters in `test_runtime.py`/`test_run_cli.py` that construct
`ChatResponse` without a `usage` argument now get the explicit unavailable
default, matching current behavior since those tests predate usage evidence
and do not assert on it.
