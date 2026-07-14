# Decision 0008: The Loopback HTTP API Redacts Captured Step Output the CLI Still Shows

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

Redacting a step's *declared* input (command argv, provider
`message.content`/`system`) to satisfy the exit-criteria wording literally
would break an already-shipped, passing acceptance test asserting exact
HTTP/CLI parity for a step's provider message, and would be inconsistent
with the milestone's own shipped behavior of showing command argv over
HTTP.

## Decision
The HTTP API redacts a completed step's *captured execution results* only:
`output.stdout`, `output.stderr`, `output.content` (provider response text),
and `output.raw` (provider response envelope), replaced with a
`"<redacted>"` marker. It leaves a step's *declared input* — command argv
and provider `message.content`/`system` — exactly as the CLI shows it. The
CLI's own `run inspect`/`inspect-step` output is unchanged and continues to
show captured output directly; this redaction is applied only within
`api.py`, not in the shared `payloads.py` builders.

## Rationale
Declared step input is operator-authored intent: whoever added the step
already typed the command or prompt, so showing it back adds no exposure
beyond what the CLI (and the state database itself) already discloses to
anyone with filesystem access. Captured output is different — it is
runtime-produced data (subprocess stdout/stderr, a model's response) that
can contain secrets, PII, or other sensitive content never authored by the
operator issuing the HTTP request. The CLI is an interactive tool invoked by
the same trusted local operator who owns the run; the loopback HTTP API is a
broader surface any co-resident process can reach without demonstrating
that same intent, so it earns the stricter guarantee for the one category
where redaction doesn't just restate output the requester already knows.

## Consequences
- `GET /api/v1/runs/{run_id}` intentionally diverges from `run inspect`
  output once a step has completed with real output — this is a permanent,
  deliberate contract difference, not a bug to reconcile later.
- Future HTTP routes that surface captured step output (if any) must apply
  the same `_redact_step_for_http`-style treatment; do not assume shared
  payload builders are HTTP-safe by default.
- If a future sprint (e.g., a web dashboard, Sprint 18) needs captured
  output over HTTP, that requires an explicit, separately reviewed decision
  (likely paired with authentication), not a quiet removal of this
  redaction.
