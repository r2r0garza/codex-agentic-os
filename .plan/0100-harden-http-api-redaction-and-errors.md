# Plan 0100: Harden HTTP API Redaction and Error Guarantees

## Status
Complete

## Goal
Audit every Sprint 17 HTTP response shape against the CLI's established
redaction contract, close the concrete gaps found, and standardize
structured, non-mutating error responses for every stdlib-triggered failure
path, not just the routes this module dispatches explicitly.

## Tasks
- [x] Audit `_run_payload`/`_step_payload` (shared with the CLI) against the
      milestone's redaction bullet. Confirmed credentials and resolved
      environment values are already structurally absent everywhere (never
      persisted past dispatch-time substitution, per Plan 0073); the real
      gap was a completed step's captured terminal output (`stdout`,
      `stderr`) and provider response (`content`, `raw`), which the HTTP
      route serialized unredacted alongside the CLI.
- [x] Add `_redact_step_for_http` in `api.py`, applied only in
      `_respond_run`, replacing those four captured-output keys with a
      `"<redacted>"` marker when present. Declared step input (command argv,
      provider `message.content`/`system`) stays visible on both surfaces:
      it is operator-authored intent already known to whoever added the
      step, not a captured execution result, and an existing shipped test
      (`test_http_api_run_detail_matches_cli_run_inspect_output`) already
      locks in HTTP/CLI parity for a step's declared provider message.
      Recorded the resulting CLI/HTTP asymmetry in Decision 0008.
- [x] Fixed a real structured-error gap: any HTTP method without a `do_*`
      handler (`OPTIONS`, `TRACE`, `CONNECT`, or any other verb) fell
      through to `BaseHTTPRequestHandler`'s default HTML error page at 501,
      breaking the API's `{"error": ...}` JSON contract. Overrode
      `send_error` to route method-shaped stdlib failures through the
      existing `_reject_mutation` (405 + `Allow: GET`) response and every
      other stdlib-triggered failure (an unparseable request line, bad
      protocol version) through the existing structured `_respond_error`.
- [x] Added focused regression tests: captured-output redaction with a real
      completed command and provider step (env-passthrough value, stdout,
      stderr, provider response content and raw envelope all confirmed
      absent from the HTTP body); a same-run CLI-vs-HTTP comparison proving
      the CLI still shows what HTTP redacts; structured 405s for
      OPTIONS/TRACE/CONNECT/an arbitrary verb; a structured (non-HTML,
      non-traceback) error for an unparseable request line; 405 coverage
      extended to every run-scoped sub-route, not just `/api/v1/runs`; and
      a static route-inventory assertion that every non-GET `do_*` handler
      is the same rejection function.
- [x] Ran focused and full verification, refreshed the committed index,
      updated the durable run record, committed, pushed, and closed the
      issue.

## Resume Notes
Selected active-milestone issue: #107 (Sprint 17, priority:2, agent-ready).
Its stated dependencies #105/#106 are closed.

The milestone's own exit-criteria wording ("command arguments... provider
request bodies... terminal outputs are never serialized") reads, taken
literally, as banning declared step input too. That conflicts with an
already-shipped, passing acceptance test asserting exact HTTP/CLI parity for
a step with a populated, uncompleted provider message. Resolved by treating
"never serialized" as applying to captured execution results (terminal
output, provider response), not declared operator input (command argv,
provider request content) — the same category the milestone already ships
unredacted for command argv. See Decision 0008 for the durable rationale.

Scope intentionally excludes the consolidated offline endpoint-coverage
review and operator UAT record (#108), which remains blocked on this issue
and is a separate milestone slice.
