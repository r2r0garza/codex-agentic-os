# Plan 0115: Native Provider Tool Payloads

## Status
Complete

## Goal
Map durable provider-step tool declarations into the native tool/function
request shape for every supported adapter family while leaving no-tool
requests unchanged.

## Tasks
- [x] Extend the provider-neutral chat request with command-free tool metadata.
- [x] Map tools into OpenAI-compatible, Anthropic, and Google request bodies.
- [x] Pass durable step declarations into runtime provider dispatch.
- [x] Add offline transport and no-tool regression tests.
- [x] Run focused/full verification and refresh the index.

## Resume Notes
Selected active-milestone issue: #127 (Sprint 21 "Durable model tool
calling", priority:1, `agent-ready`), the sole unblocked issue. #128 remains
blocked on #127 and #129 remains blocked on #128.

The provider-neutral `ChatToolDeclaration` deliberately excludes the durable
command template. Runtime dispatch translates each persisted declaration into
name, optional description, and optional parameters only, so provider transports
cannot observe sandbox commands. Missing parameters map to the same empty object
schema for all adapters. Explicit non-object roots and non-object `properties`
fail before transport because they cannot satisfy the shared function-input
contract.

OpenAI-compatible adapters emit `tools[].function`, Anthropic emits native
`tools[]` with `input_schema`, and Google emits one `tools[]` entry containing
ordered `functionDeclarations`. Every adapter adds its field only when tools are
present, leaving existing no-tool request bodies byte/shape compatible.

Verification: 22 focused tool/provider-dispatch tests passed; full `pytest` passed
822; index rebuilt to 27 files / 1176 symbols / 7207 relationships; `index check`
and `git diff --check` passed.
