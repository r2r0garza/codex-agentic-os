# Plan 0099: HTTP Approvals and Usage

## Status
Complete

## Goal
Expose one run's sanitized approval requests and provider usage evidence over
the loopback-only read-only HTTP API using the exact existing CLI JSON
contracts.

## Tasks
- [x] Move the approval and usage payload builders into the shared payload
      module without changing CLI behavior.
- [x] Add read-only approvals and usage routes under `/api/v1/runs/{run_id}`.
- [x] Cover contract parity, unavailable usage, unknown runs, and no-mutation
      behavior with focused tests.
- [x] Run full verification, refresh the committed index, and complete the
      durable run record.

## Resume Notes
Selected active-milestone issue: #106 (Sprint 17, priority:2, agent-ready).
Its sole dependency #105 is closed. Scope excludes redaction hardening (#107),
full endpoint coverage review (#108), and every mutation endpoint.

Implementation complete. The HTTP API now serves `GET
/api/v1/runs/{run_id}/approvals` and `GET
/api/v1/runs/{run_id}/usage`. Both routes use payload builders shared with
the CLI, return the established structured unknown-run error, and read through
a read-only state store. Focused verification: 33 passed. Full suite: 705
passed. The committed index was rebuilt/current (27 files, 1057 symbols, 6256
relationships); `git diff --check` passed. DEVELOPMENT.md records the new
operator-visible routes.
