# Decision 0004: Require explicit endpoints for generic OpenAI-compatible providers

## Status
Accepted

## Context
`OpenAICompatibleAdapter` served every OpenAI-compatible provider kind (`OPENAI`,
`OPENROUTER`, `LM_STUDIO`, `OLLAMA`, `OPENAI_COMPATIBLE`) through one fallback:
an omitted `base_url` always resolved to `https://api.openai.com/v1`. That default is
correct for `OPENAI` itself, but for `OPENAI_COMPATIBLE` — a spec meant to target an
arbitrary self-hosted or proxied endpoint — it let a misconfigured spec silently send
requests, and the model's credentials, to OpenAI's public API instead of failing loudly.
`LM_STUDIO` and `OLLAMA` have well-known standard local server ports and are meant to
work with zero configuration, so they need a default too, just not OpenAI's.

## Decision
Split the adapter's base-URL resolution by provider kind instead of one shared fallback:

- `OPENAI_COMPATIBLE` requires an explicit `base_url`; `complete()` raises `ValueError`
  before constructing a request or invoking the transport when it is missing.
- `LM_STUDIO` and `OLLAMA` fall back to their standard local base URLs
  (`LM_STUDIO_DEFAULT_BASE_URL` / `OLLAMA_DEFAULT_BASE_URL`, defined once in
  `providers.py` and reused by both the adapter and `DEFAULT_PROVIDER_SPECS`) when
  `base_url` is omitted.
- `OPENAI` keeps the original `https://api.openai.com/v1` fallback.
- Follow-up issue #32 defines `OPENROUTER_DEFAULT_BASE_URL` as
  `https://openrouter.ai/api/v1` and reuses it in the registry and adapter fallback;
  explicit OpenRouter `base_url` values still take precedence.
- `api_key_env` remains optional for every kind: an unset variable, or one with no
  value, omits the Authorization header rather than failing.

## Consequences
- A generic OpenAI-compatible spec without a `base_url` fails fast and cannot leak
  requests to a provider it was never configured for.
- LM Studio and Ollama remain zero-configuration for local use while still supporting an
  authenticated remote or proxied deployment through the same `api_key_env` mechanism.
- OpenRouter requests now target OpenRouter by default while retaining explicit endpoint
  overrides and the shared optional-credential behavior.
