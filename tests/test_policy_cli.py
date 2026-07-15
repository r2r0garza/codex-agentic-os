from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main
from codex_agentic_os.runtime import ExecutionPolicyRegistry
from codex_agentic_os.state import StateStore


def test_cli_creates_policy_rule_and_matches_listing(tmp_path, capsys) -> None:
    database = tmp_path / "nested" / "state.sqlite3"

    main(
        [
            "policy",
            "create",
            "rule-1",
            "--criterion-kind",
            "sandbox_network_access",
            "--criterion-value",
            "disabled",
            "--reason",
            "Deny network access by default",
            "--precedence",
            "10",
            "--state-db",
            str(database),
        ]
    )

    created = json.loads(capsys.readouterr().out)
    assert created["rule_id"] == "rule-1"
    assert created["enabled"] is True
    assert created["precedence"] == 10
    assert created["criterion_kind"] == "sandbox_network_access"
    assert created["criterion_value"] == "disabled"
    assert created["reason"] == "Deny network access by default"
    assert created["created_at"].endswith("+00:00")
    assert database.is_file()

    main(["policy", "list", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == [created]

    main(["policy", "inspect", "rule-1", "--state-db", str(database)])
    assert json.loads(capsys.readouterr().out) == created


def test_cli_creates_disabled_policy_rule(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"

    main(
        [
            "policy",
            "create",
            "rule-1",
            "--criterion-kind",
            "declared_tool_name",
            "--criterion-value",
            "search_files",
            "--reason",
            "Only search_files may run without approval",
            "--precedence",
            "0",
            "--disabled",
            "--state-db",
            str(database),
        ]
    )

    created = json.loads(capsys.readouterr().out)
    assert created["enabled"] is False


def test_cli_lists_policy_rules_in_stable_identifier_order(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    registry = ExecutionPolicyRegistry(StateStore(database))
    registry.create_rule(
        "rule-b",
        criterion_kind="execution_kind",
        criterion_value="command",
        reason="Second",
        precedence=1,
    )
    registry.create_rule(
        "rule-a",
        criterion_kind="execution_kind",
        criterion_value="provider",
        reason="First",
        precedence=0,
    )

    main(["policy", "list", "--state-db", str(database)])

    listed = json.loads(capsys.readouterr().out)
    assert [rule["rule_id"] for rule in listed] == ["rule-a", "rule-b"]


def test_cli_lists_empty_policy_registry(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    main(["policy", "list", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == []


def test_cli_policy_list_rejects_missing_database(tmp_path, capsys) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(["policy", "list", "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert f"state database does not exist: {database}" in capsys.readouterr().err


def test_cli_inspects_policy_rule_without_changing_revision(tmp_path, capsys) -> None:
    database = tmp_path / "state.sqlite3"
    original = ExecutionPolicyRegistry(StateStore(database)).create_rule(
        "rule-1",
        criterion_kind="execution_kind",
        criterion_value="delegation",
        reason="Delegated steps require review",
        precedence=0,
    )

    main(["policy", "inspect", "rule-1", "--state-db", str(database)])

    assert json.loads(capsys.readouterr().out) == {
        "rule_id": original.rule_id,
        "enabled": original.enabled,
        "precedence": original.precedence,
        "criterion_kind": original.criterion_kind,
        "criterion_value": original.criterion_value,
        "reason": original.reason,
        "created_at": original.created_at,
    }
    assert ExecutionPolicyRegistry(StateStore(database)).get("rule-1") == original


@pytest.mark.parametrize("create_database", [False, True])
def test_cli_inspect_rejects_missing_database_or_rule_without_mutation(
    tmp_path, capsys, create_database
) -> None:
    database = tmp_path / "state.sqlite3"
    if create_database:
        StateStore(database).initialize()

    with pytest.raises(SystemExit) as exit_info:
        main(["policy", "inspect", "missing", "--state-db", str(database)])

    assert exit_info.value.code == 2
    message = (
        "policy rule does not exist: missing"
        if create_database
        else f"state database does not exist: {database}"
    )
    assert message in capsys.readouterr().err
    if create_database:
        assert ExecutionPolicyRegistry(StateStore(database)).list_rules() == ()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            [
                "rule-1",
                "--criterion-kind",
                "execution_kind",
                "--criterion-value",
                "command",
                "--reason",
                "Replacement",
                "--precedence",
                "1",
            ],
            "policy rule already exists: rule-1",
        ),
        (
            [
                " ",
                "--criterion-kind",
                "execution_kind",
                "--criterion-value",
                "command",
                "--reason",
                "Replacement",
                "--precedence",
                "1",
            ],
            "policy rule id must not be empty",
        ),
        (
            [
                "rule-2",
                "--criterion-kind",
                "execution_kind",
                "--criterion-value",
                "command == provider",
                "--reason",
                "Replacement",
                "--precedence",
                "1",
            ],
            "policy rule execution_kind value must be one of: command, delegation, "
            "provider",
        ),
        (
            [
                "rule-2",
                "--criterion-kind",
                "declared_tool_name",
                "--criterion-value",
                "not a valid name",
                "--reason",
                "Replacement",
                "--precedence",
                "1",
            ],
            "policy rule declared_tool_name value must be a valid identifier",
        ),
        (
            [
                "rule-2",
                "--criterion-kind",
                "execution_kind",
                "--criterion-value",
                "command",
                "--reason",
                " ",
                "--precedence",
                "1",
            ],
            "policy rule reason must not be empty",
        ),
        (
            [
                "rule-2",
                "--criterion-kind",
                "execution_kind",
                "--criterion-value",
                "command",
                "--reason",
                "Replacement",
                "--precedence",
                "-1",
            ],
            "policy rule precedence must be a non-negative integer",
        ),
    ],
)
def test_cli_create_rejects_invalid_input_without_mutation(
    tmp_path, capsys, arguments, message
) -> None:
    database = tmp_path / "state.sqlite3"
    registry = ExecutionPolicyRegistry(StateStore(database))
    original = registry.create_rule(
        "rule-1",
        criterion_kind="execution_kind",
        criterion_value="command",
        reason="Original",
        precedence=0,
    )

    with pytest.raises(SystemExit) as exit_info:
        main(["policy", "create", *arguments, "--state-db", str(database)])

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert ExecutionPolicyRegistry(StateStore(database)).list_rules() == (original,)


def test_cli_create_rejects_unknown_criterion_kind_without_mutation(
    tmp_path, capsys
) -> None:
    database = tmp_path / "state.sqlite3"

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "policy",
                "create",
                "rule-1",
                "--criterion-kind",
                "free_form_expression",
                "--criterion-value",
                "x",
                "--reason",
                "r",
                "--precedence",
                "0",
                "--state-db",
                str(database),
            ]
        )

    assert exit_info.value.code == 2
    assert "invalid choice: 'free_form_expression'" in capsys.readouterr().err
    assert not database.is_file()
