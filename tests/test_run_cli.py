from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import RunCoordinator, RunStatus, StepStatus
from codex_agentic_os.state import StateStore


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
