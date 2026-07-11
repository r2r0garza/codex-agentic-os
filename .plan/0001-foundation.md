# Plan 0001: Foundation

## Status
Complete

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
- [x] Add persistence for plans, decisions, runs, and agent state.

## Resume Notes
Foundation is complete. `StateStore` now supplies durable SQLite records for plans, decisions, runs, and agents with revision tracking, deterministic listing, JSON payload validation, and deletion. Runtime databases belong under the ignored `.codex-agentic-os/` directory. Resume by creating a new focused plan for the next runtime capability; do not extend persistence implicitly without first recording its lifecycle requirements.
