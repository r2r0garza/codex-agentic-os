from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import RunCoordinator, RunStatus, StepStatus
from codex_agentic_os.sandboxes import ContainerSandbox, SandboxResult
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


def test_cli_add_step_accepts_hyphen_prefixed_command_after_double_dash(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Negated command",
            "--state-db", str(database), "--", "printf", "-n", "done",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["command"] == ["printf", "-n", "done"]


def test_cli_add_step_rejects_bare_double_dash_as_objective_only(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Checkpoint",
            "--state-db", str(database), "--",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["command"] is None


def test_cli_rejects_unrecognized_arguments_outside_add_step(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "create", "run-1", "--objective", "Build durable work",
                "--state-db", str(database), "unexpected",
            ]
        )

    assert exit_info.value.code == 2
    assert "unrecognized arguments: unexpected" in capsys.readouterr().err
    assert not database.exists()


def test_cli_adds_objective_only_step_and_matches_inspection(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    run = coordinator.create("run-1", objective="Coordinate durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Coordination checkpoint",
            "--state-db", str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["revision"] == run.revision
    assert payload["steps"][0] == {
        "command": None,
        "objective": "Coordination checkpoint",
        "output": None,
        "position": 1,
        "revision": 1,
        "run_id": "run-1",
        "status": "queued",
        "step_id": "step-1",
        "timeout": None,
    }

    main(["run", "inspect", "run-1", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == payload


def test_cli_adds_mixed_objective_only_and_command_steps_in_order(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Mixed durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Checkpoint",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()
    main(
        [
            "run", "add-step", "run-1", "step-2", "--objective", "Command work",
            "--state-db", str(database), "true",
        ]
    )
    capsys.readouterr()
    main(
        [
            "run", "add-step", "run-1", "step-3", "--objective", "Final checkpoint",
            "--state-db", str(database),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert [step["step_id"] for step in payload["steps"]] == ["step-1", "step-2", "step-3"]
    assert [step["position"] for step in payload["steps"]] == [1, 2, 3]
    assert payload["steps"][0]["command"] is None
    assert payload["steps"][1]["command"] == ["true"]
    assert payload["steps"][2]["command"] is None

    main(["run", "inspect", "run-1", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == payload


def test_cli_add_step_rejects_timeout_without_command_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Original")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "add-step", "run-1", "step-1", "--objective", "Work",
                "--timeout", "5", "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "step timeout requires a command" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == ()


def test_cli_claims_run_and_prints_ordered_steps(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Claim work")
    coordinator.add_step("run-1", "step-1", objective="First")
    coordinator.add_step("run-1", "step-2", objective="Second")

    main(["run", "claim", "run-1", "--agent-id", "agent-7", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["agent_id"] == "agent-7"
    assert payload["run"]["revision"] == original.revision + 1
    assert [step["step_id"] for step in payload["steps"]] == ["step-1", "step-2"]
    claimed = RunCoordinator(StateStore(database)).get("run-1")
    assert claimed is not None
    assert claimed.agent_id == "agent-7"
    assert claimed.revision == original.revision + 1


@pytest.mark.parametrize(
    ("setup", "agent_id", "message"),
    [
        ("missing", "agent-2", "run does not exist"),
        ("assigned", "agent-2", "run cannot be claimed"),
        ("running", "agent-2", "run cannot be claimed"),
        ("queued", " ", "agent id must not be empty"),
    ],
)
def test_cli_claim_rejections_do_not_mutate_state(
    tmp_path, capsys, setup, agent_id, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = None
    if setup == "missing":
        coordinator.store.initialize()
    else:
        original = coordinator.create(
            "run-1", objective="Work", agent_id="agent-1" if setup == "assigned" else None
        )
        if setup == "running":
            original = coordinator.transition("run-1", RunStatus.RUNNING)

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "claim", "run-1", "--agent-id", agent_id, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_claim_rejects_missing_database(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "claim", "run-1", "--agent-id", "agent-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_releases_run_claim_and_prints_ordered_steps(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Release work", agent_id="agent-7")
    coordinator.add_step("run-1", "step-1", objective="First")
    coordinator.add_step("run-1", "step-2", objective="Second")

    main(["run", "release", "run-1", "--agent-id", "agent-7", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["agent_id"] is None
    assert payload["run"]["revision"] == original.revision + 1
    assert [step["step_id"] for step in payload["steps"]] == ["step-1", "step-2"]
    released = RunCoordinator(StateStore(database)).get("run-1")
    assert released is not None
    assert released.agent_id is None
    assert released.revision == original.revision + 1


@pytest.mark.parametrize(
    ("setup", "agent_id", "message"),
    [
        ("missing", "agent-1", "run does not exist"),
        ("unassigned", "agent-1", "run claim cannot be released"),
        ("mismatch", "agent-2", "run claim cannot be released"),
        ("running", "agent-1", "run claim cannot be released"),
        ("assigned", " ", "agent id must not be empty"),
    ],
)
def test_cli_release_rejections_do_not_mutate_state(
    tmp_path, capsys, setup, agent_id, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = None
    if setup == "missing":
        coordinator.store.initialize()
    else:
        original = coordinator.create(
            "run-1",
            objective="Work",
            agent_id=None if setup == "unassigned" else "agent-1",
        )
        if setup == "running":
            original = coordinator.transition("run-1", RunStatus.RUNNING)

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "release", "run-1", "--agent-id", agent_id, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_release_rejects_missing_database(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "release", "run-1", "--agent-id", "agent-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_claims_next_eligible_run_and_prints_ordered_steps(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-b", objective="Later")
    original = coordinator.create("run-a", objective="Claim next work")
    coordinator.add_step("run-a", "step-1", objective="First")
    coordinator.add_step("run-a", "step-2", objective="Second")

    main(["run", "claim-next", "--agent-id", "agent-7", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["run_id"] == "run-a"
    assert payload["run"]["agent_id"] == "agent-7"
    assert payload["run"]["revision"] == original.revision + 1
    assert [step["step_id"] for step in payload["steps"]] == ["step-1", "step-2"]
    reloaded = RunCoordinator(StateStore(database))
    claimed = reloaded.get("run-a")
    assert claimed is not None
    assert claimed.agent_id == "agent-7"
    assert reloaded.get("run-b").agent_id is None


def test_cli_claim_next_reports_no_eligible_work_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    assigned = coordinator.create("run-1", objective="Assigned", agent_id="agent-1")

    main(["run", "claim-next", "--agent-id", "agent-2", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == {"claim": {"attempted": False}}
    assert RunCoordinator(StateStore(database)).get("run-1") == assigned


def test_cli_claim_next_rejects_empty_agent_id_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Queued work")

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "claim-next", "--agent-id", " ", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "agent id must not be empty" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_claim_next_rejects_missing_database(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "claim-next", "--agent-id", "agent-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


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


def test_cli_filters_runs_by_repeated_status_without_duplicates(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued = coordinator.create("run-c", objective="Queued")
    running = coordinator.create("run-b", objective="Running")
    succeeded = coordinator.create("run-a", objective="Succeeded")
    coordinator.transition("run-b", RunStatus.RUNNING)
    coordinator.transition("run-a", RunStatus.RUNNING)
    coordinator.transition("run-a", RunStatus.SUCCEEDED)

    main(
        [
            "run", "list", "--status", "running", "--status", "queued",
            "--status", "running", "--state-db", str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert [(run["run_id"], run["status"]) for run in payload] == [
        ("run-b", "running"),
        ("run-c", "queued"),
    ]
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-c") == queued
    assert reloaded.get("run-b").revision == running.revision + 1
    assert reloaded.get("run-a").revision == succeeded.revision + 2


def test_cli_status_filter_can_return_no_matches(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Queued")

    main(["run", "list", "--status", "failed", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == []


def test_cli_filters_runs_by_exact_agent_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    second = coordinator.create("run-c", objective="Second", agent_id="agent-1")
    coordinator.create("run-b", objective="Unassigned")
    first = coordinator.create("run-a", objective="First", agent_id="agent-1")
    coordinator.create("run-d", objective="Different", agent_id="agent-10")

    main(["run", "list", "--agent-id", "agent-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert [run["run_id"] for run in payload] == ["run-a", "run-c"]
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-a") == first
    assert reloaded.get("run-c") == second


def test_cli_agent_filter_can_return_no_matches(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    RunCoordinator(StateStore(database)).create("run-1", objective="Unassigned")

    main(["run", "list", "--agent-id", "missing", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == []


def test_cli_filters_unassigned_runs_with_status_and_stable_order(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-d", objective="Assigned running", agent_id="agent-1")
    queued = coordinator.create("run-c", objective="Unassigned queued")
    running = coordinator.create("run-b", objective="Unassigned running")
    coordinator.create("run-a", objective="Assigned queued", agent_id="agent-2")
    coordinator.transition("run-d", RunStatus.RUNNING)
    coordinator.transition("run-b", RunStatus.RUNNING)

    main(
        [
            "run", "list", "--unassigned", "--status", "running",
            "--state-db", str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert [run["run_id"] for run in payload] == ["run-b"]
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-c") == queued
    assert reloaded.get("run-b").revision == running.revision + 1


def test_cli_unassigned_filter_can_return_no_matches(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    RunCoordinator(StateStore(database)).create(
        "run-1", objective="Assigned", agent_id="agent-1"
    )

    main(["run", "list", "--unassigned", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == []


def test_cli_rejects_conflicting_assignment_filters_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Assigned", agent_id="agent-1")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "list", "--unassigned", "--agent-id", "agent-1",
                "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "--unassigned cannot be combined with --agent-id" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_agent_filter_rejects_empty_value_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Assigned", agent_id="agent-1")

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--agent-id", " ", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "agent id must not be empty" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_list_rejects_invalid_status_choice(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--status", "unknown", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "invalid choice: 'unknown'" in capsys.readouterr().err


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


def test_cli_filtered_list_reports_invalid_unmatched_run_records(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).put("run", "broken", status="queued", payload={})

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--status", "failed", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run record has invalid objective: broken" in capsys.readouterr().err


def test_cli_agent_filtered_list_reports_invalid_unmatched_run_records(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).put("run", "broken", status="queued", payload={})

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--agent-id", "agent-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run record has invalid objective: broken" in capsys.readouterr().err


def test_cli_unassigned_list_reports_invalid_run_records(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).put("run", "broken", status="queued", payload={})

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "list", "--unassigned", "--state-db", str(database)])

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


def test_cli_inspects_one_populated_terminal_step_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    run = coordinator.create("run-1", objective="Inspect one step")
    coordinator.add_step(
        run.run_id,
        "step-1",
        objective="Run command",
        command=("python", "-c", "print('hello')"),
        timeout=12.5,
    )
    coordinator.transition_step("step-1", StepStatus.RUNNING)
    terminal = coordinator.transition_step(
        "step-1", StepStatus.SUCCEEDED, output={"result": "ok"}
    )

    main(["run", "inspect-step", "step-1", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == {
        "command": ["python", "-c", "print('hello')"],
        "objective": "Run command",
        "output": {"result": "ok"},
        "position": 1,
        "revision": 3,
        "run_id": "run-1",
        "status": "succeeded",
        "step_id": "step-1",
        "timeout": 12.5,
    }
    assert RunCoordinator(StateStore(database)).get_step("step-1") == terminal


@pytest.mark.parametrize("status", [StepStatus.QUEUED, StepStatus.RUNNING])
def test_cli_inspects_active_step_statuses(tmp_path, capsys, status) -> None:
    database = tmp_path / f"{status.value}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Inspect active step")
    coordinator.add_step("run-1", "step-1", objective="Active")
    if status is StepStatus.RUNNING:
        coordinator.transition_step("step-1", status)

    main(["run", "inspect-step", "step-1", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out)["status"] == status.value


def test_cli_step_inspection_rejects_missing_state_without_mutation(tmp_path, capsys) -> None:
    missing_database = tmp_path / "missing.sqlite3"
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect-step", "step-1", "--state-db", str(missing_database)])
    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not missing_database.exists()

    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect-step", "missing", "--state-db", str(database)])
    assert exit_info.value.code == 2
    assert "step does not exist: missing" in capsys.readouterr().err


def test_cli_step_inspection_reports_malformed_record_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    malformed = store.put(
        "step",
        "broken",
        status=StepStatus.QUEUED,
        payload={"run_id": "", "position": 1, "objective": "Broken"},
    )

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect-step", "broken", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "step record has invalid run id: broken" in capsys.readouterr().err
    assert StateStore(database).get("step", "broken") == malformed


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


@pytest.mark.parametrize("parent_status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_cli_cancels_one_queued_step_and_prints_ordered_parent(
    tmp_path, capsys, parent_status
) -> None:
    database = tmp_path / f"{parent_status.value}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Cancel selected work")
    first = coordinator.add_step("run-1", "first", objective="First")
    target = coordinator.add_step("run-1", "target", objective="Skip")
    last = coordinator.add_step("run-1", "last", objective="Last")
    if parent_status is RunStatus.RUNNING:
        original_run = coordinator.transition("run-1", RunStatus.RUNNING)

    main(["run", "cancel-step", "target", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == parent_status.value
    assert payload["run"]["revision"] == original_run.revision
    assert [step["step_id"] for step in payload["steps"]] == ["first", "target", "last"]
    assert [step["position"] for step in payload["steps"]] == [1, 2, 3]
    assert payload["steps"][1]["status"] == "cancelled"
    assert payload["steps"][1]["revision"] == target.revision + 1

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("first") == first
    assert reloaded.get_step("last") == last


def test_cli_cancel_step_rejects_missing_database_without_creating_it(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "cancel-step", "step-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("missing", "step does not exist"),
        ("malformed", "step record has invalid run id"),
        ("orphaned", "run does not exist"),
        ("running", "step must be queued"),
        ("succeeded", "step must be queued"),
        ("failed", "step must be queued"),
        ("cancelled", "step must be queued"),
        ("terminal-parent", "run must be active"),
    ],
)
def test_cli_cancel_step_rejections_do_not_mutate_state(
    tmp_path, capsys, setup, message
) -> None:
    database = tmp_path / f"{setup}.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    original_run = None
    original_step = None
    if setup == "missing":
        store.initialize()
    elif setup in {"malformed", "orphaned"}:
        original_step = store.put(
            "step",
            "step-1",
            status=StepStatus.QUEUED,
            payload={
                "run_id": "" if setup == "malformed" else "missing-run",
                "position": 1,
                "objective": "Broken",
            },
        )
    else:
        original_run = coordinator.create("run-1", objective="Work")
        original_step = coordinator.add_step("run-1", "step-1", objective="Target")
        if setup == "terminal-parent":
            coordinator.transition("run-1", RunStatus.RUNNING)
            original_run = coordinator.transition("run-1", RunStatus.SUCCEEDED)
        elif setup == "cancelled":
            original_step = coordinator.transition_step("step-1", StepStatus.CANCELLED)
        else:
            original_step = coordinator.transition_step("step-1", StepStatus.RUNNING)
            if setup != "running":
                original_step = coordinator.transition_step("step-1", StepStatus(setup))

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "cancel-step", "step-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    if original_run is not None:
        assert reloaded.get("run-1") == original_run
    if setup in {"malformed", "orphaned"}:
        assert reloaded.store.get("step", "step-1") == original_step
    else:
        assert reloaded.get_step("step-1") == original_step


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
    coordinator_calls = []

    execute_next_step = RunCoordinator.execute_next_step

    def execute_via_coordinator(self, run_id, executor):
        coordinator_calls.append((run_id, executor))
        return execute_next_step(self, run_id, executor)

    def execute(self, argv, *, timeout=None):
        calls.append((self.spec, tuple(argv), timeout))
        return SandboxResult((sandbox, *argv), returncode, "hello", "problem")

    monkeypatch.setattr(RunCoordinator, "execute_next_step", execute_via_coordinator)
    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)
    arguments = [
        "run", "execute-next", "run-1", "--sandbox", sandbox,
        "--state-db", str(database),
    ]
    if image is not None:
        arguments.extend(["--image", image])
    arguments.extend(
        ["--mount", "/host/repo:/workspace", "--mount", "/host/cache:/cache"]
    )
    main(arguments)

    payload = json.loads(capsys.readouterr().out)
    assert len(coordinator_calls) == 1
    assert coordinator_calls[0][0] == "run-1"
    assert isinstance(coordinator_calls[0][1], ContainerSandbox)
    assert len(calls) == 1
    spec, command, timeout = calls[0]
    assert spec.kind.value == sandbox
    assert spec.image == (image or "python:3.12-slim")
    assert spec.network_enabled is False
    assert spec.read_only_root is True
    assert spec.mounts == (("/host/repo", "/workspace"), ("/host/cache", "/cache"))
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


@pytest.mark.parametrize("mount", ["missing-colon", ":/workspace", "/host:", "/a:/b:ro"])
def test_cli_execute_next_rejects_malformed_mount_without_mutation(
    tmp_path, capsys, mount
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued_run = coordinator.create("run-1", objective="Execute durable work")
    queued_step = coordinator.add_step(
        "run-1", "step-1", objective="Work", command=("true",)
    )

    with pytest.raises(SystemExit) as error:
        main([
            "run", "execute-next", "run-1", "--sandbox", "docker",
            "--mount", mount, "--state-db", str(database),
        ])

    assert error.value.code == 2
    assert "mount must be HOST:CONTAINER" in capsys.readouterr().err
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


@pytest.mark.parametrize(
    ("status", "step_count"),
    [
        (RunStatus.SUCCEEDED, 0),
        (RunStatus.SUCCEEDED, 2),
        (RunStatus.FAILED, 1),
        (RunStatus.CANCELLED, 1),
    ],
)
def test_cli_prunes_one_terminal_run_and_reports_step_count(
    tmp_path, capsys, status, step_count
) -> None:
    database = tmp_path / f"{status.value}-{step_count}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Finished work")
    for index in range(step_count):
        coordinator.add_step("run-1", f"step-{index}", objective="Work")
    coordinator.create("keep", objective="Unrelated work")
    kept_step = coordinator.add_step("keep", "kept", objective="Keep")
    if status is RunStatus.CANCELLED:
        coordinator.cancel("run-1")
    else:
        coordinator.transition("run-1", RunStatus.RUNNING)
        coordinator.transition("run-1", status)

    main(["run", "prune", "run-1", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == {
        "pruned": {"run_id": "run-1", "step_count": step_count}
    }
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") is None
    for index in range(step_count):
        assert reloaded.get_step(f"step-{index}") is None
    assert reloaded.get("keep") is not None
    assert reloaded.get_step("kept") == kept_step


def test_cli_prune_rejects_missing_database_without_creating_it(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "prune", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_prune_rejects_missing_run_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    kept = coordinator.create("keep", objective="Unrelated work")

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "prune", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run does not exist: missing" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("keep") == kept


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_cli_prune_rejects_active_runs_without_mutation(tmp_path, capsys, status) -> None:
    database = tmp_path / f"{status.value}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Active work")
    original_step = coordinator.add_step("run-1", "step-1", objective="Pending")
    if status is RunStatus.RUNNING:
        original_run = coordinator.transition("run-1", status)

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "prune", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run is not terminal" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("step-1") == original_step
