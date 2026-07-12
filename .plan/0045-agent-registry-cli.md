# Plan 0045: Durable Agent Registry CLI

## Status
Complete

## Goal
Back agent identities used by `--agent-id` across run commands with a real durable record instead of an arbitrary unchecked string.

## Tasks
- [x] Add `AgentRegistry` (`register`/`list_agents`) backed by `StateStore.insert`/`StateStore.list` on the existing `"agent"` kind.
- [x] Add `codex-agentic-os agent register AGENT_ID [--label TEXT]` and `codex-agentic-os agent list` CLI commands following `run` subcommand conventions.
- [x] Verify registration, duplicate rejection, listing, and invalid identifier/label rejection without mutation.

## Resume Notes
Selected queue issue: #38. `AgentRegistry` lives in `runtime.py` alongside `RunCoordinator` and reuses the already-declared `"agent"` `StateStore.KINDS` entry. Issue #38 adds pre-mutation registered-agent validation to `RunCoordinator.create()`, `claim()`, and `claim_next()`; `add_step`, heartbeat/liveness tracking, and capability negotiation remain out of scope. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
