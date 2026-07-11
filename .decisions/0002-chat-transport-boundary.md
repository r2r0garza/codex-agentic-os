# Decision 0002: Normalize chat at the transport boundary

## Status
Accepted

## Context
The project must support hosted vendors, local servers, and arbitrary OpenAI-compatible endpoints. Provider SDKs would add dependencies and couple the core to vendor-specific object models before the runtime contract is stable.

## Decision
Define a small `ChatRequest`/`ChatResponse` contract in the core and implement the first adapter over the widely implemented OpenAI-compatible `/chat/completions` protocol. The adapter is selected from `ProviderSpec`, and native protocol differences remain explicit follow-up work.

## Consequences
- OpenAI, OpenRouter, LM Studio, Ollama, and compatible servers can share one tested path.
- Tests can inject a transport without making network calls.
- Anthropic and Google support must be implemented as native adapters rather than guessed into the compatible path.
