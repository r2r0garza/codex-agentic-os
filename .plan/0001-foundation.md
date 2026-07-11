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
- [x] Implement a native Anthropic chat adapter.
- [x] Implement a native Google chat adapter.
- [x] Begin the deterministic repository index described in Plan 0002 while the repository is still small.
- [x] Implement sandbox command execution for Docker and Podman.
- [ ] Add persistence for plans, decisions, runs, and agent state.

## Resume Notes
Docker and Podman now share a `ContainerSandbox` execution adapter that builds shell-free engine arguments, applies network isolation, read-only root filesystems, CPU and memory limits by default, captures output and exit status, supports timeouts, and reports missing engines clearly. Offline tests cover both backends and configuration overrides. Resume with the remaining Foundation task: add persistence for plans, decisions, runs, and agent state. Follow the README credential policy when later work first needs environment configuration.
