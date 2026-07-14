# Decision 0008: The Loopback HTTP API Redacts Declared Input and Captured Output the CLI Still Shows

## Context
Sprint 17 gave the read-only HTTP API the same JSON payload builders the CLI
already used (`payloads.py`), so `GET /api/v1/runs/{run_id}` matches `run
inspect` field-for-field. The milestone's exit criteria also requires that
"credentials, raw environment values, command arguments, provider request
bodies, and terminal outputs are never serialized" by the API. Auditing the
shared payload builders (Plan 0100) found that a completed step's captured
stdout/stderr and provider response text/raw envelope flow through
unredacted on both surfaces, while credentials and resolved environment
values were already structurally absent (Plan 0073 redacts resolved
passthrough values before they ever reach persisted state).

Plan 0100 initially left a step's *declared* input (command argv, provider
`message.content`/`system`) visible over HTTP, reasoning it was
operator-authored intent rather than a captured execution result. Sprint
17's retrospective (issue #110) found that reading did not satisfy the
milestone's exit-criteria wording, which bans command arguments and
provider request bodies outright, not just captured results. Remediation
issue #109 (Sprint 17.1) closes that gap.

## Decision
The HTTP API redacts both a step's *declared input* and its *captured
execution results*: `command` (argv), `message.content`, `message.system`,
`output.stdout`, `output.stderr`, `output.content` (provider response
text), and `output.raw` (provider response envelope) are each replaced with
a `"<redacted>"` marker. Non-sensitive metadata — provider name, model,
status, temperature, `max_tokens`, the sanitized `output.command` sandbox
invocation (env passthrough names only, never resolved values, per Plan
0073) — stays visible. The CLI's own `run inspect`/`inspect-step` output is
unchanged and continues to show declared input and captured output
directly; this redaction is applied only within `api.py`, not in the shared
`payloads.py` builders.

## Rationale
The milestone's literal wording treats declared command arguments and
provider request bodies as never-serializable over HTTP, regardless of who
authored them. The loopback HTTP API is a broader surface than the
interactive CLI: it is reachable by any co-resident process on the host,
not just the trusted local operator who added the step and already knows
its content. Redacting declared input over HTTP costs nothing an operator
interface needs today — ordered steps, status, provider/model metadata, and
non-sensitive sandbox evidence remain fully visible — while satisfying the
milestone's exit criterion without qualification.

## Consequences
- `GET /api/v1/runs/{run_id}` intentionally diverges from `run inspect`
  output for both declared and captured step content — this is a
  permanent, deliberate contract difference, not a bug to reconcile later.
- Future HTTP routes that surface step command/message/output fields must
  apply the same `_redact_step_for_http`-style treatment; do not assume
  shared payload builders are HTTP-safe by default.
- If a future sprint (e.g., a web dashboard, Sprint 18) needs declared or
  captured step content over HTTP, that requires an explicit, separately
  reviewed decision (likely paired with authentication), not a quiet
  removal of this redaction.
