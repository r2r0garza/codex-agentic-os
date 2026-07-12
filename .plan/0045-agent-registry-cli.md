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
Selected queue issue: #36. `AgentRegistry` lives in `runtime.py` alongside `RunCoordinator` and reuses the already-declared `"agent"` `StateStore.KINDS` entry. No heartbeat/liveness tracking, capability negotiation, or validation that `run claim`/`add-step --agent-id` values reference a registered agent were added; those remain out of scope per the issue. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
