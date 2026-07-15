"""Local operator HTTP API over durable run inspection and mutation contracts.

Serves the same JSON payloads as the CLI's ``run list``, ``run inspect``,
``run history``, ``run approvals``, and ``run usage`` commands (see
``payloads.py``) over a loopback-only HTTP listener, so operator interfaces
beyond the CLI can be built on stable contracts. Every ``GET`` route only
reads through the caller's read-only ``RunCoordinator``. A small set of
explicitly enumerated ``POST`` mutation routes (approve, reject, cancel,
retry an eligible failed step) delegate to the same durable, compare-and-swap
coordinator operations the CLI uses, opening a separate writable state
connection only for the duration of that one mutation; no other route or
HTTP method can write.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socketserver
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import unquote, urlsplit

from .payloads import (
    _approval_payload,
    _history_payload,
    _run_list_payload,
    _run_payload,
    _usage_payload,
)
from .runtime import RunCoordinator
from .state import StateStore

API_BASE_PATH = "/api/v1"
DEFAULT_POLL_INTERVAL_SECONDS = 0.5

_REDACTED = "<redacted>"
_REDACTED_OUTPUT_KEYS = ("stdout", "stderr", "content", "raw")
_REDACTED_MESSAGE_KEYS = ("content", "system")
_REDACTED_TOOL_CALL_KEYS = ("arguments", "command", "stdout", "stderr")

_RUNS_PATH = f"{API_BASE_PATH}/runs"
_RUN_DETAIL_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)$")
_RUN_HISTORY_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/history$")
_RUN_APPROVALS_PATTERN = re.compile(
    rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/approvals$"
)
_RUN_USAGE_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/usage$")
_RUN_CANCEL_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/cancel$")
_STEP_APPROVE_PATTERN = re.compile(
    rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/steps/(?P<step_id>[^/]+)/approve$"
)
_STEP_REJECT_PATTERN = re.compile(
    rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/steps/(?P<step_id>[^/]+)/reject$"
)
_STEP_RETRY_PATTERN = re.compile(
    rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/steps/(?P<step_id>[^/]+)/retry$"
)


def is_loopback_bind_host(host: str) -> bool:
    """Return whether ``host`` is an explicit literal loopback address.

    Only accepts literal IP addresses such as ``127.0.0.1`` or ``::1``.
    Hostnames like ``localhost`` are rejected because DNS/hosts-file
    resolution could silently change what the server actually binds.
    """

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _redact_step_for_http(step_payload: dict[str, object]) -> dict[str, object]:
    """Strip a step's declared input and captured output for HTTP.

    ``_step_payload`` (shared with the CLI's ``run inspect``/``inspect-step``)
    keeps a step's declared command argv, declared provider message
    content/system, and a completed step's captured stdout/stderr and
    provider response text/raw envelope, because a local operator invoking
    the CLI directly already has that trust level. The loopback HTTP API is
    a broader surface reachable by any co-resident process, so it redacts
    both categories before serving a run's step detail: declared input
    (command argv, provider ``message.content``/``system``) and captured
    execution results (``output.stdout``/``stderr``/``content``/``raw``).
    Non-sensitive metadata — provider name, model, status, temperature,
    ``max_tokens`` — stays visible.
    """

    if step_payload.get("command") is not None:
        step_payload["command"] = _REDACTED
    message = step_payload.get("message")
    if isinstance(message, dict):
        for key in _REDACTED_MESSAGE_KEYS:
            if key in message:
                message[key] = _REDACTED
    output = step_payload.get("output")
    if isinstance(output, dict):
        for key in _REDACTED_OUTPUT_KEYS:
            if key in output:
                output[key] = _REDACTED
        _redact_tool_call_for_http(output.get("tool_call"))
        _redact_tool_iterations_for_http(output.get("tool_iterations"))
    declarations = step_payload.get("tool_declarations")
    if isinstance(declarations, list):
        for declaration in declarations:
            if isinstance(declaration, dict) and "command" in declaration:
                declaration["command"] = _REDACTED
    _redact_tool_call_for_http(step_payload.get("tool_call"))
    _redact_tool_iterations_for_http(step_payload.get("tool_iterations"))
    return step_payload


def _redact_tool_call_for_http(tool_call: object) -> None:
    """Redact model input, command arguments, and captured terminal output in place."""

    if not isinstance(tool_call, dict):
        return
    for key in _REDACTED_TOOL_CALL_KEYS:
        if key in tool_call:
            tool_call[key] = _REDACTED


def _redact_tool_iterations_for_http(iterations: object) -> None:
    """Redact provider response and tool evidence in ordered iterations."""

    if not isinstance(iterations, list):
        return
    for iteration in iterations:
        if not isinstance(iteration, dict):
            continue
        response = iteration.get("response")
        if isinstance(response, dict):
            for key in ("content", "raw"):
                if key in response:
                    response[key] = _REDACTED
        _redact_tool_call_for_http(iteration.get("tool_call"))


def _is_revision(value: object) -> bool:
    """Return whether ``value`` is a JSON integer, excluding JSON booleans."""

    return isinstance(value, int) and not isinstance(value, bool)


def _coordinator_error_message(error: KeyError | ValueError) -> str:
    """Return a plain error string, undoing ``KeyError``'s quoted ``repr`` formatting."""

    if isinstance(error, KeyError):
        return str(error.args[0]) if error.args else str(error)
    return str(error)


class _APIRequestHandler(BaseHTTPRequestHandler):
    """Serve read-only run endpoints plus a small set of mutation routes.

    Every ``GET`` route and every method on a ``GET`` route's own path is
    read-only. The only routes that write are the explicitly enumerated
    ``POST`` mutation routes below; every other method on those same routes
    is rejected exactly like the read-only routes are.
    """

    coordinator: RunCoordinator

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _respond(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_error(self, status: HTTPStatus, message: str) -> None:
        self._respond(status, {"error": message})

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == _RUNS_PATH:
            self._respond(HTTPStatus.OK, _run_list_payload(self.coordinator))
            return
        detail_match = _RUN_DETAIL_PATTERN.match(path)
        if detail_match is not None:
            self._respond_run(unquote(detail_match.group("run_id")))
            return
        history_match = _RUN_HISTORY_PATTERN.match(path)
        if history_match is not None:
            self._respond_history(unquote(history_match.group("run_id")))
            return
        approvals_match = _RUN_APPROVALS_PATTERN.match(path)
        if approvals_match is not None:
            self._respond_approvals(unquote(approvals_match.group("run_id")))
            return
        usage_match = _RUN_USAGE_PATTERN.match(path)
        if usage_match is not None:
            self._respond_usage(unquote(usage_match.group("run_id")))
            return
        self._respond_error(HTTPStatus.NOT_FOUND, f"unrecognized path: {self.path}")

    def _respond_run(self, run_id: str) -> None:
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        payload = _run_payload(self.coordinator, run_id)
        payload["steps"] = [_redact_step_for_http(step) for step in payload["steps"]]
        self._respond(HTTPStatus.OK, payload)

    def _respond_history(self, run_id: str) -> None:
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        self._respond(
            HTTPStatus.OK, _history_payload(self.coordinator.list_history(run_id))
        )

    def _respond_approvals(self, run_id: str) -> None:
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        self._respond(HTTPStatus.OK, _approval_payload(self.coordinator, run_id))

    def _respond_usage(self, run_id: str) -> None:
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        self._respond(HTTPStatus.OK, _usage_payload(self.coordinator, run_id))

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if (
            path == _RUNS_PATH
            or _RUN_DETAIL_PATTERN.match(path) is not None
            or _RUN_HISTORY_PATTERN.match(path) is not None
            or _RUN_APPROVALS_PATTERN.match(path) is not None
            or _RUN_USAGE_PATTERN.match(path) is not None
        ):
            self._reject_mutation()
            return
        approve_match = _STEP_APPROVE_PATTERN.match(path)
        if approve_match is not None:
            self._handle_step_decision(
                unquote(approve_match.group("run_id")),
                unquote(approve_match.group("step_id")),
                approve=True,
            )
            return
        reject_match = _STEP_REJECT_PATTERN.match(path)
        if reject_match is not None:
            self._handle_step_decision(
                unquote(reject_match.group("run_id")),
                unquote(reject_match.group("step_id")),
                approve=False,
            )
            return
        retry_match = _STEP_RETRY_PATTERN.match(path)
        if retry_match is not None:
            self._handle_step_retry(
                unquote(retry_match.group("run_id")),
                unquote(retry_match.group("step_id")),
            )
            return
        cancel_match = _RUN_CANCEL_PATTERN.match(path)
        if cancel_match is not None:
            self._handle_run_cancel(unquote(cancel_match.group("run_id")))
            return
        self._respond_error(HTTPStatus.NOT_FOUND, f"unrecognized path: {self.path}")

    def _read_json_body(self) -> dict[str, object]:
        """Drain and parse a JSON object request body, defaulting to empty.

        Always reads exactly ``Content-Length`` bytes, even when the caller
        ignores the result, so a body sent by a well-behaved client never
        corrupts a reused keep-alive connection's framing.
        """

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw.strip():
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"request body must be valid JSON: {error}") from error
        if not isinstance(decoded, dict):
            raise ValueError("request body must be a JSON object")
        return decoded

    def _writable_coordinator(self) -> RunCoordinator:
        return RunCoordinator(StateStore(self.coordinator.store.path, read_only=False))

    def _respond_mutation_outcome(self, run_id: str) -> None:
        """Return the refreshed, HTTP-redacted run detail after a mutation."""

        payload = _run_payload(self.coordinator, run_id)
        payload["steps"] = [_redact_step_for_http(step) for step in payload["steps"]]
        self._respond(HTTPStatus.OK, payload)

    def _handle_step_decision(self, run_id: str, step_id: str, *, approve: bool) -> None:
        try:
            self._read_json_body()
        except ValueError as error:
            self._respond_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        step = self.coordinator.get_step(step_id)
        if step is None or step.run_id != run_id:
            self._respond_error(HTTPStatus.NOT_FOUND, f"step does not exist: {step_id}")
            return
        writable = self._writable_coordinator()
        try:
            if approve:
                writable.approve_step(step_id)
            else:
                writable.reject_step(step_id)
        except (KeyError, ValueError) as error:
            self._respond_error(HTTPStatus.CONFLICT, _coordinator_error_message(error))
            return
        self._respond_mutation_outcome(run_id)

    def _handle_run_cancel(self, run_id: str) -> None:
        try:
            self._read_json_body()
        except ValueError as error:
            self._respond_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        writable = self._writable_coordinator()
        try:
            writable.cancel(run_id)
        except (KeyError, ValueError) as error:
            self._respond_error(HTTPStatus.CONFLICT, _coordinator_error_message(error))
            return
        self._respond_mutation_outcome(run_id)

    def _handle_step_retry(self, run_id: str, step_id: str) -> None:
        try:
            body = self._read_json_body()
        except ValueError as error:
            self._respond_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if self.coordinator.get(run_id) is None:
            self._respond_error(HTTPStatus.NOT_FOUND, f"run does not exist: {run_id}")
            return
        step = self.coordinator.get_step(step_id)
        if step is None or step.run_id != run_id:
            self._respond_error(HTTPStatus.NOT_FOUND, f"step does not exist: {step_id}")
            return
        expected_step_revision = body.get("expected_step_revision")
        expected_run_revision = body.get("expected_run_revision")
        if not _is_revision(expected_step_revision) or not _is_revision(expected_run_revision):
            self._respond_error(
                HTTPStatus.BAD_REQUEST,
                "expected_step_revision and expected_run_revision must be integers",
            )
            return
        new_step_id = f"{step_id}-retry-{uuid.uuid4().hex[:12]}"
        writable = self._writable_coordinator()
        try:
            writable.retry_step(
                step_id,
                new_step_id,
                expected_step_revision=expected_step_revision,
                expected_run_revision=expected_run_revision,
            )
        except (KeyError, ValueError) as error:
            self._respond_error(HTTPStatus.CONFLICT, _coordinator_error_message(error))
            return
        self._respond_mutation_outcome(run_id)

    def _reject_mutation(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET")
        body = json.dumps(
            {"error": f"unsupported method: {self.command}"}, sort_keys=True
        ).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_PUT = _reject_mutation
    do_PATCH = _reject_mutation
    do_DELETE = _reject_mutation
    do_HEAD = _reject_mutation

    def send_error(  # noqa: N802 - overriding BaseHTTPRequestHandler's stdlib name
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        """Return the established structured JSON error for stdlib-triggered failures.

        ``BaseHTTPRequestHandler`` calls this directly (bypassing every route
        handler above) for conditions no ``do_*`` method ever sees: an
        unparseable request line, an unsupported protocol version, or an
        HTTP method with no ``do_*`` handler at all (``OPTIONS``, ``TRACE``,
        ``CONNECT``, or any other verb). Left to the base implementation,
        those responses are an HTML error page instead of this API's
        ``{"error": ...}`` JSON contract. A missing ``do_*`` handler is a
        mutation-shaped failure exactly like the methods rejected above, so
        it is routed through the same structured 405 response; every other
        stdlib-triggered failure gets a structured error at its original
        status.
        """

        if code == HTTPStatus.NOT_IMPLEMENTED:
            self._reject_mutation()
            return
        try:
            status = HTTPStatus(code)
        except ValueError:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self.close_connection = True
        self._respond_error(status, message or status.phrase)


class _LoopbackHTTPServer(HTTPServer):
    """An ``HTTPServer`` that skips reverse-DNS resolution on bind.

    ``HTTPServer.server_bind`` resolves ``socket.getfqdn(host)`` purely for
    request-handler bookkeeping; since this server only ever binds to an
    explicit loopback literal, that resolved name is never meaningful and
    the lookup can stall for seconds on a host with no working resolver.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def build_server(coordinator: RunCoordinator, host: str, port: int) -> HTTPServer:
    """Bind an HTTP server exposing run inspection and mutation contracts.

    ``coordinator`` is used, read-only, for every ``GET`` route; the
    mutation routes derive a separate writable connection from
    ``coordinator.store.path`` on demand, so callers only ever need to hand
    this function the same read-only coordinator ``run inspect`` uses.
    Raises ``ValueError`` before binding a socket when ``host`` is not an
    explicit loopback address, so a typo never opens a non-loopback
    listener even transiently.
    """

    if not is_loopback_bind_host(host):
        raise ValueError(
            f"HTTP API host must be an explicit loopback address, not {host!r}"
        )

    class _BoundHandler(_APIRequestHandler):
        pass

    _BoundHandler.coordinator = coordinator
    return _LoopbackHTTPServer((host, port), _BoundHandler)


def serve_until_stopped(
    server: HTTPServer,
    *,
    should_continue: Callable[[], bool] = lambda: True,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """Serve requests until ``should_continue`` returns ``False``.

    Uses ``HTTPServer.handle_request`` with a bounded per-call timeout
    instead of ``serve_forever``, so the loop can be stopped cleanly from
    the same thread between requests (mirroring the CLI's other
    interruptible foreground loops) without a background-thread shutdown.
    """

    server.timeout = poll_interval
    while should_continue():
        server.handle_request()
