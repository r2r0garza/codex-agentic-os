import sqlite3

import pytest

from codex_agentic_os.state import StateConflictError, StateRecord, StateStore


@pytest.mark.parametrize("kind", ["plan", "decision", "run", "step", "agent"])
def test_state_kinds_persist_across_store_instances(tmp_path, kind: str) -> None:
    database = tmp_path / "state.sqlite3"
    stored = StateStore(database).put(
        kind,
        f"{kind}-1",
        status="active",
        payload={"nested": {"enabled": True}, "steps": [1, 2]},
    )

    assert stored == StateRecord(
        kind=kind,
        key=f"{kind}-1",
        status="active",
        payload={"nested": {"enabled": True}, "steps": [1, 2]},
        revision=1,
    )
    assert StateStore(database).get(kind, f"{kind}-1") == stored


def test_put_updates_a_record_and_increments_revision(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.put("run", "run-1", status="running", payload={"attempt": 1})

    updated = store.put("run", "run-1", status="complete", payload={"attempt": 2})

    assert updated.status == "complete"
    assert updated.payload == {"attempt": 2}
    assert updated.revision == 2


def test_insert_rejects_an_existing_record_without_modifying_it(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    original = StateStore(database).insert(
        "run", "run-1", status="queued", payload={"objective": "Original"}
    )

    with pytest.raises(ValueError, match="state record already exists: run/run-1"):
        StateStore(database).insert(
            "run", "run-1", status="queued", payload={"objective": "Replacement"}
        )

    assert original.revision == 1
    assert StateStore(database).get("run", "run-1") == original


def test_list_is_stable_and_delete_reports_presence(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.put("plan", "z-last", status="queued", payload={})
    store.put("plan", "a-first", status="active", payload={})
    store.put("agent", "a-first", status="idle", payload={})

    assert [record.key for record in store.list("plan")] == ["a-first", "z-last"]
    assert store.delete("plan", "a-first") is True
    assert store.delete("plan", "a-first") is False
    assert store.get("plan", "a-first") is None


@pytest.mark.parametrize(
    ("kind", "key", "status", "payload", "message"),
    [
        ("unknown", "key", "active", {}, "unsupported state kind"),
        ("plan", " ", "active", {}, "key must not be empty"),
        ("plan", "key", " ", {}, "status must not be empty"),
        ("plan", "key", "active", {"bad": object()}, "JSON serializable"),
    ],
)
def test_put_rejects_invalid_records(tmp_path, kind, key, status, payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        StateStore(tmp_path / "state.sqlite3").put(
            kind, key, status=status, payload=payload
        )


def test_transition_run_advances_status_and_revision_atomically(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.insert("run", "run-1", status="queued", payload={"objective": "Build"})

    transitioned = store.transition_run(
        "run-1",
        expected_status="queued",
        expected_revision=1,
        status="running",
        payload={"objective": "Build"},
    )

    assert transitioned == StateRecord(
        kind="run",
        key="run-1",
        status="running",
        payload={"objective": "Build"},
        revision=2,
    )
    assert store.get("run", "run-1") == transitioned


def test_transition_run_rejects_missing_run_without_mutation(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")

    with pytest.raises(KeyError, match="state record does not exist: run/missing"):
        store.transition_run(
            "missing",
            expected_status="queued",
            expected_revision=1,
            status="running",
            payload={},
        )


@pytest.mark.parametrize(
    ("expected_status", "expected_revision"),
    [
        ("running", 1),
        ("queued", 2),
    ],
)
def test_transition_run_rejects_stale_expectations_without_mutation(
    tmp_path, expected_status, expected_revision
) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    original = store.insert("run", "run-1", status="queued", payload={"objective": "Build"})

    with pytest.raises(StateConflictError, match="state run transition conflict: run-1"):
        store.transition_run(
            "run-1",
            expected_status=expected_status,
            expected_revision=expected_revision,
            status="running",
            payload={"objective": "Build"},
        )

    assert store.get("run", "run-1") == original


@pytest.mark.parametrize(
    ("expected_status", "expected_revision", "status", "message"),
    [
        (" ", 1, "running", "expected status must not be empty"),
        ("queued", 0, "running", "expected revision must be positive"),
        ("queued", 1, " ", "status must not be empty"),
    ],
)
def test_transition_run_rejects_invalid_arguments(
    tmp_path, expected_status, expected_revision, status, message
) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.insert("run", "run-1", status="queued", payload={"objective": "Build"})

    with pytest.raises(ValueError, match=message):
        store.transition_run(
            "run-1",
            expected_status=expected_status,
            expected_revision=expected_revision,
            status=status,
            payload={"objective": "Build"},
        )


def test_transition_step_advances_status_output_and_revision_atomically(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    original = store.insert(
        "step", "step-1", status="running", payload={"run_id": "run-1", "position": 1}
    )

    transitioned = store.transition_step(
        "step-1",
        expected_status=original.status,
        expected_revision=original.revision,
        status="succeeded",
        payload={"run_id": "run-1", "position": 1, "output": {"ok": True}},
    )

    assert transitioned.revision == original.revision + 1
    assert transitioned.payload["output"] == {"ok": True}
    assert store.get("step", "step-1") == transitioned


def test_transition_step_rejects_missing_and_stale_state_without_mutation(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    original = store.insert("step", "step-1", status="queued", payload={})

    with pytest.raises(KeyError, match="state record does not exist: step/missing"):
        store.transition_step(
            "missing", expected_status="queued", expected_revision=1,
            status="running", payload={},
        )
    with pytest.raises(StateConflictError, match="state step transition conflict: step-1"):
        store.transition_step(
            "step-1", expected_status="queued", expected_revision=2,
            status="running", payload={},
        )

    assert store.get("step", "step-1") == original


@pytest.mark.parametrize(
    ("expected_status", "expected_revision", "status", "message"),
    [
        (" ", 1, "running", "expected status must not be empty"),
        ("queued", 0, "running", "expected revision must be positive"),
        ("queued", 1, " ", "status must not be empty"),
    ],
)
def test_transition_step_rejects_invalid_arguments(
    tmp_path, expected_status, expected_revision, status, message
) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.insert("step", "step-1", status="queued", payload={})

    with pytest.raises(ValueError, match=message):
        store.transition_step(
            "step-1", expected_status=expected_status,
            expected_revision=expected_revision, status=status, payload={},
        )


def test_schema_rejects_unknown_kinds_outside_the_api(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO state_records VALUES (?, ?, ?, ?, ?)",
            ("unknown", "key", "active", "{}", 1),
        )


def test_existing_database_schema_is_upgraded_for_steps(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE state_records (
                kind TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY (kind, key),
                CHECK (kind IN ('plan', 'decision', 'run', 'agent')),
                CHECK (revision > 0))"""
        )
        connection.execute(
            "INSERT INTO state_records VALUES ('run', 'run-1', 'queued', '{}', 1)"
        )

    store = StateStore(database)
    store.put("step", "step-1", status="queued", payload={})

    assert store.get("run", "run-1") is not None
    assert store.get("step", "step-1") is not None
