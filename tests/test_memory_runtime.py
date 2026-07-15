from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from codex_agentic_os.runtime import MemoryEntry, MemoryRegistry
from codex_agentic_os.state import StateStore


def test_memory_creation_persists_body_kind_and_provenance_across_restart(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    moment = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    created = MemoryRegistry(
        StateStore(database), clock=lambda: moment
    ).create(
        "architecture/database",
        body="SQLite remains the durable authority.",
        kind="decision",
        agent_id="agent-1",
        run_id="run-7",
        step_id="step-3",
    )

    assert created == MemoryEntry(
        name="architecture/database",
        body="SQLite remains the durable authority.",
        kind="decision",
        created_at="2026-07-14T12:00:00+00:00",
        agent_id="agent-1",
        run_id="run-7",
        step_id="step-3",
    )
    assert MemoryRegistry(StateStore(database, read_only=True)).get(created.name) == created


def test_memory_note_allows_omitted_provenance(tmp_path) -> None:
    registry = MemoryRegistry(StateStore(tmp_path / "state.sqlite3"))

    created = registry.create("handoff", body="Resume issue 140.", kind="note")

    assert created.kind == "note"
    assert created.agent_id is None
    assert created.run_id is None
    assert created.step_id is None


def test_memory_entries_are_listed_in_stable_name_order(tmp_path) -> None:
    registry = MemoryRegistry(StateStore(tmp_path / "state.sqlite3"))
    second = registry.create("z-note", body="Second", kind="note")
    first = registry.create("a-decision", body="First", kind="decision")

    assert registry.list_entries() == (first, second)


def test_memory_duplicate_does_not_replace_existing_entry(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    registry = MemoryRegistry(StateStore(database))
    original = registry.create("choice", body="Original", kind="decision")

    with pytest.raises(ValueError, match="memory entry already exists: choice"):
        registry.create("choice", body="Replacement", kind="note")

    assert MemoryRegistry(StateStore(database)).list_entries() == (original,)


def test_memory_duplicate_name_is_atomic_across_competing_stores(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).initialize()

    def create(body: str):
        return MemoryRegistry(StateStore(database)).create(
            "shared-name", body=body, kind="note"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create_safely, ((create, "first"), (create, "second"))))

    successes = [outcome for outcome in outcomes if isinstance(outcome, MemoryEntry)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, ValueError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert str(failures[0]) == "memory entry already exists: shared-name"
    assert MemoryRegistry(StateStore(database)).list_entries() == tuple(successes)


def create_safely(arguments):
    create, body = arguments
    try:
        return create(body)
    except ValueError as error:
        return error


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": " "}, "memory name must not be empty"),
        ({"body": " "}, "memory body must not be empty"),
        ({"kind": "observation"}, "memory kind must be one of: decision, note"),
        ({"agent_id": " "}, "memory agent id must not be empty"),
        ({"run_id": " "}, "memory run id must not be empty"),
        ({"step_id": " "}, "memory step id must not be empty"),
    ],
)
def test_memory_creation_rejects_invalid_input_without_mutation(
    tmp_path, kwargs, message
) -> None:
    registry = MemoryRegistry(StateStore(tmp_path / "state.sqlite3"))
    arguments = {"name": "entry", "body": "Body", "kind": "note", **kwargs}

    with pytest.raises(ValueError, match=message):
        registry.create(**arguments)

    assert registry.list_entries() == ()


def test_memory_read_rejects_corrupted_provenance(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    StateStore(database).insert(
        "memory_entry",
        "broken",
        status="active",
        payload={
            "body": "Body",
            "kind": "note",
            "created_at": "2026-07-14T12:00:00+00:00",
            "run_id": 7,
        },
    )

    with pytest.raises(ValueError, match="memory run id must not be empty"):
        MemoryRegistry(StateStore(database)).get("broken")
