# Plan 0041: OpenRouter Default Endpoint

## Status
Complete

## Goal
Route an uncustomized OpenRouter provider specification to OpenRouter's compatible chat
API without changing explicit endpoint overrides or other provider policies.

## Tasks
- [x] Define one canonical `OPENROUTER_DEFAULT_BASE_URL` in provider configuration.
- [x] Reuse the constant in `DEFAULT_PROVIDER_SPECS` and the compatible adapter fallback.
- [x] Preserve explicit OpenRouter endpoints and optional credential behavior.
- [x] Add default, override, credential, path-construction, and provider-regression tests.

## Resume Notes
Selected queue issue: #32. OpenRouter now defaults to
`https://openrouter.ai/api/v1`; explicit `base_url` values still win. OpenAI, generic
compatible, LM Studio, Ollama, Anthropic, and Google endpoint policies are unchanged.
