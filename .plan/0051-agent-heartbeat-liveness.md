# Plan 0051: Agent Heartbeat Liveness

## Status
Complete

## Goal
Add an explicit durable liveness timestamp primitive for registered agents without coupling it to run claiming or eligibility.

## Tasks
- [x] Set an ISO-8601 UTC `last_seen` timestamp during registration and preserve compatibility with legacy records.
- [x] Add an injected-clock `AgentRegistry.heartbeat()` operation that rejects unknown identities without mutation.
- [x] Add `agent heartbeat AGENT_ID` with the existing state database and JSON payload conventions.
- [x] Verify runtime and CLI success and rejection paths.

## Resume Notes
Selected queue issue: #41. Heartbeats are explicit only; automatic worker heartbeats, staleness thresholds, expiry, and liveness-based run eligibility remain out of scope. Resume with the next prioritized unblocked `agent-ready` GitHub issue.
