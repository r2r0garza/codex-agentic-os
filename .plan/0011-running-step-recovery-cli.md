# Plan 0011: Running Step Recovery CLI

## Status
Complete

## Goal
Expose explicit running-step recovery to operators without changing the durable recovery
contract or retrying uncertain command execution.

## Tasks

- [x] Add a `run recover` CLI command that fails an uncertain running step with a typed reason and optional detail.

## Verification

- Recover interrupted and timed-out steps and print the resulting run with ordered steps.
- Reject missing databases, missing steps, and non-running steps without mutating durable state.

## Resume Notes

The plan is complete. `codex-agentic-os run recover <step-id> <reason>` delegates to
the durable recovery operation, accepts optional operator detail, and prints the failed
run with its ordered steps. Missing databases, missing steps, and invalid lifecycle
state are rejected without mutation. Resume by creating a focused plan for the next
execution-core capability; do not add automatic retries implicitly.
