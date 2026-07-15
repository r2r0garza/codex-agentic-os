from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import MemoryRegistry
from codex_agentic_os.state import StateStore


def test_cli_creates_lists_and_inspects_memory_across_fresh_calls(
    tmp_path, capsys
) -> None:
    database = tmp_path / "nested" / "state.sqlite3"

    main(
        [
            "memory",
            "create",
            "architecture/database",
            "--body",
            "SQLite remains the durable authority.",
            "--kind",
            "decision",
            "--agent-id",
            "agent-1",
            "--run-id",
            "run-7",
            "--step-id",
            "step-3",
            "--state-db",
            str(database),
        ]
    )
    created = json.loads(capsys.readouterr().out)

    assert created["name"] == "architecture/database"
    assert created["body"] == "SQLite remains the durable authority."
    assert created["kind"] == "decision"
    assert created["agent_id"] == "agent-1"
    assert created["run_id"] == "run-7"
    assert created["step_id"] == "step-3"
    assert created["created_at"].endswith("+00:00")

    main(["memory", "list", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == [created]

    main(
        [
            "memory",
            "inspect",
            "architecture/database",
            "--state-db",
            str(database),
        ]
    )
    assert json.loads(capsys.readouterr().out) == created


def test_cli_lists_memory_in_stable_name_order(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    registry = MemoryRegistry(StateStore(database))
    registry.create("z-note", body="Second", kind="note")
    registry.create("a-note", body="First", kind="note")

    main(["memory", "list", "--state-db", str(database)])

    assert [entry["name"] for entry in json.loads(capsys.readouterr().out)] == [
        "a-note",
        "z-note",
    ]


@pytest.mark.parametrize(
    ("create_database", "message"),
    [
        (False, "state database does not exist"),
        (True, "memory entry does not exist: missing"),
    ],
)
def test_cli_inspect_unknown_memory_fails_without_mutation(
    tmp_path, capsys, create_database, message
) -> None:
    database = tmp_path / "state.sqlite3"
    if create_database:
        StateStore(database).initialize()

    with pytest.raises(SystemExit) as exit_info:
        main(["memory", "inspect", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    if create_database:
        assert MemoryRegistry(StateStore(database)).list_entries() == ()
    else:
        assert not database.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ([" ", "--body", "Body", "--kind", "note"], "memory name must not be empty"),
        (["entry", "--body", " ", "--kind", "note"], "memory body must not be empty"),
        (
            ["entry", "--body", "Body", "--kind", "note", "--run-id", " "],
            "memory run id must not be empty",
        ),
    ],
)
def test_cli_create_rejects_invalid_values_without_mutation(
    tmp_path, capsys, arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["memory", "create", *arguments, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert MemoryRegistry(StateStore(database)).list_entries() == ()


def test_cli_duplicate_memory_cannot_replace_original(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    original = MemoryRegistry(StateStore(database)).create(
        "choice", body="Original", kind="decision"
    )

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "memory",
                "create",
                "choice",
                "--body",
                "Replacement",
                "--kind",
                "note",
                "--state-db",
                str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "memory entry already exists: choice" in capsys.readouterr().err
    assert MemoryRegistry(StateStore(database)).list_entries() == (original,)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["memory", "create"], "the following arguments are required: name, --body, --kind"),
        (
            ["memory", "create", "entry", "--kind", "note"],
            "the following arguments are required: --body",
        ),
        (
            ["memory", "create", "entry", "--body", "Body"],
            "the following arguments are required: --kind",
        ),
        (
            [
                "memory",
                "create",
                "entry",
                "--body",
                "Body",
                "--kind",
                "observation",
            ],
            "invalid choice: 'observation'",
        ),
    ],
)
def test_cli_create_rejects_missing_or_unknown_required_fields_before_mutation(
    tmp_path, capsys, arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main([*arguments, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert not database.exists()
