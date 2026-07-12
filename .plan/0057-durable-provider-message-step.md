# Plan 0057: Durable Provider Message Step

## Status
In Progress

## Goal
Allow an operator to queue and inspect exactly one provider-neutral model input on a
durable step without invoking a provider.

## Tasks
- [x] Persist and reconstruct validated provider-message fields.
- [x] Extend `run add-step` and inspection JSON for model steps.
- [x] Reject missing and ambiguous execution inputs before state mutation.

## Resume Notes
Selected queue issue: #51. Durable steps now require exactly one command or provider
message. Direct CLI and library checks pass, but the activated `.venv` lacks pytest and
dependency retrieval stalled, so focused and full-suite verification remain required
before closure. Provider execution and failure handling remain out of scope.
