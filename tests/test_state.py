import sqlite3

import pytest

from codex_agentic_os.state import StateRecord, StateStore


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
