# Plan 0047: Chat Send CLI

## Status
Complete

## Goal
Expose the existing provider-neutral `chat.py` adapters (`OpenAICompatibleAdapter`,
`AnthropicAdapter`, `GoogleAdapter`, `adapter_for`) through a CLI surface so an operator
or scripted agent can send one message through any declared provider without writing
Python.

## Tasks
- [x] Add `codex-agentic-os chat send --provider KIND [--model] [--base-url]
      [--api-key-env] [--temperature] [--max-tokens] MESSAGE`, building a `ProviderSpec`
      that falls back to the matching `DEFAULT_PROVIDER_SPECS` entry for
      `model`/`base_url`/`api_key_env` when a flag is omitted.
- [x] Reject an empty message and an unknown `--provider` value via
      `parser.error`/argparse choices before any network call.
- [x] Surface adapter errors (HTTP failure, malformed provider response) as a clean CLI
      error instead of a raw traceback by extending `main()`'s top-level exception
      handling to catch `RuntimeError` alongside the existing `ValueError`.
- [x] Print `ChatResponse` as JSON (`content`, `model`, `raw` when present).
- [x] Add CLI tests covering a compatible provider, Anthropic, and Google, injecting a
      fake `urlopen` (no live network calls) and covering both malformed-input rejection
      paths and a clean adapter-error surface.

## Resume Notes
Selected queue issue: #39. `chat send` reuses `_urlopen_transport` unchanged; no new
transport abstraction was added, per the issue's bounded scope. Multi-turn history,
streaming, and tool-calling wiring remain explicitly out of scope. Catching
`RuntimeError` in `main()`'s exception handler is a general CLI improvement (it also
now cleanly surfaces `ContainerSandbox`'s "backend is not installed" `RuntimeError` from
`run execute-next`, previously an uncaught traceback) — no existing test relied on that
error propagating uncaught through `main()`. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.

## Follow-up: Issue #43 — `--system TEXT` option
Added optional `chat send --system TEXT`, reusing the existing per-adapter system-message
mapping in `chat.py` (`AnthropicAdapter`'s top-level `system` field, `GoogleAdapter`'s
`systemInstruction`, and `OpenAICompatibleAdapter`'s ordinary `system` role message) —
no adapter changes were needed. The CLI rejects an empty or whitespace-only `--system`
value with `ValueError` before building the provider spec or invoking the adapter,
mirroring the existing empty-message check. Omitting `--system` leaves the single
`ChatMessage("user", ...)` payload unchanged.
