from __future__ import annotations

import contextlib
import json
import os
import signal
import threading
import urllib.error
import urllib.request

import pytest

from codex_agentic_os.api import build_server, is_loopback_bind_host, serve_until_stopped
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
    StepStatus,
)
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


def _get_json(port: int, path: str) -> object:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
        return json.loads(response.read().decode("utf-8"))


def _as_json(payload: object) -> object:
    """Round-trip a payload through JSON so tuples compare equal to lists."""

    return json.loads(json.dumps(payload))


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

    assert body == _as_json(_run_payload(coordinator, "run-1"))
    assert body["run"]["run_id"] == "run-1"
    assert [step["step_id"] for step in body["steps"]] == ["step-1"]


def test_http_api_run_detail_matches_cli_run_inspect_output(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    _seed_database(database)
    coordinator = RunCoordinator(StateStore(database, read_only=True))

    main(["run", "inspect", "run-2", "--state-db", str(database)])
    cli_payload = json.loads(capsys.readouterr().out)

    with _running_server(coordinator) as port:
        http_payload = _get_json(port, "/api/v1/runs/run-2")

    assert http_payload == cli_payload


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
