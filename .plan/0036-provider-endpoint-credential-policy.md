# Plan 0036: Provider Endpoint and Credential Policy

## Status
Complete

## Goal
Make endpoint and credential configuration explicit and safe for the OpenAI-compatible,
LM Studio, and Ollama provider kinds without changing existing OpenAI, OpenRouter,
Anthropic, or Google adapter behavior.

## Tasks
- [x] Require an explicit `base_url` for `ProviderKind.OPENAI_COMPATIBLE`; reject a
      missing `base_url` with a `ValueError` before any transport call, so it can never
      silently target the public OpenAI endpoint.
- [x] Default `ProviderKind.LM_STUDIO` and `ProviderKind.OLLAMA` to their standard local
      base URLs (`LM_STUDIO_DEFAULT_BASE_URL`, `OLLAMA_DEFAULT_BASE_URL`) when `base_url`
      is omitted, without requiring `api_key_env`.
- [x] Confirm the existing optional-credential behavior (missing env value omits the
      Authorization header; a configured value produces the bearer header) already
      applies uniformly and needs no change.
- [x] Document the endpoint/credential policy on `ProviderSpec` and reuse the same
      constants in `DEFAULT_PROVIDER_SPECS` and `chat.OpenAICompatibleAdapter`.
- [x] Add adapter tests covering explicit `OPENAI_COMPATIBLE` configuration, rejection of
      a missing `base_url` before transport invocation, and authenticated/unauthenticated
      LM Studio and Ollama requests.

## Resume Notes
Selected queue issue: #27. `OpenAICompatibleAdapter.complete()` now validates
`OPENAI_COMPATIBLE` specs before building a request and resolves `LM_STUDIO`/`OLLAMA`
base URLs through shared constants (`src/codex_agentic_os/providers.py`) instead of the
generic OpenAI fallback. `OPENAI` and `OPENROUTER` keep the prior unconditional
`https://api.openai.com/v1` fallback unchanged, since fixing OpenRouter's default was out
of this issue's bounded scope. Resume with the next prioritized unblocked `agent-ready`
GitHub issue.
