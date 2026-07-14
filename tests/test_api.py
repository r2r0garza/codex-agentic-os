from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import signal
import socket
import threading
import urllib.error
import urllib.request

import pytest

from codex_agentic_os.api import (
    _redact_step_for_http,
    build_server,
    is_loopback_bind_host,
    serve_until_stopped,
)
from codex_agentic_os.chat import ChatResponse, ChatUsage
from codex_agentic_os.cli import main
from codex_agentic_os.payloads import (
    _approval_payload,
    _history_payload,
    _run_list_payload,
    _run_payload,
    _usage_payload,
)
from codex_agentic_os.runtime import (
    AgentRegistry,
    ProviderMessage,
    RunCoordinator,
    RunStatus,
    SandboxPolicy,
    StepStatus,
)
from codex_agentic_os.sandboxes import SandboxKind, SandboxResult
from codex_agentic_os.state import StateStore


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.5.5.5", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("::", False),
        ("localhost", False),
        ("8.8.8.8", False),
        ("", False),
        ("not-an-address", False),
    ],
)
def test_is_loopback_bind_host_accepts_only_explicit_loopback_literals(
    host, expected
) -> None:
    assert is_loopback_bind_host(host) is expected


def test_build_server_rejects_non_loopback_host_before_binding(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))

    with pytest.raises(ValueError, match="explicit loopback address"):
        build_server(coordinator, "0.0.0.0", 0)


@contextlib.contextmanager
def _running_server(coordinator, *, host: str = "127.0.0.1"):
    server = build_server(coordinator, host, 0)
    stop = threading.Event()
    thread = threading.Thread(
        target=serve_until_stopped,
        args=(server,),
        kwargs={"should_continue": lambda: not stop.is_set(), "poll_interval": 0.05},
    )
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        stop.set()
        thread.join(timeout=5)
        server.server_close()


def _seed_database(database) -> RunCoordinator:
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Inspect via HTTP")
    coordinator.add_step("run-1", "step-1", objective="Work", command=("true",))
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.create("run-2", objective="Second run", agent_id=None)
    coordinator.add_step(
        "run-2",
        "step-2",
        objective="Summarize",
        message=ProviderMessage(provider="ollama", content="Summarize"),
    )
    return coordinator


def _seed_approval_and_usage(database) -> RunCoordinator:
    store = StateStore(database)
    AgentRegistry(store).register("operator-1")
    coordinator = RunCoordinator(store)
    coordinator.create(
        "run-evidence", objective="Inspect evidence", agent_id="operator-1"
    )
    coordinator.add_step(
        "run-evidence",
        "provider-1",
        objective="Approved provider request",
        message=ProviderMessage(
            provider="ollama", content="private prompt", model="requested-model"
        ),
        approval_required=True,
    )
    coordinator.add_step(
        "run-evidence",
        "provider-2",
        objective="Queued provider request",
        message=ProviderMessage(provider="anthropic", content="another private prompt"),
    )
    coordinator.approve_step("provider-1", agent_id="operator-1")
    coordinator.transition("run-evidence", RunStatus.RUNNING)
    coordinator.transition_step("provider-1", StepStatus.RUNNING)
    coordinator.transition_step(
        "provider-1",
        StepStatus.SUCCEEDED,
        output={
            "content": "sanitized response",
            "model": "served-model",
            "usage": {
                "available": True,
                "input_tokens": 7,
                "output_tokens": 3,
                "raw": {"prompt_tokens": 7, "completion_tokens": 3},
                "unavailable_reason": None,
            },
        },
    )
    return coordinator


def _seed_completed_steps_with_sensitive_output(database) -> RunCoordinator:
    """Seed a run with a completed command step and a completed provider step.

    Both steps carry raw values the HTTP API must never serialize (captured
    terminal output, a resolved passthrough environment value, and provider
    request/response text) while the CLI's own inspection commands continue
    to show them, matching the read-only HTTP redaction contract this module
    tests against.
    """

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-sensitive", objective="Inspect sensitive evidence")
    coordinator.add_step(
        "run-sensitive",
        "command-1",
        objective="Run command",
        command=("true",),
        sandbox_policy=SandboxPolicy(
            kind=SandboxKind.DOCKER, env_passthrough=("API_TOKEN",)
        ),
    )
    coordinator.add_step(
        "run-sensitive",
        "provider-1",
        objective="Ask provider",
        message=ProviderMessage(
            provider="ollama",
            content="private request prompt",
            system="private system prompt",
        ),
    )
    coordinator.transition("run-sensitive", RunStatus.RUNNING)
    coordinator.start_next_step("run-sensitive")
    coordinator.complete_step_from_result(
        "command-1",
        SandboxResult(
            (
                "docker", "run", "--env", "API_TOKEN=runtime-only-secret",
                "python:3.12-slim", "true",
            ),
            0,
            "private stdout",
            "private stderr",
        ),
    )
    coordinator.start_next_step("run-sensitive")
    coordinator.complete_step_from_chat_response(
        "provider-1",
        ChatResponse(
            content="private response text",
            model="served-model",
            raw={"echo": "private raw envelope"},
            usage=ChatUsage(available=True, input_tokens=1, output_tokens=1),
        ),
    )
    return coordinator


def _seed_pending_approval(database) -> RunCoordinator:
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-approval", objective="Needs an operator decision")
    coordinator.add_step(
        "run-approval",
        "step-1",
        objective="Approve me",
        command=("true",),
        approval_required=True,
    )
    coordinator.transition("run-approval", RunStatus.RUNNING)
    return coordinator


def _seed_active_run_with_queued_step(database) -> RunCoordinator:
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-active", objective="Cancel me")
    coordinator.add_step(
        "run-active", "active-step-1", objective="Work", command=("true",)
    )
    coordinator.transition("run-active", RunStatus.RUNNING)
    return coordinator


def _seed_retry_eligible_failed_step(database) -> tuple[RunCoordinator, int, int]:
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-retry", objective="Retry durable work")
    coordinator.add_step(
        "run-retry", "command", objective="Run command", command=("false",), timeout=4
    )
    coordinator.start_next_step("run-retry")
    failed_step, failed_run = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "false"), 17, "", "boom")
    )
    return coordinator, failed_step.revision, failed_run.revision


def _get_json(port: int, path: str) -> object:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(port: int, path: str, body: object = None, *, raw_body: bytes | None = None):
    """POST to ``path`` and return ``(status, decoded_json)``, tolerating error responses."""

    data = raw_body if raw_body is not None else (
        b"" if body is None else json.dumps(body).encode("utf-8")
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _as_json(payload: object) -> object:
    """Round-trip a payload through JSON so tuples compare equal to lists."""

    return json.loads(json.dumps(payload))


def _database_snapshot(database) -> tuple[object, ...]:
    """Capture every durable run view used by the read-only HTTP surface."""

    coordinator = RunCoordinator(StateStore(database, read_only=True))
    runs = coordinator.list_runs()
    return tuple(
        (
            run,
            tuple(coordinator.list_steps(run.run_id)),
            tuple(coordinator.list_history(run.run_id)),
        )
        for run in runs
    )


def test_http_api_run_list_matches_run_list_payload_contract(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        body = _get_json(port, "/api/v1/runs")

    assert body == _as_json(_run_list_payload(coordinator))
    assert [run["run_id"] for run in body] == ["run-1", "run-2"]


def test_http_api_run_list_matches_cli_run_list_output(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "list", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)

    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs")

    assert http_payload == cli_payload


def test_http_api_run_detail_matches_run_payload_contract(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        body = _get_json(port, "/api/v1/runs/run-1")

    expected = _as_json(_run_payload(coordinator, "run-1"))
    expected["steps"] = [_redact_step_for_http(step) for step in expected["steps"]]
    assert body == expected
    assert body["run"]["run_id"] == "run-1"
    assert [step["step_id"] for step in body["steps"]] == ["step-1"]
    assert body["steps"][0]["command"] == "<redacted>"


def test_http_api_run_detail_matches_cli_run_inspect_output_except_redacted_fields(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "inspect", "run-2", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)

    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs/run-2")

    expected = json.loads(json.dumps(cli_payload))
    expected["steps"] = [_redact_step_for_http(step) for step in expected["steps"]]
    assert http_payload == expected
    assert cli_payload["steps"][0]["message"]["content"] == "Summarize"
    assert http_payload["steps"][0]["message"]["content"] == "<redacted>"


def test_http_api_run_history_matches_history_payload_contract(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        body = _get_json(port, "/api/v1/runs/run-1/history")

    assert body == _as_json(_history_payload(coordinator.list_history("run-1")))
    assert [entry["transition"] for entry in body] == ["created", "transitioned"]


def test_http_api_run_history_matches_cli_run_history_output(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "history", "run-1", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)

    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs/run-1/history")

    assert http_payload == cli_payload


def test_http_api_approvals_matches_shared_and_cli_contracts(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_approval_and_usage(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "approvals", "run-evidence", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)
    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs/run-evidence/approvals")

    assert http_payload == _as_json(_approval_payload(coordinator, "run-evidence"))
    assert http_payload == cli_payload
    assert http_payload == [
        {
            "approval_required": True,
            "approval_status": "approved",
            "deciding_agent_id": "operator-1",
            "execution_kind": "provider",
            "objective": "Approved provider request",
            "position": 1,
            "requesting_agent_id": "operator-1",
            "run_id": "run-evidence",
            "step_id": "provider-1",
            "step_status": "succeeded",
        }
    ]
    assert "private prompt" not in json.dumps(http_payload)


def test_http_api_usage_matches_shared_and_cli_contracts(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_approval_and_usage(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "usage", "run-evidence", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)
    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs/run-evidence/usage")

    assert http_payload == _as_json(_usage_payload(coordinator, "run-evidence"))
    assert http_payload == cli_payload
    assert [step["step_id"] for step in http_payload["steps"]] == [
        "provider-1",
        "provider-2",
    ]
    assert http_payload["steps"][1]["usage"] == {
        "available": False,
        "input_tokens": None,
        "output_tokens": None,
        "raw": None,
        "unavailable_reason": "no usage recorded for step status queued",
    }
    assert http_payload["aggregate"] == {
        "steps_with_usage_available": 1,
        "steps_with_usage_unavailable": 1,
        "input_tokens": 7,
        "output_tokens": 3,
    }
    assert "private prompt" not in json.dumps(http_payload)


def test_http_api_complete_offline_endpoint_review_is_loopback_only_and_read_only(
    tmp_path, monkeypatch
) -> None:
    """Exercise every Sprint 17 endpoint against one temporary mixed database."""

    database = tmp_path / "state.sqlite3"
    _seed_approval_and_usage(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))
    before = _database_snapshot(database)

    original_create_connection = socket.create_connection
    connected_hosts: list[str] = []

    def connect_loopback_only(address, *args, **kwargs):
        host = address[0]
        assert ipaddress.ip_address(host).is_loopback
        connected_hosts.append(host)
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", connect_loopback_only)

    with _running_server(coordinator) as port:
        run_list = _get_json(port, "/api/v1/runs")
        detail = _get_json(port, "/api/v1/runs/run-evidence")
        history = _get_json(port, "/api/v1/runs/run-evidence/history")
        approvals = _get_json(port, "/api/v1/runs/run-evidence/approvals")
        usage = _get_json(port, "/api/v1/runs/run-evidence/usage")

    assert [run["run_id"] for run in run_list] == ["run-evidence"]
    detail_steps = {step["step_id"]: step for step in detail["steps"]}
    assert detail_steps["provider-1"]["output"]["content"] == "<redacted>"
    serialized_detail = json.dumps(detail)
    assert "sanitized response" not in serialized_detail
    assert [entry["transition"] for entry in history] == [
        "created",
        "step_approved",
        "transitioned",
        "step_started",
        "step_succeeded",
    ]
    assert [item["step_id"] for item in approvals] == ["provider-1"]
    assert [item["step_id"] for item in usage["steps"]] == [
        "provider-1",
        "provider-2",
    ]
    assert connected_hosts
    assert _database_snapshot(database) == before


@pytest.mark.parametrize("suffix", ["approvals", "usage"])
def test_http_api_evidence_endpoints_return_structured_unknown_run_error(
    tmp_path, suffix
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        with pytest.raises(urllib.error.HTTPError) as error:
            _get_json(port, f"/api/v1/runs/does-not-exist/{suffix}")

    assert error.value.code == 404
    assert json.loads(error.value.read()) == {
        "error": "run does not exist: does-not-exist"
    }


def test_http_api_evidence_endpoints_do_not_mutate_state(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_approval_and_usage(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))
    before = (
        coordinator.get("run-evidence"),
        coordinator.list_steps("run-evidence"),
        coordinator.list_history("run-evidence"),
    )

    with _running_server(coordinator) as port:
        _get_json(port, "/api/v1/runs/run-evidence/approvals")
        _get_json(port, "/api/v1/runs/run-evidence/usage")

    reloaded = RunCoordinator(StateStore(database, read_only=True))
    assert (
        reloaded.get("run-evidence"),
        reloaded.list_steps("run-evidence"),
        reloaded.list_history("run-evidence"),
    ) == before


def test_http_api_unknown_run_returns_structured_404_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))
    before = _run_list_payload(coordinator)

    with _running_server(coordinator) as port:
        with pytest.raises(urllib.error.HTTPError) as detail_error:
            _get_json(port, "/api/v1/runs/does-not-exist")
        with pytest.raises(urllib.error.HTTPError) as history_error:
            _get_json(port, "/api/v1/runs/does-not-exist/history")

    assert detail_error.value.code == 404
    assert json.loads(detail_error.value.read()) == {
        "error": "run does not exist: does-not-exist"
    }
    assert history_error.value.code == 404
    assert json.loads(history_error.value.read()) == {
        "error": "run does not exist: does-not-exist"
    }
    assert _run_list_payload(RunCoordinator(StateStore(database, read_only=True))) == before


def test_http_api_unrecognized_path_returns_structured_404(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        with pytest.raises(urllib.error.HTTPError) as error:
            _get_json(port, "/api/v1/agents")

    assert error.value.code == 404
    assert "unrecognized path" in json.loads(error.value.read())["error"]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_http_api_mutation_methods_return_structured_405_without_mutation(
    tmp_path, method
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))
    before = _run_list_payload(coordinator)

    with _running_server(coordinator) as port:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/runs", method=method
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)

    assert error.value.code == 405
    assert error.value.headers.get("Allow") == "GET"
    assert json.loads(error.value.read()) == {"error": f"unsupported method: {method}"}
    assert _run_list_payload(RunCoordinator(StateStore(database, read_only=True))) == before


def test_http_api_query_string_is_ignored_on_run_list(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        body = _get_json(port, "/api/v1/runs?status=queued")

    assert [run["run_id"] for run in body] == ["run-1", "run-2"]


def test_http_api_opens_state_database_read_only(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    assert coordinator.store.read_only is True
    with _running_server(coordinator) as port:
        _get_json(port, "/api/v1/runs")
        _get_json(port, "/api/v1/runs/run-1")
        _get_json(port, "/api/v1/runs/run-1/history")
        _get_json(port, "/api/v1/runs/run-1/approvals")
        _get_json(port, "/api/v1/runs/run-1/usage")


def test_cli_api_serve_rejects_non_loopback_host_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "api",
                "serve",
                "--host",
                "0.0.0.0",
                "--port",
                "0",
                "--state-db",
                str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "explicit loopback address" in capsys.readouterr().err


def test_cli_api_serve_rejects_missing_database_without_creating_one(
    tmp_path, capsys
) -> None:
    database = tmp_path / "nested" / "state.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["api", "serve", "--port", "0", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


@pytest.mark.parametrize("target_signal", [signal.SIGINT, signal.SIGTERM])
def test_cli_api_serve_stops_cleanly_on_shutdown_signal(
    tmp_path, monkeypatch, capsys, target_signal
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)

    def fake_handle_request(self) -> None:
        os.kill(os.getpid(), target_signal)

    monkeypatch.setattr(
        "codex_agentic_os.api.HTTPServer.handle_request", fake_handle_request
    )

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    main(["api", "serve", "--port", "0", "--state-db", str(database)])

    assert signal.getsignal(signal.SIGINT) is previous_sigint
    assert signal.getsignal(signal.SIGTERM) is previous_sigterm

    payload = json.loads(capsys.readouterr().out)
    assert payload["host"] == "127.0.0.1"
    assert isinstance(payload["port"], int)


def test_http_api_run_detail_redacts_declared_input_across_lifecycle_states(
    tmp_path,
) -> None:
    """Declared command argv and provider content/system are redacted regardless
    of a step's lifecycle status (queued, running, or failed); succeeded-step
    coverage lives in the captured-output redaction tests below."""

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))

    coordinator.create("run-queued", objective="Queued lifecycle")
    coordinator.add_step(
        "run-queued", "command-queued", objective="Run", command=("echo", "queued-secret")
    )
    coordinator.add_step(
        "run-queued",
        "provider-queued",
        objective="Ask",
        message=ProviderMessage(
            provider="ollama",
            content="queued-secret-prompt",
            system="queued-secret-system",
        ),
    )

    coordinator.create("run-running", objective="Running lifecycle")
    coordinator.add_step(
        "run-running", "command-running", objective="Run", command=("echo", "running-secret")
    )
    coordinator.transition("run-running", RunStatus.RUNNING)
    coordinator.start_next_step("run-running")

    coordinator.create("run-failed-command", objective="Failed command lifecycle")
    coordinator.add_step(
        "run-failed-command",
        "command-failed",
        objective="Run",
        command=("false", "failed-command-secret"),
    )
    coordinator.transition("run-failed-command", RunStatus.RUNNING)
    coordinator.start_next_step("run-failed-command")
    coordinator.complete_step_from_result(
        "command-failed", SandboxResult(("false",), 1, "", "boom")
    )

    coordinator.create("run-failed-provider", objective="Failed provider lifecycle")
    coordinator.add_step(
        "run-failed-provider",
        "provider-failed",
        objective="Ask",
        message=ProviderMessage(
            provider="ollama",
            content="failed-provider-secret-prompt",
            system="failed-provider-secret-system",
        ),
    )
    coordinator.transition("run-failed-provider", RunStatus.RUNNING)
    coordinator.start_next_step("run-failed-provider")
    coordinator.fail_step_from_error("provider-failed", RuntimeError("boom"))

    read_only_coordinator = RunCoordinator(StateStore(database, read_only=True))
    run_ids = [
        "run-queued",
        "run-running",
        "run-failed-command",
        "run-failed-provider",
    ]
    raw_bodies = []
    with _running_server(read_only_coordinator) as port:
        for run_id in run_ids:
            body = _get_json(port, f"/api/v1/runs/{run_id}")
            raw_bodies.append(json.dumps(body))
            for step in body["steps"]:
                if step.get("command") is not None:
                    assert step["command"] == "<redacted>"
                message = step.get("message")
                if message is not None:
                    assert message["content"] == "<redacted>"
                    if "system" in message:
                        assert message["system"] == "<redacted>"

    combined = "\n".join(raw_bodies)
    for secret in (
        "queued-secret",
        "queued-secret-prompt",
        "queued-secret-system",
        "running-secret",
        "failed-command-secret",
        "failed-provider-secret-prompt",
        "failed-provider-secret-system",
    ):
        assert secret not in combined


def test_http_api_run_detail_redacts_captured_terminal_and_provider_output(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_completed_steps_with_sensitive_output(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        body = _get_json(port, "/api/v1/runs/run-sensitive")

    steps = {step["step_id"]: step for step in body["steps"]}
    command_output = steps["command-1"]["output"]
    assert command_output["stdout"] == "<redacted>"
    assert command_output["stderr"] == "<redacted>"
    assert command_output["exit_code"] == 0
    assert command_output["command"] == [
        "docker", "run", "--env", "API_TOKEN", "python:3.12-slim", "true",
    ]
    # Declared step input (command argv) is redacted; the captured
    # sandbox-invocation command above is a separate, already-sanitized field.
    assert steps["command-1"]["command"] == "<redacted>"

    provider_step = steps["provider-1"]
    # Declared provider request content/system is redacted; provider
    # metadata (dispatch target) stays visible.
    assert provider_step["message"]["content"] == "<redacted>"
    assert provider_step["message"]["system"] == "<redacted>"
    assert provider_step["message"]["provider"] == "ollama"
    # Captured provider response output is redacted.
    assert provider_step["output"]["content"] == "<redacted>"
    assert provider_step["output"]["raw"] == "<redacted>"
    assert provider_step["output"]["model"] == "served-model"
    assert provider_step["output"]["usage"] == {
        "available": True,
        "input_tokens": 1,
        "output_tokens": 1,
        "raw": None,
        "unavailable_reason": None,
    }


def test_http_redaction_covers_tool_declarations_calls_and_nested_results() -> None:
    payload = {
        "tool_declarations": [
            {"name": "inspect", "command": ["sh", "-c", "private command"]}
        ],
        "tool_call": {
            "tool_name": "inspect",
            "arguments": {"token": "private argument"},
            "phase": "executed",
            "command": ["docker", "private command"],
            "exit_code": 0,
            "stdout": "private stdout",
            "stderr": "private stderr",
        },
        "output": {
            "content": "private response",
            "tool_call": {
                "tool_name": "inspect",
                "arguments": {"token": "private argument"},
                "phase": "executed",
                "command": ["docker", "private command"],
                "exit_code": 0,
                "stdout": "private stdout",
                "stderr": "private stderr",
            },
        },
    }

    redacted = _redact_step_for_http(payload)

    assert redacted["tool_declarations"][0]["name"] == "inspect"
    assert redacted["tool_declarations"][0]["command"] == "<redacted>"
    for tool_call in (redacted["tool_call"], redacted["output"]["tool_call"]):
        assert tool_call["tool_name"] == "inspect"
        assert tool_call["phase"] == "executed"
        assert tool_call["exit_code"] == 0
        for key in ("arguments", "command", "stdout", "stderr"):
            assert tool_call[key] == "<redacted>"
    assert "private" not in json.dumps(redacted)


def test_http_api_run_detail_never_serializes_sensitive_raw_values(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_completed_steps_with_sensitive_output(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/v1/runs/run-sensitive"
        ) as response:
            raw_body = response.read().decode("utf-8")

    for sensitive_value in (
        "private stdout",
        "private stderr",
        "private response text",
        "private raw envelope",
        "runtime-only-secret",
        "private request prompt",
        "private system prompt",
    ):
        assert sensitive_value not in raw_body


def test_cli_run_inspect_shows_full_detail_while_http_redacts_declared_and_captured(
    tmp_path, capsys
) -> None:
    """The CLI keeps full local-operator detail; only the HTTP surface redacts it.

    This is a deliberate divergence from exact CLI/HTTP contract parity,
    recorded in .decisions/0008: the HTTP loopback API is a broader
    co-resident-process surface than the interactive CLI, so it redacts both
    a step's declared input (command argv, provider message content/system)
    and a completed step's captured terminal/provider output, all of which
    the CLI still shows in full.
    """

    database = tmp_path / "state.sqlite3"
    _seed_completed_steps_with_sensitive_output(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "inspect", "run-sensitive", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)
    cli_steps = {step["step_id"]: step for step in cli_payload["steps"]}
    assert cli_steps["command-1"]["command"] == ["true"]
    assert cli_steps["command-1"]["output"]["stdout"] == "private stdout"
    assert cli_steps["provider-1"]["message"]["content"] == "private request prompt"
    assert cli_steps["provider-1"]["output"]["content"] == "private response text"

    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs/run-sensitive")

    http_steps = {step["step_id"]: step for step in http_payload["steps"]}
    assert http_steps["command-1"]["command"] == "<redacted>"
    assert http_steps["command-1"]["output"]["stdout"] == "<redacted>"
    assert http_steps["provider-1"]["message"]["content"] == "<redacted>"
    assert http_steps["provider-1"]["output"]["content"] == "<redacted>"
    assert http_payload != cli_payload


def _raw_http_response(port: int, request_line: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(request_line)
        connection.settimeout(5)
        chunks = []
        try:
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            pass
        return b"".join(chunks)


@pytest.mark.parametrize("method", ["OPTIONS", "TRACE", "CONNECT", "FOOBAR"])
def test_http_api_unimplemented_methods_return_structured_405_without_mutation(
    tmp_path, method
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))
    before = _run_list_payload(coordinator)

    with _running_server(coordinator) as port:
        response = _raw_http_response(
            port, f"{method} /api/v1/runs HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        )

    header_block, _, body = response.partition(b"\r\n\r\n")
    assert b"405 Method Not Allowed" in header_block
    assert b"Content-Type: application/json" in header_block
    assert b"Allow: GET" in header_block
    assert json.loads(body) == {"error": f"unsupported method: {method}"}
    assert _run_list_payload(RunCoordinator(StateStore(database, read_only=True))) == before


def test_http_api_malformed_request_line_returns_structured_error_without_html(
    tmp_path,
) -> None:
    """An unparseable request line gets the API's JSON error shape, not HTML.

    ``BaseHTTPRequestHandler`` detects this failure before a real HTTP
    version is established, so (per the stdlib's own HTTP/0.9 framing rules)
    the wire response here is the bare error body with no status line or
    headers — that framing quirk is unrelated to this handler's error
    contract. What this test proves is the body itself: the established
    ``{"error": ...}`` JSON shape, never the stdlib's default HTML error
    page or an unhandled traceback.
    """

    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        response = _raw_http_response(port, b"not a valid request line at all\r\n\r\n")

    decoded = response.decode("utf-8", errors="replace")
    assert "<html" not in decoded.lower()
    assert "Traceback" not in decoded
    parsed = json.loads(response)
    assert "error" in parsed
    assert "Bad request version" in parsed["error"]


@pytest.mark.parametrize("suffix", ["", "/history", "/approvals", "/usage"])
def test_http_api_mutation_methods_rejected_on_run_scoped_routes(
    tmp_path, suffix
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))
    before = _run_list_payload(coordinator)

    with _running_server(coordinator) as port:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/runs/run-1{suffix}", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)

    assert error.value.code == 405
    assert error.value.headers.get("Allow") == "GET"
    assert json.loads(error.value.read()) == {"error": "unsupported method: POST"}
    assert _run_list_payload(RunCoordinator(StateStore(database, read_only=True))) == before


def test_http_api_route_inventory_exposes_no_mutation_handler() -> None:
    from codex_agentic_os.api import _APIRequestHandler

    unimplemented_methods = ("PUT", "PATCH", "DELETE", "HEAD")
    for method in unimplemented_methods:
        handler = getattr(_APIRequestHandler, f"do_{method}")
        assert handler is _APIRequestHandler._reject_mutation
    assert _APIRequestHandler.do_GET is not _APIRequestHandler._reject_mutation
    assert _APIRequestHandler.do_POST is not _APIRequestHandler._reject_mutation
    assert _APIRequestHandler.do_POST is not _APIRequestHandler.do_GET


def test_http_api_approve_step_delegates_to_coordinator_and_returns_refreshed_run(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        before_status, before_approvals = _post_json(
            port, "/api/v1/runs/run-approval/approvals"
        )
        status, body = _post_json(
            port, "/api/v1/runs/run-approval/steps/step-1/approve"
        )
        approvals = _get_json(port, "/api/v1/runs/run-approval/approvals")

    assert before_status == 405  # GET-only route stays GET-only under POST.
    assert status == 200
    assert body["steps"][0]["step_id"] == "step-1"
    assert body["steps"][0]["status"] == "queued"
    assert approvals[0]["approval_status"] == "approved"


def test_http_api_reject_step_delegates_to_coordinator_and_fails_run(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port, "/api/v1/runs/run-approval/steps/step-1/reject"
        )
        approvals = _get_json(port, "/api/v1/runs/run-approval/approvals")

    assert status == 200
    assert body["run"]["status"] == "failed"
    assert body["steps"][0]["status"] == "failed"
    assert approvals[0]["approval_status"] == "rejected"


def test_http_api_cancel_run_delegates_to_coordinator(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_active_run_with_queued_step(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(port, "/api/v1/runs/run-active/cancel")

    assert status == 200
    assert body["run"]["status"] == "cancelled"
    assert body["steps"][0]["status"] == "cancelled"


def test_http_api_retry_step_delegates_to_coordinator_and_creates_new_attempt(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _, step_revision, run_revision = _seed_retry_eligible_failed_step(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port,
            "/api/v1/runs/run-retry/steps/command/retry",
            {
                "expected_step_revision": step_revision,
                "expected_run_revision": run_revision,
            },
        )

    assert status == 200
    assert body["run"]["status"] == "queued"
    assert len(body["steps"]) == 2
    assert body["steps"][0]["status"] == "failed"
    assert body["steps"][0]["retried_into_step_id"] == body["steps"][1]["step_id"]
    assert body["steps"][1]["status"] == "queued"
    assert body["steps"][1]["retried_from_step_id"] == "command"


def test_http_api_approve_step_rejects_already_decided_step_without_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    seed_coordinator = _seed_pending_approval(database)
    seed_coordinator.approve_step("step-1")
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port, "/api/v1/runs/run-approval/steps/step-1/approve"
        )

    assert status == 409
    assert "not pending approval" in body["error"]
    assert _database_snapshot(database) == before


def test_http_api_cancel_rejects_terminal_run_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    seed_coordinator = _seed_active_run_with_queued_step(database)
    seed_coordinator.cancel("run-active")
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(port, "/api/v1/runs/run-active/cancel")

    assert status == 409
    assert "invalid run transition" in body["error"]
    assert _database_snapshot(database) == before


def test_http_api_retry_step_rejects_stale_revision_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    seed_coordinator, step_revision, run_revision = _seed_retry_eligible_failed_step(
        database
    )
    # Retry once directly through the coordinator so the run's revision the
    # HTTP request below still carries is no longer current: a genuine CAS
    # conflict, distinct from an eligibility failure.
    seed_coordinator.retry_step(
        "command",
        "command-retry-direct",
        expected_step_revision=step_revision,
        expected_run_revision=run_revision,
    )
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port,
            "/api/v1/runs/run-retry/steps/command/retry",
            {
                "expected_step_revision": step_revision,
                "expected_run_revision": run_revision,
            },
        )

    assert status == 409
    assert "step retry conflict" in body["error"]
    assert _database_snapshot(database) == before


def test_http_api_retry_step_rejects_ineligible_failed_step_without_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        reject_status, _ = _post_json(
            port, "/api/v1/runs/run-approval/steps/step-1/reject"
        )
        assert reject_status == 200
        before = _database_snapshot(database)
        # An operator-rejected step is terminally FAILED but was never a
        # command/provider execution failure, so it is never retry-eligible
        # regardless of which revisions are supplied.
        status, body = _post_json(
            port,
            "/api/v1/runs/run-approval/steps/step-1/retry",
            {"expected_step_revision": 1, "expected_run_revision": 1},
        )

    assert status == 409
    assert "not retry-eligible" in body["error"]
    assert _database_snapshot(database) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"expected_step_revision": 1},
        {"expected_run_revision": 1},
        {"expected_step_revision": "1", "expected_run_revision": 1},
        {"expected_step_revision": True, "expected_run_revision": 1},
    ],
)
def test_http_api_retry_step_rejects_malformed_or_missing_revisions(
    tmp_path, body
) -> None:
    database = tmp_path / "state.sqlite3"
    _, _, _ = _seed_retry_eligible_failed_step(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, response_body = _post_json(
            port, "/api/v1/runs/run-retry/steps/command/retry", body
        )

    assert status == 400
    assert "must be integers" in response_body["error"]
    assert _database_snapshot(database) == before


def test_http_api_mutation_route_rejects_invalid_json_body(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port,
            "/api/v1/runs/run-approval/steps/step-1/approve",
            raw_body=b"{not valid json",
        )

    assert status == 400
    assert "valid JSON" in body["error"]
    assert _database_snapshot(database) == before


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/runs/missing-run/cancel",
        "/api/v1/runs/missing-run/steps/step-1/approve",
        "/api/v1/runs/missing-run/steps/step-1/reject",
        "/api/v1/runs/missing-run/steps/step-1/retry",
    ],
)
def test_http_api_mutation_routes_return_structured_404_for_unknown_run(
    tmp_path, path
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(port, path)

    assert status == 404
    assert body["error"] == "run does not exist: missing-run"
    assert _database_snapshot(database) == before


@pytest.mark.parametrize(
    "suffix", ["steps/missing-step/approve", "steps/missing-step/reject", "steps/missing-step/retry"]
)
def test_http_api_mutation_routes_return_structured_404_for_unknown_step(
    tmp_path, suffix
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(port, f"/api/v1/runs/run-approval/{suffix}")

    assert status == 404
    assert body["error"] == "step does not exist: missing-step"
    assert _database_snapshot(database) == before


def test_http_api_mutation_routes_reject_step_belonging_to_a_different_run(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    _seed_active_run_with_queued_step(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port, "/api/v1/runs/run-active/steps/step-1/approve"
        )

    assert status == 404
    assert body["error"] == "step does not exist: step-1"
    assert _database_snapshot(database) == before


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/runs/run-approval/steps/step-1/approve",
        "/api/v1/runs/run-approval/steps/step-1/reject",
        "/api/v1/runs/run-approval/steps/step-1/retry",
        "/api/v1/runs/run-approval/cancel",
    ],
)
def test_http_api_mutation_routes_reject_unsupported_methods(
    tmp_path, path, method
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", method=method
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)

    assert error.value.code == 404 if method == "GET" else error.value.code == 405
    assert _database_snapshot(database) == before


def test_http_api_unrecognized_mutation_shaped_path_returns_structured_404(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_pending_approval(database)
    before = _database_snapshot(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(coordinator) as port:
        status, body = _post_json(
            port, "/api/v1/runs/run-approval/steps/step-1/complete"
        )

    assert status == 404
    assert body["error"].startswith("unrecognized path")
    assert _database_snapshot(database) == before


def test_http_api_mutation_response_redacts_declared_and_captured_step_fields(
    tmp_path,
) -> None:
    """Decision 0008 applies identically to a successful mutation response."""

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-mixed", objective="Mixed sensitive and pending work")
    coordinator.add_step(
        "run-mixed",
        "command-1",
        objective="Run command",
        command=("true",),
        sandbox_policy=SandboxPolicy(
            kind=SandboxKind.DOCKER, env_passthrough=("API_TOKEN",)
        ),
    )
    coordinator.add_step(
        "run-mixed",
        "step-2",
        objective="Approve me",
        command=("true",),
        approval_required=True,
    )
    coordinator.transition("run-mixed", RunStatus.RUNNING)
    coordinator.start_next_step("run-mixed")
    coordinator.complete_step_from_result(
        "command-1",
        SandboxResult(
            (
                "docker", "run", "--env", "API_TOKEN=runtime-only-secret",
                "python:3.12-slim", "true",
            ),
            0,
            "private stdout",
            "private stderr",
        ),
    )
    read_coordinator = RunCoordinator(StateStore(database, read_only=True))

    with _running_server(read_coordinator) as port:
        status, body = _post_json(
            port, "/api/v1/runs/run-mixed/steps/step-2/approve"
        )

    assert status == 200
    command_step = next(step for step in body["steps"] if step["step_id"] == "command-1")
    assert command_step["command"] == "<redacted>"
    assert command_step["output"]["stdout"] == "<redacted>"
    assert command_step["output"]["stderr"] == "<redacted>"
