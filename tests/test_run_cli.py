from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import (
    AgentRegistry,
    ProviderMessage,
    RunCoordinator as _RunCoordinator,
    RunStatus,
    SandboxPolicy,
    StepRecoveryReason,
    StepStatus,
)
from codex_agentic_os.sandboxes import ContainerSandbox, SandboxKind, SandboxResult
from codex_agentic_os.state import StateStore


def RunCoordinator(store: StateStore) -> _RunCoordinator:
    """Build a coordinator with the registered identities used by legacy fixtures."""

    registry = AgentRegistry(store)
    for agent_id in ("agent-0", "agent-1", "agent-2", "agent-7", "agent-10"):
        if store.get("agent", agent_id) is None:
            registry.register(agent_id)
    return _RunCoordinator(store)


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
        AgentRegistry(StateStore(database)).register(agent_id)
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


@pytest.mark.parametrize("command", ["create", "claim", "claim-next"])
def test_cli_rejects_unregistered_agent_without_mutation(
    tmp_path, capsys, command
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = _RunCoordinator(StateStore(database))
    original = None
    if command != "create":
        original = coordinator.create("run-1", objective="Work")
    arguments = ["run", command]
    if command != "claim-next":
        arguments.append("run-1")
    if command == "create":
        arguments.extend(["--objective", "Work"])
    arguments.extend(["--agent-id", "missing", "--state-db", str(database)])

    with pytest.raises(SystemExit) as exit_info:
        main(arguments)

    assert exit_info.value.code == 2
    assert "agent is not registered: missing" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


@pytest.mark.parametrize("command", ["create", "claim", "claim-next"])
def test_cli_accepts_registered_agent(tmp_path, capsys, command) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    AgentRegistry(store).register("agent-1")
    coordinator = RunCoordinator(store)
    if command != "create":
        coordinator.create("run-1", objective="Work")
    arguments = ["run", command]
    if command != "claim-next":
        arguments.append("run-1")
    if command == "create":
        arguments.extend(["--objective", "Work"])
    arguments.extend(["--agent-id", "agent-1", "--state-db", str(database)])

    main(arguments)

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["agent_id"] == "agent-1"


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
def test_cli_transitions_run_through_terminal_statuses_with_ordered_steps(
    tmp_path, capsys, terminal
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Operate lifecycle")
    coordinator.add_step("run-1", "step-2", objective="Second", command=("true",))
    coordinator.add_step("run-1", "step-1", objective="First", command=("true",))

    main(["run", "transition", "run-1", "running", "--state-db", str(database)])
    running = json.loads(capsys.readouterr().out)
    assert running["run"]["status"] == "running"
    assert [step["position"] for step in running["steps"]] == [1, 2]

    arguments = ["run", "transition", "run-1", terminal, "--state-db", str(database)]
    if terminal in {"succeeded", "failed"}:
        arguments.extend(["--output", '{"detail": "operator"}'])
    main(arguments)

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == terminal
    assert payload["run"]["output"] == (
        {"detail": "operator"} if terminal in {"succeeded", "failed"} else None
    )
    assert [step["position"] for step in payload["steps"]] == [1, 2]


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("{", "run output must be valid JSON"),
        ("[]", "run output must be a JSON object"),
        ("null", "run output must be a JSON object"),
    ],
)
def test_cli_transition_rejects_invalid_output_without_mutation(
    tmp_path, capsys, output, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Original")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "transition", "run-1", "succeeded", "--output", output,
                "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_transition_rejects_output_for_nonterminal_edge_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Original")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "transition", "run-1", "running", "--output", "{}",
                "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "run output is only valid" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


@pytest.mark.parametrize("setup", ["missing-run", "invalid-edge"])
def test_cli_transition_rejects_missing_run_and_invalid_edge_without_mutation(
    tmp_path, capsys, setup
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Original")
    run_id = "missing" if setup == "missing-run" else "run-1"
    status = "running" if setup == "missing-run" else "succeeded"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "transition", run_id, status, "--state-db", str(database)])

    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert ("run does not exist" if setup == "missing-run" else "invalid run transition") in error
    assert RunCoordinator(StateStore(database)).get("run-1") == original


@pytest.mark.parametrize("with_command", [False, True])
def test_cli_transitions_step_through_terminal_status_with_durable_output(
    tmp_path, capsys, with_command
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Operate step lifecycle")
    first = coordinator.add_step(
        "run-1",
        "step-1",
        objective="Selected step",
        command=("python", "-V") if with_command else None,
        message=None if with_command else ProviderMessage(
            provider="local", content="Selected step"
        ),
    )
    sibling = coordinator.add_step(
        "run-1", "step-2", objective="Untouched sibling", command=("true",)
    )

    main(["run", "transition-step", "step-1", "running", "--state-db", str(database)])
    running = json.loads(capsys.readouterr().out)
    assert running["status"] == "running"
    assert running["revision"] == first.revision + 1

    main(
        [
            "run", "transition-step", "step-1", "succeeded",
            "--output", '{"detail": "operator"}', "--state-db", str(database),
        ]
    )
    terminal = json.loads(capsys.readouterr().out)
    assert terminal["status"] == "succeeded"
    assert terminal["output"] == {"detail": "operator"}
    assert terminal["command"] == (["python", "-V"] if with_command else None)

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_step("step-1").output == {"detail": "operator"}
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("step-2") == sibling


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("{", "step output must be valid JSON"),
        ("[]", "step output must be a JSON object"),
        ("null", "step output must be a JSON object"),
    ],
)
def test_cli_transition_step_rejects_invalid_output_without_mutation(
    tmp_path, capsys, output, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Original")
    original = coordinator.add_step(
        "run-1", "step-1", objective="Original", command=("true",)
    )

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "transition-step", "step-1", "succeeded", "--output", output,
                "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get_step("step-1") == original


@pytest.mark.parametrize("setup", ["missing", "invalid-edge", "invalid-output-edge"])
def test_cli_transition_step_rejections_do_not_mutate_state(
    tmp_path, capsys, setup
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Original")
    original = coordinator.add_step(
        "run-1", "step-1", objective="Original", command=("true",)
    )
    step_id = "missing" if setup == "missing" else "step-1"
    arguments = ["run", "transition-step", step_id, "succeeded"]
    if setup == "invalid-output-edge":
        arguments = ["run", "transition-step", step_id, "running", "--output", "{}"]
    arguments.extend(["--state-db", str(database)])

    with pytest.raises(SystemExit) as exit_info:
        main(arguments)

    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    expected = {
        "missing": "step does not exist",
        "invalid-edge": "invalid step transition",
        "invalid-output-edge": "step output is only valid",
    }
    assert expected[setup] in error
    assert RunCoordinator(StateStore(database)).get_step("step-1") == original


def test_cli_transition_step_rejects_missing_database_without_creating_it(
    tmp_path, capsys
) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "transition-step", "step-1", "running", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


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


def test_cli_adds_and_inspects_provider_message_step(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    RunCoordinator(StateStore(database)).create("run-1", objective="Ask a model")

    arguments = [
        "run", "add-step", "run-1", "model", "--objective", "Summarize",
        "--provider", "openrouter", "--message", "Summarize this change",
        "--model", "example/model", "--system", "Be concise",
        "--temperature", "0.25", "--max-tokens", "321",
        "--state-db", str(database),
    ]
    main(arguments)
    created = json.loads(capsys.readouterr().out)

    main(["run", "inspect-step", "model", "--state-db", str(database)])
    inspected = json.loads(capsys.readouterr().out)
    assert inspected == created["steps"][0]
    assert inspected["message"] == {
        "provider": "openrouter",
        "content": "Summarize this change",
        "model": "example/model",
        "system": "Be concise",
        "temperature": 0.25,
        "max_tokens": 321,
    }


def test_cli_inspects_only_declared_provider_context_step_ids(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.transition_step("first", StepStatus.RUNNING)
    coordinator.transition_step(
        "first", StepStatus.SUCCEEDED, output={"private": "persisted output"}
    )
    coordinator.add_step("run-1", "second", objective="Second", command=("true",))

    main(
        [
            "run", "add-step", "run-1", "model", "--objective", "Synthesize",
            "--provider", "local", "--message", "Use prior results",
            "--context-step", "second", "--context-step", "first",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()
    main(["run", "inspect-step", "model", "--state-db", str(database)])
    inspected = json.loads(capsys.readouterr().out)

    assert inspected["context_step_ids"] == ["second", "first"]
    assert "persisted output" not in json.dumps(inspected)


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


def test_cli_add_step_rejects_bare_double_dash_without_message(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Execute durable work")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "add-step", "run-1", "step-1", "--objective", "Checkpoint",
                "--state-db", str(database), "--",
            ]
        )

    assert exit_info.value.code == 2
    assert "exactly one of command or provider message" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == ()


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


def test_cli_adds_mixed_command_and_provider_message_steps_in_order(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Mixed durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Checkpoint",
            "--provider", "local", "--message", "Checkpoint",
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
            "--provider", "local", "--message", "Final checkpoint",
            "--state-db", str(database),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert [step["step_id"] for step in payload["steps"]] == ["step-1", "step-2", "step-3"]
    assert [step["position"] for step in payload["steps"]] == [1, 2, 3]
    assert payload["steps"][0]["command"] is None
    assert payload["steps"][0]["message"]["content"] == "Checkpoint"
    assert payload["steps"][1]["command"] == ["true"]
    assert payload["steps"][2]["command"] is None
    assert payload["steps"][2]["message"]["content"] == "Final checkpoint"

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


def test_cli_adds_command_step_with_sandbox_policy_and_matches_inspection(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Sandboxed command",
            "--sandbox", "docker", "--image", "python:3.12-slim",
            "--mount", "/host/data:/data", "--env-passthrough", "API_TOKEN",
            "--env-passthrough", "HOME", "--workdir", "/data", "--network",
            "--state-db", str(database), "--", "python", "-c", "print('hello')",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["sandbox_policy"] == {
        "kind": "docker",
        "image": "python:3.12-slim",
        "mounts": [["/host/data", "/data"]],
        "working_dir": "/data",
        "env_passthrough": ["API_TOKEN", "HOME"],
        "network_enabled": True,
    }

    main(["run", "inspect-step", "step-1", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == payload["steps"][0]


def test_cli_add_step_without_sandbox_flags_has_no_sandbox_policy(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")

    main(
        [
            "run", "add-step", "run-1", "step-1", "--objective", "Plain command",
            "--state-db", str(database), "--", "printf", "hi",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert "sandbox_policy" not in payload["steps"][0]


@pytest.mark.parametrize(
    ("extra_arguments", "message"),
    [
        (["--image", "python:3.12-slim"], "sandbox policy requires --sandbox"),
        (
            ["--sandbox", "docker", "--mount", "onlyhost"],
            "mount must be HOST:CONTAINER with non-empty paths",
        ),
        (
            ["--sandbox", "docker", "--workdir", "relative"],
            "working directory must be a non-empty absolute path",
        ),
        (
            ["--sandbox", "docker", "--env-passthrough", "NOT VALID"],
            "env passthrough names must be valid identifiers",
        ),
    ],
)
def test_cli_add_step_rejects_invalid_sandbox_policy_without_mutation(
    tmp_path, capsys, extra_arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Original")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "add-step", "run-1", "step-1", "--objective", "Work",
                *extra_arguments, "--state-db", str(database), "--", "printf", "hi",
            ]
        )

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == ()


def test_cli_add_step_rejects_sandbox_policy_for_provider_message_step(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Original")

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "add-step", "run-1", "step-1", "--objective", "Ask a model",
                "--provider", "openrouter", "--message", "Summarize",
                "--sandbox", "docker", "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "sandbox policy is only valid for command steps" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == ()


def test_cli_claims_run_and_prints_ordered_steps(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Claim work")
    coordinator.add_step("run-1", "step-1", objective="First", command=("true",))
    coordinator.add_step("run-1", "step-2", objective="Second", command=("true",))

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
    coordinator.add_step("run-1", "step-1", objective="First", command=("true",))
    coordinator.add_step("run-1", "step-2", objective="Second", command=("true",))

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
    coordinator.add_step("run-a", "step-1", objective="First", command=("true",))
    coordinator.add_step("run-a", "step-2", objective="Second", command=("true",))

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
    coordinator.add_step(
        "run-1", "step-b", objective="Second in lexical order", command=("true",)
    )
    coordinator.add_step(
        "run-1", "step-a", objective="First in lexical order", command=("true",)
    )
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
    coordinator.add_step("run-1", "step-1", objective="Active", command=("true",))
    if status is StepStatus.RUNNING:
        coordinator.transition_step("step-1", status)

    main(["run", "inspect-step", "step-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == status.value
    assert "failure_kind" not in payload
    assert "retry_eligible" not in payload


@pytest.mark.parametrize("command", ["inspect", "inspect-step"])
@pytest.mark.parametrize(
    ("failure_source", "failure_kind", "retry_eligible"),
    [
        ("command", "definite", True),
        ("provider", "definite", True),
        ("recovery", "uncertain", False),
    ],
)
def test_cli_inspection_classifies_failed_step_retry_eligibility(
    tmp_path, capsys, command, failure_source, failure_kind, retry_eligible
) -> None:
    database = tmp_path / f"{command}-{failure_source}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Inspect failed work")
    if failure_source == "provider":
        coordinator.add_step(
            "run-1",
            "step-1",
            objective="Ask provider",
            message=ProviderMessage(provider="local", content="Review"),
        )
    else:
        coordinator.add_step(
            "run-1", "step-1", objective="Run command", command=("false",)
        )
    coordinator.start_next_step("run-1")
    if failure_source == "command":
        coordinator.complete_step_from_result(
            "step-1", SandboxResult(("docker", "run", "false"), 17, "", "failed")
        )
    elif failure_source == "provider":
        coordinator.fail_step_from_error("step-1", RuntimeError("provider unavailable"))
    else:
        coordinator.recover_running_step("step-1", StepRecoveryReason.TIMED_OUT)

    arguments = ["run", command]
    arguments.append("run-1" if command == "inspect" else "step-1")
    arguments.extend(["--state-db", str(database)])
    main(arguments)

    payload = json.loads(capsys.readouterr().out)
    step = payload["steps"][0] if command == "inspect" else payload
    assert step["failure_kind"] == failure_kind
    assert step["retry_eligible"] is retry_eligible


def test_cli_retries_failed_step_and_inspection_links_both_attempts(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Retry durable work")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("false",), timeout=4
    )
    coordinator.start_next_step("run-1")
    failed_step, failed_run = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "false"), 17, "", "boom")
    )

    main([
        "run", "retry-step", "command", "command-retry",
        "--expected-step-revision", str(failed_step.revision),
        "--expected-run-revision", str(failed_run.revision),
        "--state-db", str(database),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "queued"
    assert payload["steps"][0]["status"] == "failed"
    assert payload["steps"][0]["retried_into_step_id"] == "command-retry"
    assert payload["steps"][1]["status"] == "queued"
    assert payload["steps"][1]["retried_from_step_id"] == "command"

    main(["run", "inspect-step", "command", "--state-db", str(database)])
    original = json.loads(capsys.readouterr().out)
    main(["run", "inspect-step", "command-retry", "--state-db", str(database)])
    retry = json.loads(capsys.readouterr().out)
    assert original["retried_into_step_id"] == "command-retry"
    assert retry["retried_from_step_id"] == "command"


@pytest.mark.parametrize("rejection", ["uncertain", "non-failed", "stale"])
def test_cli_retry_rejections_do_not_mutate_state(
    tmp_path, capsys, rejection
) -> None:
    database = tmp_path / f"{rejection}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Reject unsafe retry")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("false",)
    )
    if rejection == "non-failed":
        step = coordinator.get_step("command")
        run = coordinator.get("run-1")
    else:
        coordinator.start_next_step("run-1")
        if rejection == "uncertain":
            step, run = coordinator.recover_running_step(
                "command", StepRecoveryReason.INTERRUPTED
            )
        else:
            step, run = coordinator.complete_step_from_result(
                "command", SandboxResult(("docker", "false"), 2, "", "boom")
            )
    before_steps = coordinator.list_steps("run-1")
    before_history = coordinator.list_history("run-1")
    expected_run_revision = run.revision - 1 if rejection == "stale" else run.revision

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "retry-step", "command", "command-retry",
            "--expected-step-revision", str(step.revision),
            "--expected-run-revision", str(expected_run_revision),
            "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert ("not retry-eligible" if rejection != "stale" else "retry conflict") in error
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == run
    assert reloaded.list_steps("run-1") == before_steps
    assert reloaded.list_history("run-1") == before_history
    assert reloaded.get_step("command-retry") is None


def test_cli_retry_uses_approval_execution_and_history_paths_after_restart(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    setup = RunCoordinator(StateStore(database))
    setup.create("run-1", objective="Retry approved work")
    setup.add_step(
        "run-1",
        "command",
        objective="Run command",
        command=("false",),
        approval_required=True,
    )
    setup.approve_step("command")
    setup.start_next_step("run-1")
    original, failed_run = setup.complete_step_from_result(
        "command", SandboxResult(("docker", "false"), 9, "", "boom")
    )

    main([
        "run", "retry-step", "command", "command-retry",
        "--expected-step-revision", str(original.revision),
        "--expected-run-revision", str(failed_run.revision),
        "--state-db", str(database),
    ])
    capsys.readouterr()
    main(["run", "approve", "command-retry", "--state-db", str(database)])
    capsys.readouterr()

    monkeypatch.setattr(
        "codex_agentic_os.cli.ContainerSandbox.execute",
        lambda self, argv, timeout=None: SandboxResult(
            ("docker", *argv), 0, "retried", ""
        ),
    )
    main([
        "run", "execute-next", "run-1", "--sandbox", "docker",
        "--state-db", str(database),
    ])
    completed = json.loads(capsys.readouterr().out)

    assert completed["run"]["status"] == "succeeded"
    assert completed["steps"][0]["status"] == "failed"
    assert completed["steps"][1]["status"] == "succeeded"
    assert completed["steps"][0]["retried_into_step_id"] == "command-retry"
    assert completed["steps"][1]["retried_from_step_id"] == "command"
    assert RunCoordinator(StateStore(database)).get_step("command") == original

    main(["run", "history", "run-1", "--state-db", str(database)])
    history = json.loads(capsys.readouterr().out)
    assert [entry["transition"] for entry in history[-6:]] == [
        "step_retried",
        "step_approved",
        "run_started",
        "step_started",
        "step_succeeded",
        "run_succeeded",
    ]
    assert history[-1]["transition"] == "run_succeeded"


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
    coordinator.add_step(
        "run-1", "completed", objective="Already complete", command=("true",)
    )
    coordinator.add_step(
        "run-1", "active", objective="Still running", command=("true",)
    )
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
    first = coordinator.add_step(
        "run-1", "first", objective="First", command=("true",)
    )
    target = coordinator.add_step(
        "run-1", "target", objective="Skip", command=("true",)
    )
    last = coordinator.add_step(
        "run-1", "last", objective="Last", command=("true",)
    )
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
        original_step = coordinator.add_step(
            "run-1", "step-1", objective="Target", command=("true",)
        )
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

    execute_next_step = _RunCoordinator.execute_next_step

    def execute_via_coordinator(self, run_id, executor):
        coordinator_calls.append((run_id, executor))
        return execute_next_step(self, run_id, executor)

    def execute(self, argv, *, timeout=None):
        calls.append((self.spec, tuple(argv), timeout))
        return SandboxResult((sandbox, *argv), returncode, "hello", "problem")

    monkeypatch.setattr(_RunCoordinator, "execute_next_step", execute_via_coordinator)
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
    arguments.extend(["--env", "API_KEY=secret", "--env", "DEBUG=1"])
    arguments.extend(["--workdir", "/workspace"])
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
    assert spec.env == (("API_KEY", "secret"), ("DEBUG", "1"))
    assert spec.working_dir == "/workspace"
    assert command == ("printf", "hello")
    assert timeout == 4
    assert payload["steps"][0]["status"] == ("succeeded" if returncode == 0 else "failed")
    assert payload["steps"][1]["status"] == "queued"
    assert payload["run"]["status"] == ("running" if returncode == 0 else "failed")
    assert "execution" not in payload


@pytest.mark.parametrize("network_enabled", [False, True])
def test_cli_executes_from_persisted_sandbox_policy_and_resolves_worker_env(
    tmp_path, monkeypatch, capsys, network_enabled
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")
    policy = SandboxPolicy(
        kind=SandboxKind.PODMAN,
        image="custom:1",
        mounts=(("/host/repo", "/workspace"),),
        env_passthrough=("API_TOKEN", "DEBUG"),
        working_dir="/workspace",
        network_enabled=network_enabled,
    )
    coordinator.add_step(
        "run-1",
        "step-1",
        objective="First",
        command=("printf", "hello"),
        timeout=4,
        sandbox_policy=policy,
    )
    coordinator.add_step(
        "run-1", "step-2", objective="Second", command=("true",),
        sandbox_policy=policy,
    )
    monkeypatch.setenv("API_TOKEN", "worker-secret")
    monkeypatch.setenv("DEBUG", "1")
    calls = []

    def execute(self, argv, *, timeout=None):
        calls.append((self.spec, tuple(argv), timeout))
        return SandboxResult(("podman", *argv), 0, "hello", "")

    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)

    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    first_payload = json.loads(capsys.readouterr().out)
    monkeypatch.setenv("API_TOKEN", "changed-worker-secret")
    main(["run", "execute-next", "run-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert len(calls) == 2
    spec, command, timeout = calls[0]
    assert spec.kind is SandboxKind.PODMAN
    assert spec.image == "custom:1"
    assert spec.mounts == (("/host/repo", "/workspace"),)
    assert spec.env == (("API_TOKEN", "worker-secret"), ("DEBUG", "1"))
    assert spec.working_dir == "/workspace"
    assert spec.network_enabled is network_enabled
    assert command == ("printf", "hello")
    assert timeout == 4
    assert first_payload["steps"][0]["status"] == "succeeded"
    assert first_payload["steps"][1]["status"] == "queued"
    assert calls[1][0].env == (
        ("API_TOKEN", "changed-worker-secret"),
        ("DEBUG", "1"),
    )
    assert payload["steps"][1]["status"] == "succeeded"
    assert "worker-secret" not in database.read_bytes().decode("utf-8", errors="ignore")
    assert "changed-worker-secret" not in database.read_bytes().decode(
        "utf-8", errors="ignore"
    )


@pytest.mark.parametrize(
    "flags",
    [
        ("--sandbox", "docker"),
        ("--image", "other:1"),
        ("--mount", "/other:/workspace"),
        ("--env", "TOKEN=secret"),
        ("--workdir", "/other"),
        ("--network",),
    ],
)
def test_cli_rejects_flags_that_conflict_with_persisted_policy_before_mutation(
    tmp_path, capsys, flags
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    run = coordinator.create("run-1", objective="Execute durable work")
    step = coordinator.add_step(
        "run-1", "step-1", objective="First", command=("true",),
        sandbox_policy=SandboxPolicy(kind=SandboxKind.PODMAN),
    )

    with pytest.raises(SystemExit) as error:
        main([
            "run", "execute-next", "run-1", *flags,
            "--state-db", str(database),
        ])

    assert error.value.code == 2
    assert "per-invocation sandbox flags are not allowed" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == run
    assert reloaded.get_step("step-1") == step


def test_cli_missing_persisted_environment_name_does_not_start_step(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    run = coordinator.create("run-1", objective="Execute durable work")
    step = coordinator.add_step(
        "run-1", "step-1", objective="First", command=("true",),
        sandbox_policy=SandboxPolicy(
            kind=SandboxKind.DOCKER, env_passthrough=("MISSING_TOKEN",)
        ),
    )
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    with pytest.raises(SystemExit) as error:
        main(["run", "execute-next", "run-1", "--state-db", str(database)])

    assert error.value.code == 2
    assert "environment variable is not set: MISSING_TOKEN" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == run
    assert reloaded.get_step("step-1") == step


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


def test_cli_executes_provider_message_without_sandbox(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse, ChatUsage

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable model work")
    coordinator.add_step(
        "run-1",
        "model-1",
        objective="Ask model",
        message=ProviderMessage(
            provider="ollama",
            content="Hello",
            model="custom-local",
            system="Be concise",
            temperature=0.2,
            max_tokens=64,
        ),
    )
    captured = {}
    monkeypatch.setenv("PROVIDER_SECRET", "never-persist-this-secret")

    class Adapter:
        def complete(self, request):
            captured["request"] = request
            return ChatResponse(
                "Hi",
                model="custom-local",
                raw={"id": "one"},
                usage=ChatUsage(
                    available=True,
                    input_tokens=11,
                    output_tokens=2,
                    raw={"prompt_tokens": 11, "completion_tokens": 2},
                ),
            )

    def build_adapter(spec):
        captured["spec"] = spec
        return Adapter()

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", build_adapter)

    main(["run", "execute-next", "run-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert captured["spec"].kind.value == "ollama"
    assert captured["spec"].model == "custom-local"
    assert captured["request"].messages[0].content == "Be concise"
    assert captured["request"].messages[1].content == "Hello"
    assert captured["request"].temperature == 0.2
    assert captured["request"].max_tokens == 64
    assert payload["steps"][0]["status"] == "succeeded"
    assert payload["steps"][0]["output"] == {
        "content": "Hi",
        "model": "custom-local",
        "raw": {"id": "one"},
        "usage": {
            "available": True,
            "input_tokens": 11,
            "output_tokens": 2,
            "raw": {"prompt_tokens": 11, "completion_tokens": 2},
            "unavailable_reason": None,
        },
    }
    assert payload["run"]["status"] == "succeeded"
    persisted = json.dumps(payload["steps"][0]["output"])
    assert "never-persist-this-secret" not in persisted
    assert "Be concise" not in persisted
    assert "Hello" not in persisted


def test_cli_execute_next_records_provider_failure_without_orphaned_claim(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable model work")
    coordinator.add_step(
        "run-1",
        "model-1",
        objective="Ask model",
        message=ProviderMessage(provider="anthropic", content="Hello"),
    )

    class Adapter:
        def complete(self, request):
            raise RuntimeError("chat request failed: HTTP Error 401: Unauthorized")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())

    main(["run", "execute-next", "run-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"][0]["status"] == "failed"
    assert payload["steps"][0]["output"] == {
        "error": "chat request failed: HTTP Error 401: Unauthorized",
        "error_type": "RuntimeError",
    }
    assert payload["run"]["status"] == "failed"
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1").status is RunStatus.FAILED
    assert reloaded.get_step("model-1").status is StepStatus.FAILED


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


@pytest.mark.parametrize("env", ["KEY", "=value", "KEY=", "=", ""])
def test_cli_execute_next_rejects_malformed_env_without_mutation(
    tmp_path, capsys, env
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
            "--env", env, "--state-db", str(database),
        ])

    assert error.value.code == 2
    assert "env var must be KEY=VALUE" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == queued_run
    assert reloaded.get_step("step-1") == queued_step


@pytest.mark.parametrize("workdir", ["", " ", "workspace", "./workspace"])
def test_cli_execute_next_rejects_invalid_workdir_without_mutation(
    tmp_path, capsys, workdir
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
            "--workdir", workdir, "--state-db", str(database),
        ])

    assert error.value.code == 2
    assert "working directory must be a non-empty absolute path" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == queued_run
    assert reloaded.get_step("step-1") == queued_step


def test_cli_execute_next_network_flag_enables_bridge_networking(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute durable work")
    coordinator.add_step("run-1", "step-1", objective="First", command=("true",))
    calls = []

    execute_next_step = _RunCoordinator.execute_next_step

    def execute_via_coordinator(self, run_id, executor):
        return execute_next_step(self, run_id, executor)

    def execute(self, argv, *, timeout=None):
        calls.append(self.spec)
        return SandboxResult(("docker", *argv), 0, "", "")

    monkeypatch.setattr(_RunCoordinator, "execute_next_step", execute_via_coordinator)
    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)

    main([
        "run", "execute-next", "run-1", "--sandbox", "docker",
        "--workdir", "/workspace", "--network", "--state-db", str(database),
    ])
    capsys.readouterr()

    assert len(calls) == 1
    assert calls[0].network_enabled is True
    assert calls[0].working_dir == "/workspace"


def test_cli_execute_next_help_identifies_network_as_explicit_opt_in(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["run", "execute-next", "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "--network" in output
    assert "opt-in" in output
    assert "isolated" in output


def test_cli_execute_next_failure_preserves_recoverable_state(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued_run = coordinator.create("run-1", objective="Execute durable work")
    coordinator.add_step(
        "run-1", "step-1", objective="Work", command=("sleep", "10")
    )

    def execute(self, argv, *, timeout=None):
        raise TimeoutError("sandbox command timed out")
    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)

    with pytest.raises(TimeoutError):
        main([
            "run", "execute-next", "run-1", "--sandbox", "podman",
            "--state-db", str(database),
        ])

    reloaded = RunCoordinator(StateStore(database))
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
        coordinator.add_step(
            "run-1", f"step-{index}", objective="Work", command=("true",)
        )
    coordinator.create("keep", objective="Unrelated work")
    kept_step = coordinator.add_step(
        "keep", "kept", objective="Keep", command=("true",)
    )
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
    original_step = coordinator.add_step(
        "run-1", "step-1", objective="Pending", command=("true",)
    )
    if status is RunStatus.RUNNING:
        original_run = coordinator.transition("run-1", status)

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "prune", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run is not terminal" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("step-1") == original_step


def test_cli_history_reports_creation_claim_and_transition_in_order(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Track lifecycle")
    coordinator.claim("run-1", "agent-1")
    coordinator.transition("run-1", RunStatus.RUNNING)

    main(["run", "history", "run-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert [
        (entry["transition"], entry["status"], entry["agent_id"]) for entry in payload
    ] == [
        ("created", "queued", None),
        ("claimed", "queued", "agent-1"),
        ("transitioned", "running", "agent-1"),
    ]
    assert [entry["sequence"] for entry in payload] == [1, 2, 3]


def test_cli_history_reconstructs_mixed_command_and_provider_run_across_processes(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Mixed durable work")
    coordinator.add_step("run-1", "step-1", objective="Checkpoint", command=("true",))
    coordinator.add_step(
        "run-1",
        "step-2",
        objective="Summarize",
        message=ProviderMessage(provider="ollama", content="Summarize"),
    )

    def execute(self, argv, *, timeout=None):
        return SandboxResult(("docker", *argv), 0, "ok", "")

    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)
    main([
        "run", "execute-next", "run-1", "--sandbox", "docker",
        "--state-db", str(database),
    ])
    capsys.readouterr()

    class Adapter:
        def complete(self, request):
            return ChatResponse("Summary", model="served-model")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    capsys.readouterr()

    main(["run", "history", "run-1", "--state-db", str(database)])
    payload = json.loads(capsys.readouterr().out)

    assert [entry["transition"] for entry in payload] == [
        "created",
        "run_started",
        "step_started",
        "step_succeeded",
        "step_started",
        "step_succeeded",
        "run_succeeded",
    ]
    step_scoped = [
        (entry["step_id"], entry["execution_kind"])
        for entry in payload
        if entry["step_id"] is not None
    ]
    assert step_scoped == [
        ("step-1", "command"),
        ("step-1", "command"),
        ("step-2", "provider"),
        ("step-2", "provider"),
    ]
    for entry in payload:
        assert set(entry) == {
            "run_id",
            "sequence",
            "transition",
            "status",
            "agent_id",
            "execution_kind",
            "step_id",
            "retried_step_id",
            "context_step_ids",
        }

    reconstructed = RunCoordinator(StateStore(database)).list_history("run-1")
    assert [entry["transition"] for entry in payload] == [
        entry.transition for entry in reconstructed
    ]


def test_cli_execute_next_reports_unresolved_context_reference_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage(provider="ollama", content="Use the result"),
        context_step_ids=("first",),
    )
    coordinator.cancel_step("first")

    original_run = coordinator.get("run-1")
    original_model = coordinator.get_step("model")

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "execute-next", "run-1", "--state-db", str(database)])
    assert exit_info.value.code == 2
    assert "unresolved context references: model" in capsys.readouterr().err

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("model") == original_model


def test_cli_history_rejects_missing_run_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "history", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run does not exist: missing" in capsys.readouterr().err


def test_cli_history_rejects_missing_database_without_creating_it(
    tmp_path, capsys
) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "history", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_history_does_not_mutate_run_or_step_state(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Inspect history")
    original_step = coordinator.add_step(
        "run-1", "step-1", objective="Work", command=("true",)
    )

    main(["run", "history", "run-1", "--state-db", str(database)])
    capsys.readouterr()

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("step-1") == original_step


def test_cli_usage_reports_available_and_unavailable_usage_in_order(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse, ChatUsage

    database = tmp_path / "state.sqlite3"
    monkeypatch.setenv("PROVIDER_SECRET", "never-persist-this-secret")
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Mixed usage work")
    coordinator.add_step("run-1", "step-1", objective="Checkpoint", command=("true",))
    coordinator.add_step(
        "run-1",
        "step-2",
        objective="Ask model",
        message=ProviderMessage(provider="ollama", content="Hello", system="Be concise"),
    )
    coordinator.add_step(
        "run-1",
        "step-3",
        objective="Ask again",
        message=ProviderMessage(provider="anthropic", content="Follow up"),
    )

    def execute(self, argv, *, timeout=None):
        return SandboxResult(("docker", *argv), 0, "ok", "")

    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)
    main([
        "run", "execute-next", "run-1", "--sandbox", "docker",
        "--state-db", str(database),
    ])
    capsys.readouterr()

    responses = iter([
        ChatResponse(
            "Hi", model="served-model", raw={"id": "one"},
            usage=ChatUsage(
                available=True, input_tokens=11, output_tokens=2,
                raw={"prompt_tokens": 11, "completion_tokens": 2},
            ),
        ),
        ChatResponse(
            "Ok", model="served-model-2",
            usage=ChatUsage(
                available=False,
                unavailable_reason="provider omitted usage block",
            ),
        ),
    ])

    class Adapter:
        def complete(self, request):
            return next(responses)

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    capsys.readouterr()
    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    capsys.readouterr()

    main(["run", "usage", "run-1", "--state-db", str(database)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["run_id"] == "run-1"
    assert [step["step_id"] for step in payload["steps"]] == ["step-2", "step-3"]
    assert payload["steps"][0] == {
        "step_id": "step-2",
        "position": 2,
        "status": "succeeded",
        "provider": "ollama",
        "model": "served-model",
        "usage": {
            "available": True,
            "input_tokens": 11,
            "output_tokens": 2,
            "raw": {"prompt_tokens": 11, "completion_tokens": 2},
            "unavailable_reason": None,
        },
    }
    assert payload["steps"][1] == {
        "step_id": "step-3",
        "position": 3,
        "status": "succeeded",
        "provider": "anthropic",
        "model": "served-model-2",
        "usage": {
            "available": False,
            "input_tokens": None,
            "output_tokens": None,
            "raw": None,
            "unavailable_reason": "provider omitted usage block",
        },
    }
    assert payload["aggregate"] == {
        "steps_with_usage_available": 1,
        "steps_with_usage_unavailable": 1,
        "input_tokens": 11,
        "output_tokens": 2,
    }
    persisted = json.dumps(payload)
    assert "never-persist-this-secret" not in persisted
    assert "Be concise" not in persisted
    assert "Hello" not in persisted
    assert "Follow up" not in persisted


def test_cli_usage_marks_queued_provider_step_as_unavailable_without_fabrication(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Not yet dispatched")
    coordinator.add_step(
        "run-1", "step-1", objective="Ask model",
        message=ProviderMessage(provider="ollama", content="Hello"),
    )

    main(["run", "usage", "run-1", "--state-db", str(database)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["steps"] == [{
        "step_id": "step-1",
        "position": 1,
        "status": "queued",
        "provider": "ollama",
        "model": None,
        "usage": {
            "available": False,
            "input_tokens": None,
            "output_tokens": None,
            "raw": None,
            "unavailable_reason": "no usage recorded for step status queued",
        },
    }]
    assert payload["aggregate"] == {
        "steps_with_usage_available": 0,
        "steps_with_usage_unavailable": 1,
        "input_tokens": None,
        "output_tokens": None,
    }


def test_cli_usage_reports_command_only_run_with_no_provider_steps(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Command only")
    coordinator.add_step("run-1", "step-1", objective="Checkpoint", command=("true",))

    main(["run", "usage", "run-1", "--state-db", str(database)])
    payload = json.loads(capsys.readouterr().out)

    assert payload["steps"] == []
    assert payload["aggregate"] == {
        "steps_with_usage_available": 0,
        "steps_with_usage_unavailable": 0,
        "input_tokens": None,
        "output_tokens": None,
    }


def test_cli_usage_rejects_missing_run_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "usage", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "run does not exist: missing" in capsys.readouterr().err


def test_cli_usage_rejects_missing_database_without_creating_it(
    tmp_path, capsys
) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "usage", "run-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_usage_does_not_mutate_run_or_step_state(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Inspect usage")
    original_step = coordinator.add_step(
        "run-1", "step-1", objective="Ask model",
        message=ProviderMessage(provider="ollama", content="Hello"),
    )

    main(["run", "usage", "run-1", "--state-db", str(database)])
    capsys.readouterr()

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("step-1") == original_step


def test_cli_approval_flow_is_sanitized_and_reconstructible(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    main([
        "agent", "register", "operator-1", "--state-db", str(database),
    ])
    capsys.readouterr()
    main([
        "run", "create", "run-1", "--objective", "Approval flow",
        "--agent-id", "operator-1", "--state-db", str(database),
    ])
    capsys.readouterr()
    main([
        "run", "add-step", "run-1", "step-1", "--objective", "Sensitive command",
        "--approval-required", "--state-db", str(database), "--",
        "sh", "-c", "TOKEN=secret do-work",
    ])
    capsys.readouterr()

    main(["run", "approvals", "run-1", "--state-db", str(database)])
    pending = json.loads(capsys.readouterr().out)
    assert pending == [{
        "approval_required": True,
        "approval_status": "pending",
        "deciding_agent_id": None,
        "execution_kind": "command",
        "objective": "Sensitive command",
        "position": 1,
        "requesting_agent_id": "operator-1",
        "run_id": "run-1",
        "step_id": "step-1",
        "step_status": "queued",
    }]
    assert "secret" not in json.dumps(pending)

    main([
        "run", "approve", "step-1", "--agent-id", "operator-1",
        "--state-db", str(database),
    ])
    capsys.readouterr()
    main(["run", "approvals", "run-1", "--state-db", str(database)])
    approved = json.loads(capsys.readouterr().out)
    assert approved[0]["approval_status"] == "approved"
    assert approved[0]["deciding_agent_id"] == "operator-1"

    main(["run", "history", "run-1", "--state-db", str(database)])
    history = json.loads(capsys.readouterr().out)
    assert history[-1]["transition"] == "step_approved"
    assert history[-1]["agent_id"] == "operator-1"


def test_cli_rejects_pending_step_and_records_terminal_decision(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Reject flow")
    coordinator.add_step(
        "run-1", "step-1", objective="Provider request",
        message=ProviderMessage(provider="ollama", content="private request"),
        approval_required=True,
    )

    main(["run", "reject", "step-1", "--state-db", str(database)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["status"] == "failed"
    main(["run", "approvals", "run-1", "--state-db", str(database)])
    requests = json.loads(capsys.readouterr().out)
    assert requests[0]["approval_status"] == "rejected"
    assert requests[0]["step_status"] == "failed"
    assert requests[0]["execution_kind"] == "provider"
    assert "private request" not in json.dumps(requests)


@pytest.mark.parametrize("command", ["approve", "reject"])
def test_cli_approval_decision_rejections_do_not_mutate_state(
    tmp_path, capsys, command
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original_run = coordinator.create("run-1", objective="Decision conflicts")
    original_step = coordinator.add_step(
        "run-1", "step-1", objective="Pending", command=("true",),
        approval_required=True,
    )

    with pytest.raises(SystemExit) as exit_info:
        main(["run", command, "missing", "--state-db", str(database)])
    assert exit_info.value.code == 2
    assert "step does not exist: missing" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("step-1") == original_step

    reloaded.approve_step("step-1")
    decided_run = reloaded.get("run-1")
    decided_step = reloaded.get_step("step-1")
    decided_history = reloaded.list_history("run-1")
    with pytest.raises(SystemExit) as exit_info:
        main(["run", command, "step-1", "--state-db", str(database)])
    assert exit_info.value.code == 2
    assert "step is not pending approval" in capsys.readouterr().err
    final = RunCoordinator(StateStore(database))
    assert final.get("run-1") == decided_run
    assert final.get_step("step-1") == decided_step
    assert final.list_history("run-1") == decided_history


def test_cli_run_staleness_reports_fresh_owner_and_evidence(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    capsys.readouterr()
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()

    main([
        "run", "staleness", "run-1", "--threshold-seconds", "999999999",
        "--state-db", str(database),
    ])
    evaluation = json.loads(capsys.readouterr().out)

    assert evaluation["run_id"] == "run-1"
    assert evaluation["agent_id"] == "agent-1"
    assert evaluation["threshold_seconds"] == 999999999
    assert evaluation["stale"] is False
    assert isinstance(evaluation["last_seen"], str)
    assert isinstance(evaluation["evaluated_at"], str)


def test_cli_run_staleness_detects_owner_past_threshold(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    capsys.readouterr()
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()

    main([
        "run", "staleness", "run-1", "--threshold-seconds", "1e-9",
        "--state-db", str(database),
    ])
    evaluation = json.loads(capsys.readouterr().out)

    assert evaluation["stale"] is True


def test_cli_run_staleness_does_not_mutate_run_or_agent_state(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    capsys.readouterr()
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()
    original_run = RunCoordinator(StateStore(database)).get("run-1")
    original_agent = AgentRegistry(StateStore(database)).get("agent-1")

    main([
        "run", "staleness", "run-1", "--threshold-seconds", "60",
        "--state-db", str(database),
    ])
    capsys.readouterr()

    assert RunCoordinator(StateStore(database)).get("run-1") == original_run
    assert AgentRegistry(StateStore(database)).get("agent-1") == original_agent


def test_cli_run_staleness_rejects_missing_run_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Unrelated")

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "staleness", "missing", "--threshold-seconds", "60",
            "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    assert "run does not exist: missing" in capsys.readouterr().err


def test_cli_run_staleness_rejects_unclaimed_run_without_mutation(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Unclaimed")

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "staleness", "run-1", "--threshold-seconds", "60",
            "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    assert "run is not claimed: run-1" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


@pytest.mark.parametrize("threshold", ["0", "-1"])
def test_cli_run_staleness_rejects_non_positive_threshold_without_mutation(
    tmp_path, capsys, threshold
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    original = coordinator.create("run-1", objective="Build feature", agent_id="agent-1")

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "staleness", "run-1", "--threshold-seconds", threshold,
            "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    assert "threshold must be a positive" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_run_reassign_claim_transfers_ownership_and_records_provenance(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    main(["agent", "register", "agent-2", "--state-db", str(database)])
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()

    main([
        "run", "reassign-claim", "run-1", "agent-2",
        "--expected-agent-id", "agent-1", "--expected-revision", "1",
        "--threshold-seconds", "1e-9", "--state-db", str(database),
    ])
    reassigned = json.loads(capsys.readouterr().out)

    assert reassigned["run"]["agent_id"] == "agent-2"
    assert reassigned["run"]["revision"] == 2

    main(["run", "history", "run-1", "--state-db", str(database)])
    history = json.loads(capsys.readouterr().out)
    assert history[-1]["transition"] == "claim_reassigned"
    assert history[-1]["agent_id"] == "agent-2"
    assert RunCoordinator(StateStore(database)).get("run-1").agent_id == "agent-2"


def test_cli_run_reassign_claim_preserves_running_step_state(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    main(["agent", "register", "agent-2", "--state-db", str(database)])
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    main([
        "run", "add-step", "run-1", "step-1", "--objective", "Execute",
        "--state-db", str(database), "--", "true",
    ])
    main(["run", "transition", "run-1", "running", "--state-db", str(database)])
    main([
        "run", "transition-step", "step-1", "running", "--state-db", str(database),
    ])
    capsys.readouterr()
    original_run = RunCoordinator(StateStore(database)).get("run-1")
    original_step = RunCoordinator(StateStore(database)).get_step("step-1")
    assert original_run.status is RunStatus.RUNNING

    main([
        "run", "reassign-claim", "run-1", "agent-2",
        "--expected-agent-id", "agent-1", "--expected-revision",
        str(original_run.revision), "--threshold-seconds", "1e-9",
        "--state-db", str(database),
    ])
    capsys.readouterr()

    reloaded_run = RunCoordinator(StateStore(database)).get("run-1")
    reloaded_step = RunCoordinator(StateStore(database)).get_step("step-1")
    assert reloaded_run.agent_id == "agent-2"
    assert reloaded_run.status is RunStatus.RUNNING
    assert reloaded_step == original_step


def test_cli_run_reassign_claim_rejects_fresh_owner_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    main(["agent", "register", "agent-2", "--state-db", str(database)])
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()
    original = RunCoordinator(StateStore(database)).get("run-1")

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "reassign-claim", "run-1", "agent-2",
            "--expected-agent-id", "agent-1", "--expected-revision", "1",
            "--threshold-seconds", "999999999", "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    assert "run claim cannot be reassigned: run-1" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == original


def test_cli_run_reassign_claim_rejects_stale_expected_revision_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    main(["agent", "register", "agent-2", "--state-db", str(database)])
    main(["agent", "register", "agent-3", "--state-db", str(database)])
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()

    main([
        "run", "reassign-claim", "run-1", "agent-2",
        "--expected-agent-id", "agent-1", "--expected-revision", "1",
        "--threshold-seconds", "1e-9", "--state-db", str(database),
    ])
    capsys.readouterr()
    winner = RunCoordinator(StateStore(database)).get("run-1")
    assert winner.agent_id == "agent-2"

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "reassign-claim", "run-1", "agent-3",
            "--expected-agent-id", "agent-1", "--expected-revision", "1",
            "--threshold-seconds", "1e-9", "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    assert "run claim cannot be reassigned: run-1" in capsys.readouterr().err
    assert RunCoordinator(StateStore(database)).get("run-1") == winner


def test_cli_run_reassign_claim_rejects_missing_run_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Unrelated")
    capsys.readouterr()

    with pytest.raises(SystemExit) as exit_info:
        main([
            "run", "reassign-claim", "missing", "agent-1",
            "--expected-agent-id", "agent-0", "--expected-revision", "1",
            "--threshold-seconds", "60", "--state-db", str(database),
        ])

    assert exit_info.value.code == 2
    assert "run does not exist: missing" in capsys.readouterr().err


def test_cli_run_reassign_claim_produces_exactly_one_winner_under_contention(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"
    main(["agent", "register", "agent-1", "--state-db", str(database)])
    main(["agent", "register", "agent-2", "--state-db", str(database)])
    main(["agent", "register", "agent-3", "--state-db", str(database)])
    main([
        "run", "create", "run-1", "--objective", "Build feature",
        "--agent-id", "agent-1", "--state-db", str(database),
    ])
    capsys.readouterr()

    def attempt(replacement: str) -> bool:
        try:
            main([
                "run", "reassign-claim", "run-1", replacement,
                "--expected-agent-id", "agent-1", "--expected-revision", "1",
                "--threshold-seconds", "1e-9", "--state-db", str(database),
            ])
            return True
        except SystemExit:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("agent-2", "agent-3")))
    capsys.readouterr()

    assert results.count(True) == 1
    final = RunCoordinator(StateStore(database)).get("run-1")
    assert final.agent_id in ("agent-2", "agent-3")
    assert final.revision == 2
    reassignment_entries = [
        entry
        for entry in RunCoordinator(StateStore(database)).list_history("run-1")
        if entry.transition == "claim_reassigned"
    ]
    assert len(reassignment_entries) == 1
    assert reassignment_entries[0].agent_id == final.agent_id


PLAN_PROPOSAL_CONTENT = (
    '{"steps": ['
    '{"objective": "Write the fix", "execution_kind": "command", '
    '"command": ["pytest"], "sandbox_policy": {"kind": "docker"}}, '
    '{"objective": "Summarize the change", "execution_kind": "provider", '
    '"message": {"provider": "ollama", "content": "Summarize the diff"}}'
    "]}"
)

PLAN_PROPOSAL_STEPS_PAYLOAD = [
    {
        "step_id": "draft-1-step-1",
        "objective": "Write the fix",
        "execution_kind": "command",
        "command": ["pytest"],
        "timeout": None,
        "sandbox_policy": {
            "kind": "docker",
            "image": "python:3.12-slim",
            "mounts": [],
            "working_dir": None,
            "env_passthrough": [],
            "network_enabled": False,
        },
    },
    {
        "step_id": "draft-1-step-2",
        "objective": "Summarize the change",
        "execution_kind": "provider",
        "command": None,
        "timeout": None,
        "message": {
            "provider": "ollama",
            "content": "Summarize the diff",
            "model": None,
            "system": None,
            "temperature": None,
            "max_tokens": None,
        },
    },
]


def test_cli_plan_dispatches_objective_and_persists_draft_without_queuing_steps(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    captured = {}

    class Adapter:
        def complete(self, request):
            captured["request"] = request
            return ChatResponse(
                content=PLAN_PROPOSAL_CONTENT, model="served-model", raw={"id": "plan-1"}
            )

    def build_adapter(spec):
        captured["spec"] = spec
        return Adapter()

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", build_adapter)

    main(
        [
            "run", "plan", "run-1", "draft-1",
            "--provider", "ollama", "--model", "custom-local",
            "--state-db", str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert captured["spec"].kind.value == "ollama"
    assert captured["spec"].model == "custom-local"
    assert captured["request"].messages[1].content == "Ship the feature"
    assert payload == {
        "plan_id": "draft-1",
        "run_id": "run-1",
        "status": "draft",
        "revision": 1,
        "steps": PLAN_PROPOSAL_STEPS_PAYLOAD,
        "evidence": {
            "provider": "ollama",
            "requested_model": "custom-local",
            "response_model": "served-model",
            "content": PLAN_PROPOSAL_CONTENT,
            "raw": {"id": "plan-1"},
        },
    }

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.list_steps("run-1") == ()
    assert reloaded.get("run-1").status is RunStatus.QUEUED


def test_cli_plan_defaults_objective_to_run_objective(tmp_path, monkeypatch, capsys) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Run's own objective")
    captured = {}

    class Adapter:
        def complete(self, request):
            captured["request"] = request
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())

    main(["run", "plan", "run-1", "draft-1", "--provider", "ollama", "--state-db", str(database)])

    assert captured["request"].messages[1].content == "Run's own objective"

    captured.clear()
    main(
        [
            "run", "plan", "run-1", "draft-2",
            "--provider", "ollama", "--objective", "Custom planning objective",
            "--state-db", str(database),
        ]
    )
    assert captured["request"].messages[1].content == "Custom planning objective"


def test_cli_plan_surfaces_malformed_proposal_and_preserves_evidence(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    class Adapter:
        def complete(self, request):
            return ChatResponse(content="not json at all", model="served-model")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "plan", "run-1", "draft-1",
                "--provider", "ollama", "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    error_output = capsys.readouterr().err
    assert "plan proposal is malformed" in error_output
    assert "plan/draft-1" in error_output

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.list_steps("run-1") == ()
    record = StateStore(database).get("plan", "draft-1")
    assert record is not None
    assert record.status == "invalid"
    assert record.payload["evidence"]["content"] == "not json at all"


def test_cli_plan_rejects_missing_run_before_dispatch(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    def build_adapter(spec):
        raise AssertionError("adapter must not be resolved for a missing run")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", build_adapter)

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", "plan", "missing-run", "draft-1",
                "--provider", "ollama", "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "run does not exist: missing-run" in capsys.readouterr().err


def test_cli_inspect_plan_prints_a_reviewable_draft_without_mutation(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    class Adapter:
        def complete(self, request):
            return ChatResponse(
                content=PLAN_PROPOSAL_CONTENT, model="served-model", raw={"id": "plan-1"}
            )

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    main(
        [
            "run", "plan", "run-1", "draft-1",
            "--provider", "ollama", "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    main(["run", "inspect-plan", "draft-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "plan_id": "draft-1",
        "run_id": "run-1",
        "status": "draft",
        "revision": 1,
        "steps": PLAN_PROPOSAL_STEPS_PAYLOAD,
        "evidence": {
            "provider": "ollama",
            "requested_model": None,
            "response_model": "served-model",
            "content": PLAN_PROPOSAL_CONTENT,
            "raw": {"id": "plan-1"},
        },
    }

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.list_steps("run-1") == ()
    assert reloaded.get("run-1").status is RunStatus.QUEUED
    assert reloaded.get_plan("draft-1").revision == 1


def test_cli_inspect_plan_prints_an_invalid_draft_with_recorded_error(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    class Adapter:
        def complete(self, request):
            return ChatResponse(content="not json at all", model="served-model")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    with pytest.raises(SystemExit):
        main(
            [
                "run", "plan", "run-1", "draft-1",
                "--provider", "ollama", "--state-db", str(database),
            ]
        )
    capsys.readouterr()

    main(["run", "inspect-plan", "draft-1", "--state-db", str(database)])

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "invalid"
    assert payload["steps"] == []
    assert "plan proposal is not valid JSON" in payload["error"]
    assert payload["evidence"]["content"] == "not json at all"


def test_cli_inspect_plan_rejects_a_missing_plan(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect-plan", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "plan does not exist: missing" in capsys.readouterr().err


def test_cli_inspect_plan_rejects_missing_database_without_creating_it(
    tmp_path, capsys
) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["run", "inspect-plan", "draft-1", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert "state database does not exist" in capsys.readouterr().err
    assert not database.exists()


def test_cli_accept_plan_materializes_steps_and_prints_decision(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    class Adapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    main(
        [
            "run", "plan", "run-1", "draft-1", "--provider", "ollama",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    main(
        [
            "run", "accept-plan", "draft-1", "--expected-revision", "1",
            "--agent-id", "agent-1", "--state-db", str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "accepted"
    assert payload["revision"] == 2
    assert payload["decision_agent_id"] == "agent-1"
    assert payload["steps"] == PLAN_PROPOSAL_STEPS_PAYLOAD
    reloaded = RunCoordinator(StateStore(database))
    assert [step.step_id for step in reloaded.list_steps("run-1")] == [
        "draft-1-step-1",
        "draft-1-step-2",
    ]
    assert reloaded.list_history("run-1")[-1].plan_id == "draft-1"


def test_cli_reject_plan_records_decision_without_steps(
    tmp_path, monkeypatch, capsys
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    class Adapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    main(
        [
            "run", "plan", "run-1", "draft-1", "--provider", "ollama",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    main(
        [
            "run", "reject-plan", "draft-1", "--expected-revision", "1",
            "--state-db", str(database),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "rejected"
    assert payload["revision"] == 2
    assert "decision_agent_id" not in payload
    assert RunCoordinator(StateStore(database)).list_steps("run-1") == ()


@pytest.mark.parametrize("command", ["accept-plan", "reject-plan"])
def test_cli_plan_decision_rejects_stale_revision_without_mutation(
    tmp_path, monkeypatch, capsys, command
) -> None:
    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / f"{command}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    class Adapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: Adapter())
    main(
        [
            "run", "plan", "run-1", "draft-1", "--provider", "ollama",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "run", command, "draft-1", "--expected-revision", "2",
                "--state-db", str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "plan decision conflict: draft-1" in capsys.readouterr().err
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_plan("draft-1").status == "draft"
    assert reloaded.list_steps("run-1") == ()


def test_cli_end_to_end_operator_review_reconstructs_plan_execution_after_restart(
    tmp_path, monkeypatch, capsys
) -> None:
    """Sprint 13 UAT: objective -> proposed plan -> inspection -> acceptance ->
    execution -> durable reconstruction across a simulated process restart."""

    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"

    main(
        [
            "run", "create", "run-1", "--objective", "Ship the feature",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    class PlanAdapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT, model="planner-model")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: PlanAdapter())
    main(
        [
            "run", "plan", "run-1", "draft-1", "--provider", "ollama",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    main(["run", "inspect-plan", "draft-1", "--state-db", str(database)])
    draft_payload = json.loads(capsys.readouterr().out)
    assert draft_payload["status"] == "draft"
    assert [step["step_id"] for step in draft_payload["steps"]] == [
        "draft-1-step-1",
        "draft-1-step-2",
    ]

    main(["agent", "register", "operator-1", "--state-db", str(database)])
    capsys.readouterr()

    # No draft step can execute before explicit acceptance: the run's queue is
    # still empty, so execution reports no attempt.
    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    pre_acceptance = json.loads(capsys.readouterr().out)
    assert pre_acceptance["execution"] == {"attempted": False}
    assert pre_acceptance["steps"] == []

    main(
        [
            "run", "accept-plan", "draft-1", "--expected-revision", "1",
            "--agent-id", "operator-1", "--state-db", str(database),
        ]
    )
    acceptance = json.loads(capsys.readouterr().out)
    assert acceptance["status"] == "accepted"
    assert acceptance["decision_agent_id"] == "operator-1"

    # Accepted steps execute through the existing worker/coordinator dispatch
    # path (persisted sandbox policy, adapter resolver) rather than a
    # planner-specific shortcut.
    def execute(self, argv, *, timeout=None):
        return SandboxResult(("docker", *argv), 0, "pytest ok", "")

    monkeypatch.setattr("codex_agentic_os.cli.ContainerSandbox.execute", execute)
    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    capsys.readouterr()

    class SummaryAdapter:
        def complete(self, request):
            return ChatResponse("Summary complete", model="served-model")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: SummaryAdapter())
    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    capsys.readouterr()

    # Simulate a process restart: fresh coordinator/state connections and
    # fresh CLI invocations reconstruct the objective, draft, decision,
    # materialized/executed steps, and terminal run outcome from durable
    # state alone.
    restarted = RunCoordinator(StateStore(database))
    reconstructed_run = restarted.get("run-1")
    assert reconstructed_run.objective == "Ship the feature"
    assert reconstructed_run.status is RunStatus.SUCCEEDED

    main(["run", "inspect-plan", "draft-1", "--state-db", str(database)])
    reconstructed_draft = json.loads(capsys.readouterr().out)
    assert reconstructed_draft["status"] == "accepted"
    assert reconstructed_draft["decision_agent_id"] == "operator-1"
    assert reconstructed_draft["steps"] == draft_payload["steps"]

    main(["run", "inspect", "run-1", "--state-db", str(database)])
    reconstructed_inspect = json.loads(capsys.readouterr().out)
    assert reconstructed_inspect["run"]["status"] == "succeeded"
    assert [step["step_id"] for step in reconstructed_inspect["steps"]] == [
        "draft-1-step-1",
        "draft-1-step-2",
    ]
    assert all(
        step["status"] == "succeeded" for step in reconstructed_inspect["steps"]
    )

    main(["run", "history", "run-1", "--state-db", str(database)])
    reconstructed_history = json.loads(capsys.readouterr().out)
    assert [entry["transition"] for entry in reconstructed_history] == [
        "created",
        "plan_accepted",
        "run_started",
        "step_started",
        "step_succeeded",
        "step_started",
        "step_succeeded",
        "run_succeeded",
    ]
    decision_entry = reconstructed_history[1]
    assert decision_entry["plan_id"] == "draft-1"
    assert decision_entry["agent_id"] == "operator-1"


def test_cli_rejected_plan_remains_reconstructable_with_no_executable_steps_after_restart(
    tmp_path, monkeypatch, capsys
) -> None:
    """Regression: a rejected draft stays reconstructable and the run has no
    executable steps, even after a simulated process restart."""

    from codex_agentic_os.chat import ChatResponse

    database = tmp_path / "state.sqlite3"

    main(
        [
            "run", "create", "run-1", "--objective", "Ship the feature",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    class PlanAdapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT, model="planner-model")

    monkeypatch.setattr("codex_agentic_os.cli.adapter_for", lambda spec: PlanAdapter())
    main(
        [
            "run", "plan", "run-1", "draft-1", "--provider", "ollama",
            "--state-db", str(database),
        ]
    )
    capsys.readouterr()

    main(["agent", "register", "operator-2", "--state-db", str(database)])
    capsys.readouterr()

    main(
        [
            "run", "reject-plan", "draft-1", "--expected-revision", "1",
            "--agent-id", "operator-2", "--state-db", str(database),
        ]
    )
    rejection = json.loads(capsys.readouterr().out)
    assert rejection["status"] == "rejected"

    # Simulate a process restart before inspecting the outcome.
    restarted = RunCoordinator(StateStore(database))
    assert restarted.list_steps("run-1") == ()

    main(["run", "inspect-plan", "draft-1", "--state-db", str(database)])
    reconstructed_draft = json.loads(capsys.readouterr().out)
    assert reconstructed_draft["status"] == "rejected"
    assert reconstructed_draft["decision_agent_id"] == "operator-2"

    main(["run", "execute-next", "run-1", "--state-db", str(database)])
    execution = json.loads(capsys.readouterr().out)
    assert execution["execution"] == {"attempted": False}
    assert execution["steps"] == []

    main(["run", "history", "run-1", "--state-db", str(database)])
    reconstructed_history = json.loads(capsys.readouterr().out)
    assert [entry["transition"] for entry in reconstructed_history] == [
        "created",
        "plan_rejected",
    ]
    assert reconstructed_history[-1]["plan_id"] == "draft-1"
