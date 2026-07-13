from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import AgentRegistry, RunCoordinator, SandboxPolicy
from codex_agentic_os.sandboxes import SandboxKind, SandboxResult
from codex_agentic_os.state import StateStore
from codex_agentic_os.worker import WorkerRunSummary


class _StopLoop(Exception):
    """Raised by a patched sleeper to deterministically end an otherwise infinite loop."""


@pytest.mark.parametrize(
    ("heartbeat_interval", "poll_interval"),
    [("0", "5"), ("-1", "5"), ("5", "0"), ("5", "-1")],
)
def test_cli_worker_run_rejects_non_positive_intervals_without_mutation(
    tmp_path, capsys, heartbeat_interval, poll_interval
) -> None:
    database = tmp_path / "nested" / "state.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "worker",
                "run",
                "--agent-id",
                "agent-1",
                "--heartbeat-interval",
                heartbeat_interval,
                "--poll-interval",
                poll_interval,
                "--state-db",
                str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "positive number of seconds" in capsys.readouterr().err
    assert not database.exists()


def test_cli_worker_run_dispatches_expected_arguments_to_run_worker(
    monkeypatch, tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    captured: dict[str, object] = {}

    def fake_run_worker(coordinator, registry, agent_id, **kwargs):
        captured["coordinator"] = coordinator
        captured["registry"] = registry
        captured["agent_id"] = agent_id
        captured["kwargs"] = kwargs
        return WorkerRunSummary(
            agent_id=agent_id, claimed_run_ids=("run-1",), executed_step_ids=("first",)
        )

    monkeypatch.setattr("codex_agentic_os.cli.run_worker", fake_run_worker)

    main(
        [
            "worker",
            "run",
            "--agent-id",
            "agent-1",
            "--heartbeat-interval",
            "30",
            "--poll-interval",
            "5",
            "--label",
            "Build worker",
            "--state-db",
            str(database),
        ]
    )

    assert isinstance(captured["coordinator"], RunCoordinator)
    assert isinstance(captured["registry"], AgentRegistry)
    assert captured["agent_id"] == "agent-1"
    kwargs = captured["kwargs"]
    assert kwargs["heartbeat_interval"] == 30.0
    assert kwargs["poll_interval"] == 5.0
    assert kwargs["label"] == "Build worker"
    assert callable(kwargs["sandbox_resolver"])
    assert callable(kwargs["adapter_resolver"])

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "agent_id": "agent-1",
        "claimed_run_ids": ["run-1"],
        "executed_step_ids": ["first"],
    }


def test_cli_worker_run_claims_unassigned_run_and_executes_to_completion(
    monkeypatch, tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    coordinator.create("run-1", objective="Deliver")
    coordinator.add_step(
        "run-1",
        "first",
        objective="First",
        command=("true",),
        sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
    )

    def fake_execute(self, argv, *, timeout=None):
        return SandboxResult(tuple(argv), 0, "ok", "")

    monkeypatch.setattr(
        "codex_agentic_os.sandboxes.ContainerSandbox.execute", fake_execute
    )

    def fake_sleep(seconds):
        raise _StopLoop()

    monkeypatch.setattr("codex_agentic_os.worker.time.sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        main(
            [
                "worker",
                "run",
                "--agent-id",
                "agent-1",
                "--heartbeat-interval",
                "60",
                "--poll-interval",
                "1",
                "--state-db",
                str(database),
            ]
        )

    main(["run", "inspect", "run-1", "--state-db", str(database)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "succeeded"
    assert payload["run"]["agent_id"] == "agent-1"
    assert payload["steps"][0]["status"] == "succeeded"

    main(["agent", "inspect", "agent-1", "--state-db", str(database)])
    agent_payload = json.loads(capsys.readouterr().out)
    assert agent_payload["agent_id"] == "agent-1"


def test_cli_worker_run_resumes_existing_agent_identity(
    monkeypatch, tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--label", "Original", "--state-db", str(database)])
    registered = json.loads(capsys.readouterr().out)
    assert registered["revision"] == 1

    def fake_sleep(seconds):
        raise _StopLoop()

    monkeypatch.setattr("codex_agentic_os.worker.time.sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        main(
            [
                "worker",
                "run",
                "--agent-id",
                "agent-1",
                "--heartbeat-interval",
                "60",
                "--poll-interval",
                "1",
                "--state-db",
                str(database),
            ]
        )

    main(["agent", "inspect", "agent-1", "--state-db", str(database)])
    agent_payload = json.loads(capsys.readouterr().out)
    assert agent_payload["label"] == "Original"
    assert agent_payload["revision"] == 2
