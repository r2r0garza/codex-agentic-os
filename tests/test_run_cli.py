from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import RunCoordinator, RunStatus, StepStatus
from codex_agentic_os.sandboxes import SandboxResult
from codex_agentic_os.state import StateStore


@pytest.mark.parametrize("agent_id", [None, "agent-1"])
def test_cli_creates_queued_run_and_matches_inspection(
    tmp_path, capsys, agent_id
) -> None:
    database = tmp_path / "nested" / "state.sqlite3"
    arguments = [
        "run", "create", "run-1", "--objective", "Build durable work",
        "--state-db", str(database),
    ]
    if agent_id is not None:
        arguments.extend(["--agent-id", agent_id])

    main(arguments)

    created = json.loads(capsys.readouterr().out)
    assert created == {
        "run": {
            "agent_id": agent_id,
            "objective": "Build durable work",
            "output": None,
            "revision": 1,
            "run_id": "run-1",
            "status": "queued",
        },
        "steps": [],
    }
    assert database.is_file()

    main(["run", "inspect", "run-1", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == created


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--objective", "Replacement"], "run already exists: run-1"),
        (["--objective", " "], "run objective must not be empty"),
        (["--objective", "Replacement", "--agent-id", " "], "agent id must not be empty"),
    ],
)
def test_cli_create_rejects_duplicate_and_empty_values_without_mutation(
    tmp_path, capsys, arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Original", agent_id="agent-1")

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "create", "run-1", *arguments, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_adds_ordered_command_steps_and_matches_inspection(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    run = coordinator.create("run-1", objective="Execute durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "First command",
            "--timeout", "12.5", "--state-db", str(database),
            "--", "python", "-c", "print('hello')", "tail",
        ]
    )
    first_payload = json.loads(capsys.readouterr().out)
    assert first_payload["run"]["revision"] == run.revision
    assert first_payload["steps"][0] == {
        "command": ["python", "-c", "print('hello')", "tail"],
        "objective": "First command",
        "output": None,
        "position": 1,
        "revision": 1,
        "run_id": "run-1",
        "status": "queued",
        "step_id": "step-1",
        "timeout": 12.5,
    }

    main(
        [
            "run", "add-step", "run-1", "step-2", "--objective", "Second command",
            "--state-db", str(database), "printf", "done",
        ]
    )
    second_payload = json.loads(capsys.readouterr().out)
    assert [step["step_id"] for step in second_payload["steps"]] == ["step-1", "step-2"]
    assert second_payload["steps"][1]["position"] == 2
    assert second_payload["steps"][1]["command"] == ["printf", "done"]
    assert second_payload["steps"][1]["timeout"] is None

    main(["run", "inspect", "run-1", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == second_payload


@pytest.mark.parametrize(
    ("setup", "arguments", "message"),
    [
        ("missing", ["missing", "step-1", "--objective", "Work", "true"], "run does not exist"),
        ("terminal", ["run-1", "step-1", "--objective", "Work", "true"], "cannot add a step"),
        ("duplicate", ["run-1", "step-1", "--objective", "Work", "true"], "step already exists"),
        ("queued", ["run-1", "step-2", "--objective", " ", "true"], "step objective must not be empty"),
        ("queued", ["run-1", "step-2", "--objective", "Work", "", "tail"], "step command arguments must be non-empty strings"),
        ("queued", ["run-1", "step-2", "--objective", "Work", "--timeout", "0", "true"], "step timeout must be positive"),
    ],
)
def test_cli_add_step_rejections_do_not_mutate_state(
    tmp_path, capsys, setup, arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Original")
    original_step = None
    if setup == "terminal":
        coordinator.transition("run-1", RunStatus.RUNNING)
        original_run = coordinator.transition("run-1", RunStatus.SUCCEEDED)
    elif setup == "duplicate":
        original_step = coordinator.add_step(
            "run-1", "step-1", objective="Original step", command=("echo", "original")
        )

    with pytest.raises(SystemExit) as exit_info:
        command_start = next(
            index for index, value in enumerate(arguments[2:], start=2)
            if not value.startswith("--") and arguments[index - 1] not in {"--objective", "--timeout"}
        )
        main(
            ["run", "add-step", *arguments[:command_start], "--state-db", str(database),
             *arguments[command_start:]]
        )

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == (() if original_step is None else (original_step,))


def test_cli_lists_runs_in_identifier_order_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    second = coordinator.create("run-b", objective="Second")
    first = coordinator.create("run-a", objective="First", agent_id="agent-1")
    coordinator.transition("run-b", RunStatus.RUNNING)

    main(["run", "list", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert [run["run_id"] for run in payload] == ["run-a", "run-b"]
    assert payload[0] == {
        "agent_id": "agent-1", "objective": "First", "output": None,
        "revision": 1, "run_id": "run-a", "status": "queued",
    }
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-a") == first
    assert reloaded.get("run-b").revision == second.revision + 1


def test_cli_lists_empty_database(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()
    main(["run", "list", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == []


def test_cli_list_rejects_missing_database_without_creating_it(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--state-db", str(database)])
    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_list_reports_invalid_run_records(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).put("run", "broken", status="queued", payload={})
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--state-db", str(database)])
    assert exit_info.value.code == 2
    assert "run record has invalid objective: broken" in capsys.readouterr().err


def test_cli_inspects_run_and_steps_in_position_order(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Inspect durable state", agent_id="agent-1")
    coordinator.add_step("run-1", "step-b", objective="Second in lexical order")
    coordinator.add_step("run-1", "step-a", objective="First in lexical order")
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.transition_step("step-b", StepStatus.RUNNING)
    coordinator.transition_step("step-b", StepStatus.SUCCEEDED, output={"result": "ok"})

    main(["run", "inspect", "run-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"] == {
        "agent_id": "agent-1",
        "objective": "Inspect durable state",
        "output": None,
        "revision": 2,
        "run_id": "run-1",
        "status": "running",
    }
    assert [step["step_id"] for step in payload["steps"]] == ["step-b", "step-a"]
    assert payload["steps"][0]["output"] == {"result": "ok"}


def test_cli_inspection_does_not_create_a_missing_database(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_reports_a_missing_run(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run does not exist: missing" in capsys.readouterr().err


def test_cli_cancels_run_and_active_steps(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Cancel durable work")
    coordinator.add_step("run-1", "completed", objective="Already complete")
    coordinator.add_step("run-1", "active", objective="Still running")
    coordinator.transition_step("completed", StepStatus.RUNNING)
    coordinator.transition_step(
        "completed", StepStatus.SUCCEEDED, output={"artifact": "result.json"}
    )
    coordinator.transition_step("active", StepStatus.RUNNING)
    coordinator.transition("run-1", RunStatus.RUNNING)

    main(["run", "cancel", "run-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "cancelled"
    assert [step["status"] for step in payload["steps"]] == [
        "succeeded",
        "cancelled",
    ]
    assert payload["steps"][0]["output"] == {"artifact": "result.json"}
    assert RunCoordinator(StateStore(database)).get("run-1").status is RunStatus.CANCELLED


def test_cli_cancel_rejects_missing_database_without_creating_it(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "cancel", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_cancel_rejects_terminal_run(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Finished work")
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.transition("run-1", RunStatus.SUCCEEDED)

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "cancel", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "invalid run transition" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1").status is RunStatus.SUCCEEDED


@pytest.mark.parametrize("reason", ["interrupted", "timed_out"])
def test_cli_recovers_running_step(tmp_path, capsys, reason) -> None:
    database = tmp_path / f"{reason}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")
    coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10")
    )
    coordinator.start_next_step("run-1")

    main(
        [
            "run",
            "recover",
            "command",
            reason,
            "--detail",
            "worker exited before recording a result",
            "--state-db",
            str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "failed"
    assert payload["run"]["output"] == {
        "failed_step_id": "command",
        "recovery_reason": reason,
    }
    assert payload["steps"][0]["status"] == "failed"
    assert payload["steps"][0]["output"] == {
        "recovery_detail": "worker exited before recording a result",
        "recovery_reason": reason,
    }


def test_cli_recovery_rejections_do_not_mutate_state(tmp_path, capsys) -> None:
    missing_database = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run",
                "recover",
                "command",
                "interrupted",
                "--state-db",
                str(missing_database),
            ]
        )
    assert exit_info.value.code == 2
    assert not missing_database.exists()
    capsys.readouterr()

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued_run = coordinator.create("run-1", objective="Execute durable work")
    queued_step = coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10")
    )

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run",
                "recover",
                "missing",
                "interrupted",
                "--state-db",
                str(database),
            ]
        )
    assert exit_info.value.code == 2
    assert "step does not exist" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run",
                "recover",
                "command",
                "timed_out",
                "--state-db",
                str(database),
            ]
        )
    assert exit_info.value.code == 2
    assert "run must be running" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == queued_run
    assert reloaded.get_step("command") == queued_step


@pytest.mark.parametrize(("sandbox", "image"), [("docker", None), ("podman", "custom:1")])
@pytest.mark.parametrize("returncode", [0, 7])
def test_cli_executes_exactly_one_next_step(
    tmp_path, monkeypatch, capsys, sandbox, image, returncode
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")
    coordinator.add_step(
        "run-1", "step-1", objective="First", command=("printf", "hello"), timeout=4
    )
    coordinator.add_step("run-1", "step-2", objective="Second", command=("true",))
    calls = []

    def execute(self, argv, *, timeout=None):
        calls.append((self.spec, tuple(argv), timeout))
        return SandboxResult((sandbox, *argv), returncode, "hello", "problem")

    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)
    arguments = [
        "run", "execute-next", "run-1", "--sandbox", sandbox,
        "--state-db", str(database),
    ]
    if image is not None:
        arguments.extend(["--image", image])
    main(arguments)

    payload = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    spec, command, timeout = calls[0]
    assert spec.kind.value == sandbox
    assert spec.image == (image or "python:3.12-slim")
    assert spec.network_enabled is False
    assert spec.read_only_root is True
    assert command == ("printf", "hello")
    assert timeout == 4
    assert payload["steps"][0]["status"] == ("succeeded" if returncode == 0 else "failed")
    assert payload["steps"][1]["status"] == "queued"
    assert payload["run"]["status"] == ("running" if returncode == 0 else "failed")
    assert "execution" not in payload


def test_cli_execute_next_reports_empty_queue_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="No queued work")

    main([
        "run", "execute-next", "run-1", "--sandbox", "docker",
        "--state-db", str(database),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["execution"] == {"attempted": False}
    assert payload["run"]["revision"] == original.revision
    assert payload["steps"] == []


def test_cli_execute_next_rejects_empty_image_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued_run = coordinator.create("run-1", objective="Execute durable work")
    queued_step = coordinator.add_step(
        "run-1", "step-1", objective="Work", command=("true",)
    )

    with pytest.raises(SystemExit) as error:
        main([
            "run", "execute-next", "run-1", "--sandbox", "docker", "--image", " ",
            "--state-db", str(database),
        ])

    assert error.value.code == 2
    assert "sandbox image must not be empty" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == queued_run
    assert reloaded.get_step("step-1") == queued_step


@pytest.mark.parametrize("failure", ["coordination", "exception"])
def test_cli_execute_next_failure_preserves_recoverable_state(
    tmp_path, monkeypatch, capsys, failure
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued_run = coordinator.create("run-1", objective="Execute durable work")
    command = None if failure == "coordination" else ("sleep", "10")
    queued_step = coordinator.add_step("run-1", "step-1", objective="Work", command=command)

    if failure == "exception":
        def execute(self, argv, *, timeout=None):
            raise TimeoutError("sandbox command timed out")
        monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)

    with pytest.raises((SystemExit, TimeoutError)) as error:
        main([
            "run", "execute-next", "run-1", "--sandbox", "podman",
            "--state-db", str(database),
        ])

    reloaded = RunCoordinator(StateStore(database))
    if failure == "coordination":
        assert error.value.code == 2
        assert "next step does not have a command" in capsys.readouterr().err
        assert reloaded.get("run-1") == queued_run
        assert reloaded.get_step("step-1") == queued_step
    else:
        assert reloaded.get("run-1").status is RunStatus.RUNNING
        assert reloaded.get_step("step-1").status is StepStatus.RUNNING
