# Plan 0056: Read-only Agent Inspection CLI

## Status
Complete

## Goal
Let an operator inspect one durable agent identity without scanning the registry or mutating liveness and revision state.

## Tasks
- [x] Add `AgentRegistry.get()` with legacy `last_seen` compatibility.
- [x] Add read-only `agent inspect AGENT_ID` JSON output.
- [x] Reject missing databases, unknown agents, and empty identifiers without mutation.
- [x] Verify successful, missing, legacy, and revision-preservation paths.

## Resume Notes
Selected queue issue: #46. Inspection is read-only and does not add editing, deletion, heartbeat refresh, staleness evaluation, or run eligibility behavior. Sprint 3 becomes retrospective-eligible after issue closure.
