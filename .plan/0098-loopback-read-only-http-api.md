# Plan 0098: Loopback Read-Only HTTP API

## Status
Complete

## Goal
Add a CLI-started local HTTP server that exposes stable, read-only JSON
run-list, run-detail, and run-history endpoints over an explicitly
configured loopback bind, reusing the CLI's existing inspection contracts
instead of inventing divergent shapes.

## Tasks
- [x] Extract `_run_payload`, `_step_payload`, `_run_list_payload`,
      `_history_payload`, and `_artifact_record_payload` out of `cli.py`
      into a new `payloads.py`, shared by the CLI and the new HTTP module
      without introducing a circular import.
- [x] Add `api.py`: a stdlib-only (`http.server`) read-only HTTP server.
      `is_loopback_bind_host` accepts only literal loopback IP addresses
      (rejecting hostnames like `localhost`); `build_server` validates the
      host before binding a socket. Routes: `GET /api/v1/runs`,
      `GET /api/v1/runs/{run_id}`, `GET /api/v1/runs/{run_id}/history`.
      Every other path returns a structured 404 and every non-GET method
      returns a structured 405; there is no mutation route of any kind.
- [x] Add `codex-agentic-os api serve --host HOST --port PORT --state-db
      PATH`, opening the state database read-only and rejecting a
      non-loopback host or missing database before serving, reusing the
      worker command's SIGINT/SIGTERM shutdown adapter.
- [x] Work around `http.server.HTTPServer.server_bind`'s
      `socket.getfqdn(host)` call, which stalled for ~35s per bind in this
      sandbox's DNS-less environment; `_LoopbackHTTPServer` overrides
      `server_bind` to skip the reverse lookup since the host is always an
      already-validated loopback literal.
- [x] Add focused tests: loopback-host validation, routing/JSON-contract
      parity against both the shared payload functions and live CLI
      output, 404/405 error shape, read-only state opening, and clean
      CLI-level shutdown on SIGINT/SIGTERM.
- [x] Run focused and full verification, refresh the committed code
      index, update the durable run record, commit, push, and close the
      issue.

## Resume Notes
Selected active-milestone issue: #105 (Sprint 17, priority:1, agent-ready,
no stated dependency). Scope intentionally excludes approvals/usage
endpoints (#106), redaction hardening beyond the reused read models
(#107), and full offline endpoint coverage review (#108) — those remain
separate milestone issues.
