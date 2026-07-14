"""Local, read-only operator HTTP API over durable run inspection contracts.

Serves the same JSON payloads as the CLI's ``run list``, ``run inspect``,
``run history``, ``run approvals``, and ``run usage`` commands (see
``payloads.py``) over a loopback-only HTTP listener, so operator interfaces
beyond the CLI can be built on stable contracts. There are no mutation routes:
every handler here only reads through ``RunCoordinator``, and the state
database is always opened read-only by the caller.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socketserver
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

API_BASE_PATH = "/api/v1"
DEFAULT_POLL_INTERVAL_SECONDS = 0.5

_RUNS_PATH = f"{API_BASE_PATH}/runs"
_RUN_DETAIL_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)$")
_RUN_HISTORY_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/history$")
_RUN_APPROVALS_PATTERN = re.compile(
    rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/approvals$"
)
_RUN_USAGE_PATTERN = re.compile(rf"^{re.escape(_RUNS_PATH)}/(?P<run_id>[^/]+)/usage$")


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


class _ReadOnlyAPIRequestHandler(BaseHTTPRequestHandler):
    """Serve read-only run endpoints; reject every other path and method."""

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
        self._respond(HTTPStatus.OK, _run_payload(self.coordinator, run_id))

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

    do_POST = _reject_mutation
    do_PUT = _reject_mutation
    do_PATCH = _reject_mutation
    do_DELETE = _reject_mutation
    do_HEAD = _reject_mutation


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
    """Bind a read-only HTTP server exposing run inspection contracts.

    Raises ``ValueError`` before binding a socket when ``host`` is not an
    explicit loopback address, so a typo never opens a non-loopback
    listener even transiently.
    """

    if not is_loopback_bind_host(host):
        raise ValueError(
            f"HTTP API host must be an explicit loopback address, not {host!r}"
        )

    class _BoundHandler(_ReadOnlyAPIRequestHandler):
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
