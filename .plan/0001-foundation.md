# Plan 0001: Foundation

## Goal
Create the first durable shape of codex-agentic-os: a provider-neutral agentic OS that can grow incrementally and resume work across scheduled runs.

## Tasks
- [x] Create project metadata and a Python package skeleton.
- [x] Declare first-class model-provider families: OpenAI, Anthropic, Google, OpenRouter, LM Studio, Ollama, and OpenAI-compatible endpoints.
- [x] Declare required sandbox backends: Docker and Podman.
- [x] Add an hourly scheduled workflow that gives future agents a predictable heartbeat.
- [x] Update the README with project intent and current status.
- [x] Implement a provider-neutral chat contract and OpenAI-compatible completion adapter.
- [ ] Implement native Anthropic and Google chat adapters.
- [ ] Implement sandbox command execution for Docker and Podman.
- [ ] Add persistence for plans, decisions, runs, and agent state.

## Resume Notes
Next run should choose one unchecked task above, preferably a native Anthropic adapter. The current environment could not install pytest due its package/build isolation, so rerun the suite when dependencies are available.
