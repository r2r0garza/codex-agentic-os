# Plan 0053: Provider Credential Readiness CLI

## Status
Complete

## Goal
Expose a read-only readiness view for default provider credential environment variables without revealing values, making network requests, or accessing durable state.

## Tasks
- [x] Add `codex-agentic-os provider credentials` with one ordered record per default provider.
- [x] Report credential-free providers as configured and unset or empty named variables as unconfigured.
- [x] Keep output limited to provider kind, credential-variable name, and readiness boolean.
- [x] Add CLI coverage for ordering, readiness states, secret non-disclosure, and no network or state access.

## Resume Notes
Selected queue issue: #45 in active milestone Sprint 1. Credential correctness checks, endpoint probing, persistence, and provider-spec editing remain out of scope.
