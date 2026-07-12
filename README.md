# codex-agentic-os

codex-agentic-os is an agentic operating system project: a durable home for agents that can plan, execute, sandbox work, remember decisions, and route model calls across multiple providers.

## Direction

This repository is being maintained as an incremental, scheduled build. Each run should tackle one focused plan item, update the project record, and leave the repo in a resumable state.

[`VISION.md`](VISION.md) defines the durable product end state and the rolling three-sprint roadmap contract used to choose vertical milestones.

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

`ProviderKind.OPENAI_COMPATIBLE` targets an arbitrary self-hosted or proxied endpoint and requires an explicit `base_url`; it is never defaulted to the public OpenAI endpoint. `ProviderKind.LM_STUDIO` and `ProviderKind.OLLAMA` default to their standard local server URLs when `base_url` is omitted and work without credentials for local use. All provider kinds treat `api_key_env` as optional: a missing or empty environment variable omits the Authorization header instead of failing.

## Agent runtime strategy

The initial runtime is a small internal core with typed boundaries. This avoids overfitting too early while leaving room to add adapters for orchestration frameworks such as LangChain DeepAgents when a plan calls for them.

Runtime declarations live in `src/codex_agentic_os/runtime.py`.

## Persistent runtime state

Plans, decisions, runs, and agent state can be stored as versioned JSON documents in a
local SQLite database through `StateStore`. Runtime databases should live under the
ignored `.codex-agentic-os/` directory; planning and architectural Markdown records
remain committed source-of-truth documentation.

## Sandbox execution

Sandboxing is a required capability. The first supported container backends are:

- Docker
- Podman

Sandbox declarations live in `src/codex_agentic_os/sandboxes.py`. Execution adapters are planned next.

## Planning and decisions

- `.plan/` stores active and historical implementation plans.
- `.decisions/` stores architectural decision records explaining why choices were made.
- `.github/workflows/hourly-agentic-os.yml` defines an hourly heartbeat workflow that reports the live `agent-ready` GitHub issue queue.

## Project status

GitHub issues labeled `agent-ready` are the execution queue and source of truth for
upcoming work. Completed architectural work is recorded in `.plan/`, with rationale in
`.decisions/`. See `MEMORY.md` for recent automation runs and resumable state.

## Development

Installation, testing, runtime usage, frontend scaffolding, and repository-index
workflows are documented in [DEVELOPMENT.md](DEVELOPMENT.md).
