import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from codex_agentic_os.state import (
    RunHistoryEntry,
    StateConflictError,
    StateRecord,
    StateStore,
)


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


def test_transition_step_records_context_step_ids_on_history(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.insert("run", "run-1", status="running", payload={"objective": "Compose"})
    original = store.insert(
        "step", "model", status="queued",
        payload={
            "run_id": "run-1", "position": 2, "objective": "Synthesize",
            "context_step_ids": ["second", "first"],
        },
    )

    store.transition_step(
        "model",
        expected_status=original.status,
        expected_revision=original.revision,
        status="running",
        payload=original.payload,
        run_id="run-1",
        execution_kind="provider",
        context_step_ids=("second", "first"),
    )

    entry = store.list_run_history("run-1")[-1]
    assert entry.transition == "step_started"
    assert entry.step_id == "model"
    assert entry.context_step_ids == ("second", "first")
    assert StateStore(store.path).list_run_history("run-1")[-1].context_step_ids == (
        "second",
        "first",
    )


def test_transition_step_omits_context_step_ids_from_history_when_not_provided(
    tmp_path,
) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.insert("run", "run-1", status="running", payload={"objective": "Build"})
    original = store.insert(
        "step", "step-1", status="running",
        payload={"run_id": "run-1", "position": 1},
    )

    store.transition_step(
        "step-1",
        expected_status=original.status,
        expected_revision=original.revision,
        status="succeeded",
        payload={"run_id": "run-1", "position": 1, "output": {"ok": True}},
        run_id="run-1",
        execution_kind="command",
    )

    entry = store.list_run_history("run-1")[-1]
    assert entry.context_step_ids is None


def test_batch_transition_conflict_appends_no_step_history(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    run = store.insert("run", "run-1", status="running", payload={"agent_id": "a"})
    step = store.insert(
        "step", "step-1", status="running", payload={"run_id": "run-1", "position": 1}
    )
    before = store.list_run_history("run-1")

    with pytest.raises(StateConflictError, match="step transition conflict"):
        store.put_many(
            (
                ("step", "step-1", "succeeded", step.payload),
                ("run", "run-1", "succeeded", run.payload),
            ),
            expected=(("step", "step-1", "queued", step.revision),),
            history=(
                RunHistoryEntry(
                    "run-1", 0, "step_succeeded", "succeeded",
                    agent_id="a", execution_kind="command", step_id="step-1",
                ),
            ),
        )

    assert store.get("step", "step-1") == step
    assert store.get("run", "run-1") == run
    assert store.list_run_history("run-1") == before


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


def test_run_history_records_creation_claim_release_and_transition_in_order(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("run", "run-1", status="queued", payload={"objective": "Build"})
    store.claim_run("run-1", "agent-1")
    store.release_run_claim("run-1", "agent-1")
    store.claim_run("run-1", "agent-2")
    store.transition_run(
        "run-1",
        expected_status="queued",
        expected_revision=4,
        status="running",
        payload={"objective": "Build", "agent_id": "agent-2"},
        execution_kind="provider_message",
    )

    history = StateStore(database).list_run_history("run-1")

    assert history == (
        RunHistoryEntry("run-1", 1, "created", "queued", None, None),
        RunHistoryEntry("run-1", 2, "claimed", "queued", "agent-1", None),
        RunHistoryEntry("run-1", 3, "claim_released", "queued", "agent-1", None),
        RunHistoryEntry("run-1", 4, "claimed", "queued", "agent-2", None),
        RunHistoryEntry(
            "run-1", 5, "transitioned", "running", "agent-2", "provider_message"
        ),
    )


def test_run_history_is_isolated_per_run_and_reconstructs_after_restart(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("run", "run-1", status="queued", payload={"objective": "First"})
    store.insert("run", "run-2", status="queued", payload={"objective": "Second"})
    store.claim_run("run-2", "agent-1")

    reloaded = StateStore(database)
    assert reloaded.list_run_history("run-1") == (
        RunHistoryEntry("run-1", 1, "created", "queued", None, None),
    )
    assert reloaded.list_run_history("run-2") == (
        RunHistoryEntry("run-2", 1, "created", "queued", None, None),
        RunHistoryEntry("run-2", 2, "claimed", "queued", "agent-1", None),
    )
    assert reloaded.list_run_history("missing-run") == ()


def test_losing_claim_and_stale_transition_append_no_history_entry(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("run", "run-1", status="queued", payload={"objective": "Build"})
    store.claim_run("run-1", "agent-1")

    with pytest.raises(StateConflictError):
        store.claim_run("run-1", "agent-2")
    with pytest.raises(StateConflictError, match="state run transition conflict"):
        store.transition_run(
            "run-1",
            expected_status="queued",
            expected_revision=99,
            status="running",
            payload={"objective": "Build", "agent_id": "agent-1"},
        )

    assert store.list_run_history("run-1") == (
        RunHistoryEntry("run-1", 1, "created", "queued", None, None),
        RunHistoryEntry("run-1", 2, "claimed", "queued", "agent-1", None),
    )


def test_competing_claims_append_exactly_one_history_entry(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("run", "run-1", status="queued", payload={"objective": "Build"})
    stores = (StateStore(database), StateStore(database))

    def attempt(instance: StateStore, agent_id: str) -> bool:
        try:
            instance.claim_run("run-1", agent_id)
            return True
        except StateConflictError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt, instance, agent_id)
            for instance, agent_id in zip(stores, ("agent-1", "agent-2"))
        ]
        results = [future.result() for future in futures]

    assert sorted(results) == [False, True]
    claim_entries = [
        entry
        for entry in StateStore(database).list_run_history("run-1")
        if entry.transition == "claimed"
    ]
    assert len(claim_entries) == 1


def test_claim_next_run_appends_a_claimed_history_entry(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("run", "run-1", status="queued", payload={"objective": "Build"})

    store.claim_next_run("agent-1")

    assert store.list_run_history("run-1") == (
        RunHistoryEntry("run-1", 1, "created", "queued", None, None),
        RunHistoryEntry("run-1", 2, "claimed", "queued", "agent-1", None),
    )


def test_reassign_stale_run_claim_preserves_running_step_and_history(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert(
        "agent", "old", status="registered",
        payload={"last_seen": "2026-07-12T12:00:00+00:00"},
    )
    store.insert(
        "agent", "new", status="registered",
        payload={"last_seen": "2026-07-12T13:00:00+00:00"},
    )
    run = store.insert(
        "run", "run-1", status="running",
        payload={"objective": "Build", "agent_id": "old"},
    )
    step = store.insert(
        "step", "step-1", status="running",
        payload={"run_id": "run-1", "position": 1, "objective": "Execute", "output": None},
    )

    reassigned = store.reassign_stale_run_claim(
        "run-1", expected_agent_id="old", expected_revision=run.revision,
        replacement_agent_id="new", threshold_seconds=300,
        evaluated_at=datetime(2026, 7, 12, 12, 5, 1, tzinfo=timezone.utc),
    )

    assert reassigned.payload["agent_id"] == "new"
    assert reassigned.revision == run.revision + 1
    assert StateStore(database).get("step", "step-1") == step
    assert StateStore(database).list_run_history("run-1")[-1] == RunHistoryEntry(
        "run-1", 2, "claim_reassigned", "running", "new", None
    )


def test_reassign_stale_run_claim_rejects_fresh_heartbeat_without_mutation(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.insert(
        "agent", "old", status="registered",
        payload={"last_seen": "2026-07-12T12:05:00+00:00"},
    )
    store.insert("agent", "new", status="registered", payload={"last_seen": "2026-07-12T12:05:00+00:00"})
    run = store.insert("run", "run-1", status="queued", payload={"objective": "Build", "agent_id": "old"})
    before = store.list_run_history("run-1")

    with pytest.raises(StateConflictError, match="owner is not stale"):
        store.reassign_stale_run_claim(
            "run-1", expected_agent_id="old", expected_revision=run.revision,
            replacement_agent_id="new", threshold_seconds=300,
            evaluated_at=datetime(2026, 7, 12, 12, 10, tzinfo=timezone.utc),
        )

    assert store.get("run", "run-1") == run
    assert store.list_run_history("run-1") == before


def test_retry_failed_step_creates_queued_attempt_and_reopens_run(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert(
        "run", "run-1", status="failed",
        payload={
            "objective": "Build",
            "agent_id": "agent-1",
            "output": {"failed_step_id": "step-1", "exit_code": 17},
        },
    )
    failed_step = store.insert(
        "step", "step-1", status="failed",
        payload={
            "run_id": "run-1", "position": 1, "objective": "Run command",
            "command": ["true"], "timeout": 30,
            "output": {"command": ["true"], "exit_code": 17, "stdout": "", "stderr": "boom"},
        },
    )
    run = store.get("run", "run-1")

    original, new_step, reopened_run = store.retry_failed_step(
        "step-1", "step-1-retry",
        expected_step_revision=failed_step.revision,
        expected_run_revision=run.revision,
    )

    assert original == store.get("step", "step-1")
    assert new_step.status == "queued"
    assert new_step.payload == {
        "run_id": "run-1", "position": 2, "objective": "Run command",
        "retried_step_id": "step-1", "command": ["true"], "timeout": 30,
        "approval_required": False,
    }
    assert reopened_run.status == "queued"
    assert reopened_run.revision == run.revision + 1
    assert reopened_run.payload == {"objective": "Build", "agent_id": "agent-1"}
    assert store.list_run_history("run-1")[-1] == RunHistoryEntry(
        run_id="run-1", sequence=2, transition="step_retried", status="queued",
        agent_id="agent-1", execution_kind=None, step_id="step-1-retry",
        retried_step_id="step-1",
    )


def test_retry_failed_step_preserves_provider_context_step_ids(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    run = store.insert(
        "run", "run-1", status="failed",
        payload={"objective": "Compose", "output": {"failed_step_id": "model"}},
    )
    store.insert(
        "step", "source", status="succeeded",
        payload={
            "run_id": "run-1", "position": 1, "objective": "Source",
            "command": ["true"], "output": {"exit_code": 0},
        },
    )
    failed = store.insert(
        "step", "model", status="failed",
        payload={
            "run_id": "run-1", "position": 2, "objective": "Synthesize",
            "message": {"provider": "local", "content": "Use source"},
            "context_step_ids": ["source"],
            "output": {"error": "offline", "error_type": "RuntimeError"},
        },
    )

    _, retried, _ = store.retry_failed_step(
        "model", "model-retry",
        expected_step_revision=failed.revision,
        expected_run_revision=run.revision,
    )

    assert retried.payload["context_step_ids"] == ["source"]


def test_retry_failed_step_rejects_stale_revisions_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert(
        "run", "run-1", status="failed",
        payload={"objective": "Build", "output": {"failed_step_id": "step-1"}},
    )
    failed_step = store.insert(
        "step", "step-1", status="failed",
        payload={"run_id": "run-1", "position": 1, "objective": "Run", "command": ["true"]},
    )
    run = store.get("run", "run-1")
    before_history = store.list_run_history("run-1")

    with pytest.raises(StateConflictError, match="state step retry conflict"):
        store.retry_failed_step(
            "step-1", "step-1-retry",
            expected_step_revision=failed_step.revision + 1,
            expected_run_revision=run.revision,
        )
    with pytest.raises(StateConflictError, match="state run retry conflict"):
        store.retry_failed_step(
            "step-1", "step-1-retry",
            expected_step_revision=failed_step.revision,
            expected_run_revision=run.revision + 1,
        )

    assert store.get("step", "step-1") == failed_step
    assert store.get("run", "run-1") == run
    assert store.list_run_history("run-1") == before_history
    assert store.get("step", "step-1-retry") is None


def test_retry_failed_step_rejects_non_failed_step_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("run", "run-1", status="running", payload={"objective": "Build"})
    step = store.insert(
        "step", "step-1", status="running",
        payload={"run_id": "run-1", "position": 1, "objective": "Run", "command": ["true"]},
    )
    run = store.get("run", "run-1")

    with pytest.raises(StateConflictError, match="state step retry conflict"):
        store.retry_failed_step(
            "step-1", "step-1-retry",
            expected_step_revision=step.revision,
            expected_run_revision=run.revision,
        )

    assert store.get("step", "step-1") == step
    assert store.get("run", "run-1") == run


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
