from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import AgentRegistry
from codex_agentic_os.state import StateStore


@pytest.mark.parametrize("label", [None, "Build worker"])
def test_cli_registers_agent_and_matches_listing(tmp_path, capsys, label) -> None:
    database = tmp_path / "nested" / "state.sqlite3"
    arguments = ["agent", "register", "agent-1", "--state-db", str(database)]
    if label is not None:
        arguments.extend(["--label", label])

    main(arguments)

    registered = json.loads(capsys.readouterr().out)
    assert registered == {"agent_id": "agent-1", "label": label, "revision": 1}
    assert database.is_file()

    main(["agent", "list", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == [registered]


def test_cli_lists_agents_in_stable_identifier_order(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    registry.register("agent-b")
    registry.register("agent-a", label="First")

    main(["agent", "list", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == [
        {"agent_id": "agent-a", "label": "First", "revision": 1},
        {"agent_id": "agent-b", "label": None, "revision": 1},
    ]


def test_cli_lists_empty_registry(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    main(["agent", "list", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == []


def test_cli_agent_list_rejects_missing_database(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["agent", "list", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert f"state database does not exist: {database}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["agent-1"], "agent already exists: agent-1"),
        ([" "], "agent id must not be empty"),
        (["agent-2", "--label", " "], "agent label must not be empty"),
    ],
)
def test_cli_register_rejects_duplicate_and_empty_values_without_mutation(
    tmp_path, capsys, arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    original = registry.register("agent-1", label="Original")

    with pytest.raises(SystemExit) as exit_info:
        main(["agent", "register", *arguments, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert AgentRegistry(StateStore(database)).list_agents() == (original,)
