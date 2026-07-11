# Decision 0001: Provider-neutral core first

## Status
Accepted

## Context
codex-agentic-os must support hosted providers, local providers, and OpenAI-compatible servers without forcing all agents through one vendor-specific abstraction.

## Decision
Start with small typed specifications for providers, sandboxes, and runtimes before choosing heavier orchestration dependencies. This keeps early architecture explicit and makes it easier to add LangChain DeepAgents, direct SDK adapters, or local-only execution later.

## Consequences
- Provider support is visible in code and tests from the first commit.
- The runtime can remain internal until an external runtime clearly earns its place.
- Docker and Podman are treated as required sandbox backends, not optional afterthoughts.
