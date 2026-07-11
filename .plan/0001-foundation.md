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
The deterministic repository index now has its language-neutral contract, Git-backed tracked-file discovery and hashing, Python AST extraction, and byte-equivalent clean and incremental artifact builds. Next run should choose exactly one remaining unchecked task; Plan 0002's CLI command task is the natural continuation. The full local pytest suite passes with 24 tests. Follow the README credential policy: create `.env.example` only when configuration is needed, keep `.env` untracked, and explicitly tell the user what to populate and which live checks could not run without keys.
