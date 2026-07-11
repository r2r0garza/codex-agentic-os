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
- [ ] Implement sandbox command execution for Docker and Podman.
- [ ] Add persistence for plans, decisions, runs, and agent state.

## Resume Notes
The deterministic repository index now has its language-neutral contract, Git-backed tracked-file discovery and hashing, Python AST extraction, byte-equivalent clean and incremental artifact builds, CLI commands, optional repository-managed pre-commit refresh, and CI clean-rebuild drift verification. Continue with the active Plan 0002 rather than choosing a Foundation task: generate and commit the initial `.code-index/` artifacts as the next single focused task. Follow the README credential policy when later work first needs environment configuration.
