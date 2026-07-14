# Plan 0102: Redact Declared Command and Provider Input from HTTP Run Detail

## Status
Complete

## Goal
Align `GET /api/v1/runs/{run_id}` with Sprint 17's literal redaction
criterion by never serializing a step's declared command argv or provider
`message.content`/`system`, closing the gap Sprint 17's retrospective
(#110) found in Decision 0008's original scope.

## Tasks
- [x] Extend `_redact_step_for_http` in `api.py` to also replace a step's
      `command` (when set) and its `message.content`/`system` (when
      present) with `"<redacted>"`, alongside the existing captured-output
      redaction. Non-sensitive metadata (provider, model, status,
      temperature, `max_tokens`, the already-sanitized `output.command`
      sandbox invocation) stays visible.
- [x] Update `.decisions/0008` to record the corrected boundary: the HTTP
      surface now redacts both declared input and captured output; only
      the CLI shows full detail.
- [x] Update `DEVELOPMENT.md`'s route description and operator UAT recipe
      to describe the corrected redaction contract.
- [x] Update `tests/test_api.py` regressions that previously asserted
      exact HTTP/CLI parity for declared command/message fields, and add a
      lifecycle-coverage regression proving declared input is redacted for
      queued, running, and failed steps (succeeded-step coverage already
      existed).
- [x] Run focused and full verification, refresh the committed index,
      perform an operator UAT confirming CLI/HTTP divergence and clean
      shutdown, update the durable run record, commit, push, and close the
      issue.

## Resume Notes
Selected active-milestone issue: #109 (Sprint 17.1, priority:1,
agent-ready), the sole issue in the remediation milestone created by
Sprint 17's retrospective (#110).

Focused verification passed with 48 tests (up from 47, +1 net); the full
suite passed with 720 tests (up from 719, +1 net). The refreshed index is
current at 27 files, 1072 symbols, and 6388 relationships, and
`git diff --check` passed. A CLI-started UAT against a temporary seeded
database confirmed HTTP redacts declared command argv and provider
content/system alongside captured output, `run inspect` still shows full
detail, and the server exits cleanly (status 0) on SIGINT.
