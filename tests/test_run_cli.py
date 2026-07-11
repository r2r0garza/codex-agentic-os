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
