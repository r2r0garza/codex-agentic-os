# codex-agentic-os

codex-agentic-os is an agentic operating system project: a durable home for agents that can plan, execute, sandbox work, remember decisions, and route model calls across multiple providers.

## Direction

This repository is being maintained as an incremental, scheduled build. Each run should tackle one focused plan item, update the project record, and leave the repo in a resumable state.

## First-class model providers

The OS is intentionally provider-neutral. The foundation declares support for:

- OpenAI
- Anthropic
- Google
- OpenRouter
- LM Studio
- Ollama
- OpenAI-compatible endpoints

Provider declarations live in `src/codex_agentic_os/providers.py`. Concrete adapters will be added behind this interface as the runtime matures.

## Agent runtime strategy

The initial runtime is a small internal core with typed boundaries. This avoids overfitting too early while leaving room to add adapters for orchestration frameworks such as LangChain DeepAgents when a plan calls for them.

Runtime declarations live in `src/codex_agentic_os/runtime.py`.

## Sandbox execution

Sandboxing is a required capability. The first supported container backends are:

- Docker
- Podman

Sandbox declarations live in `src/codex_agentic_os/sandboxes.py`. Execution adapters are planned next.

## Planning and decisions

- `.plan/` stores active and historical implementation plans.
- `.decisions/` stores architectural decision records explaining why choices were made.
- `.github/workflows/hourly-agentic-os.yml` defines an hourly heartbeat workflow that identifies the next unchecked plan task.

## Current status

Implemented foundation:

- Python package metadata and CLI entrypoint.
- Provider family declarations and tests.
- Docker/Podman sandbox declarations and tests.
- Plan and decision records.
- Hourly GitHub Actions heartbeat.
- Provider-neutral chat request/response types and an injectable OpenAI-compatible adapter.
- Native Anthropic Messages API adapter with system-message normalization and prompt caching.

Verification note: the full local pytest suite passes.

Planned next:

1. Native Google chat adapter.
2. Docker and Podman sandbox execution adapters.
3. Persistent state for agent runs, plans, and decisions.

## Development

### Provider credentials

Provider integrations must remain independently usable: a missing API key for one provider must not block development or tests for other providers. Prefer injected transports and offline tests; require live credentials only for explicit integration or end-to-end verification.

When a task first requires environment-based configuration, add or update a committed `.env.example` containing placeholder values and ensure `.env` is ignored by Git. Do not commit secrets. The session performing that work must tell the user to copy `.env.example` to `.env`, identify which variables are required or optional, and clearly report any verification skipped because a key is unavailable.

Install locally:

```bash
python -m pip install -e '.[dev]'
```

Run tests:

```bash
pytest
```

Inspect declared capabilities:

```bash
codex-agentic-os
```
