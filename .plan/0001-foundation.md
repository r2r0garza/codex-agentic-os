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
- [ ] Begin the deterministic repository index described in Plan 0002 while the repository is still small.
- [ ] Implement sandbox command execution for Docker and Podman.
- [ ] Add persistence for plans, decisions, runs, and agent state.

## Resume Notes
Native Google `models.generateContent` support is complete, including role mapping, system instructions, generation configuration, API-key headers, multi-part text responses, and injected-transport tests. Next run should choose exactly one remaining unchecked task above; the deterministic repository index is the preferred next step and begins with the first task in Plan 0002. The full local pytest suite passes. Follow the README credential policy: create `.env.example` only when configuration is needed, keep `.env` untracked, and explicitly tell the user what to populate and which live checks could not run without keys.
