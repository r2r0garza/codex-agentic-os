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

Verification note: source compilation and an injected transport smoke test pass locally; the full pytest suite is pending in an environment where the dev dependency can be installed.

Planned next:

1. Native Anthropic and Google chat adapters.
2. Docker and Podman sandbox execution adapters.
3. Persistent state for agent runs, plans, and decisions.

## Development

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
