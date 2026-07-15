"""Durable SQLite state for runtime records, policy, and named memory."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class StateRecord:
    """One versioned state document stored by the agent runtime."""

    kind: str
    key: str
    status: str
    payload: Mapping[str, object]
    revision: int


@dataclass(frozen=True, slots=True)
class RunHistoryEntry:
    """One durable, ordered provenance entry for a run's lifecycle."""

    run_id: str
    sequence: int
    transition: str
    status: str
    agent_id: str | None = None
    execution_kind: str | None = None
    step_id: str | None = None
    retried_step_id: str | None = None
    context_step_ids: tuple[str, ...] | None = None
    memory_names: tuple[str, ...] | None = None
    plan_id: str | None = None
    required_capability: str | None = None
    resolved_provider: str | None = None
    resolved_model: str | None = None
    routing_reason: str | None = None
    artifact_name: str | None = None
    parent_run_id: str | None = None
    parent_step_id: str | None = None
    delegated_run_id: str | None = None
    tool_name: str | None = None
    tool_outcome: str | None = None
    tool_iteration: int | None = None
    tool_phase: str | None = None
    policy_rule_id: str | None = None
    policy_reason: str | None = None


class StateConflictError(ValueError):
    """Raised when insert-only persistence finds an existing identity."""


class StateStore:
    """Persist runtime state in a repository-local SQLite database."""

    KINDS = frozenset(
        {
            "plan",
            "decision",
            "run",
            "step",
            "agent",
            "artifact",
            "policy_rule",
            "memory_entry",
        }
    )
    _CREATE_TABLE = """
        CREATE TABLE {clause} state_records (
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            revision INTEGER NOT NULL,
            PRIMARY KEY (kind, key),
            CHECK (kind IN (
                'plan', 'decision', 'run', 'step', 'agent', 'artifact', 'policy_rule',
                'memory_entry'
            )),
            CHECK (revision > 0)
        )
    """
    _CREATE_HISTORY_TABLE = """
        CREATE TABLE IF NOT EXISTS run_history (
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            transition TEXT NOT NULL,
            status TEXT NOT NULL,
            step_id TEXT,
            agent_id TEXT,
            execution_kind TEXT,
            retried_step_id TEXT,
            plan_id TEXT,
            required_capability TEXT,
            resolved_provider TEXT,
            resolved_model TEXT,
            routing_reason TEXT,
            PRIMARY KEY (run_id, sequence)
        )
    """

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only

    def initialize(self) -> None:
        """Create the database and schema if they do not exist."""

        if self.read_only:
            if not self.path.is_file():
                raise ValueError(f"state database does not exist: {self.path}")
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'state_records'"
                ).fetchone()
            if (
                table is None
                or "'step'" not in str(table[0])
                or "'artifact'" not in str(table[0])
            ):
                raise ValueError(f"state database has an incompatible schema: {self.path}")
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(self._CREATE_TABLE.format(clause="IF NOT EXISTS"))
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'state_records'"
            ).fetchone()[0]
            if (
                "'step'" not in schema
                or "'artifact'" not in schema
                or "'policy_rule'" not in schema
                or "'memory_entry'" not in schema
            ):
                connection.execute("ALTER TABLE state_records RENAME TO state_records_old")
                connection.execute(self._CREATE_TABLE.format(clause=""))
                connection.execute(
                    "INSERT INTO state_records SELECT * FROM state_records_old"
                )
                connection.execute("DROP TABLE state_records_old")
            connection.execute(self._CREATE_HISTORY_TABLE)
            history_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(run_history)").fetchall()
            }
            if "step_id" not in history_columns:
                connection.execute("ALTER TABLE run_history ADD COLUMN step_id TEXT")
            if "retried_step_id" not in history_columns:
                connection.execute("ALTER TABLE run_history ADD COLUMN retried_step_id TEXT")
            if "context_step_ids" not in history_columns:
                connection.execute(
                    "ALTER TABLE run_history ADD COLUMN context_step_ids TEXT"
                )
            if "memory_names" not in history_columns:
                connection.execute(
                    "ALTER TABLE run_history ADD COLUMN memory_names TEXT"
                )
            if "plan_id" not in history_columns:
                connection.execute("ALTER TABLE run_history ADD COLUMN plan_id TEXT")
            for column in (
                "required_capability",
                "resolved_provider",
                "resolved_model",
                "routing_reason",
                "artifact_name",
                "parent_run_id",
                "parent_step_id",
                "delegated_run_id",
                "tool_name",
                "tool_outcome",
                "tool_phase",
                "policy_rule_id",
                "policy_reason",
            ):
                if column not in history_columns:
                    connection.execute(
                        f"ALTER TABLE run_history ADD COLUMN {column} TEXT"
                    )
            if "tool_iteration" not in history_columns:
                connection.execute(
                    "ALTER TABLE run_history ADD COLUMN tool_iteration INTEGER"
                )
            connection.commit()

    def put(
        self,
        kind: str,
        key: str,
        *,
        status: str,
        payload: Mapping[str, object],
    ) -> StateRecord:
        """Insert or replace a document and increment its revision."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity(kind, key, status)
        encoded = self._encode_payload(payload)
        self.initialize()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision FROM state_records WHERE kind = ? AND key = ?",
                (kind, key),
            ).fetchone()
            revision = 1 if current is None else int(current[0]) + 1
            connection.execute(
                """
                INSERT INTO state_records (kind, key, status, payload, revision)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, key) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    revision = excluded.revision
                """,
                (kind, key, status, encoded, revision),
            )
            connection.commit()
        return StateRecord(kind, key, status, json.loads(encoded), revision)

    def insert(
        self,
        kind: str,
        key: str,
        *,
        status: str,
        payload: Mapping[str, object],
        history: Sequence[RunHistoryEntry] = (),
    ) -> StateRecord:
        """Insert a new document at revision one, rejecting an existing identity."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity(kind, key, status)
        encoded = self._encode_payload(payload)
        self.initialize()
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO state_records (kind, key, status, payload, revision)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (kind, key, status, encoded),
                )
                if kind == "run":
                    self._append_run_history(
                        connection,
                        key,
                        transition="created",
                        status=status,
                        agent_id=payload.get("agent_id"),
                        execution_kind=None,
                    )
                for entry in history:
                    self._append_run_history(
                        connection,
                        entry.run_id,
                        transition=entry.transition,
                        status=entry.status,
                        step_id=entry.step_id,
                        agent_id=entry.agent_id,
                        execution_kind=entry.execution_kind,
                        retried_step_id=entry.retried_step_id,
                        context_step_ids=entry.context_step_ids,
                        memory_names=entry.memory_names,
                        plan_id=entry.plan_id,
                        required_capability=entry.required_capability,
                        resolved_provider=entry.resolved_provider,
                        resolved_model=entry.resolved_model,
                        routing_reason=entry.routing_reason,
                        artifact_name=entry.artifact_name,
                        parent_run_id=entry.parent_run_id,
                        parent_step_id=entry.parent_step_id,
                        delegated_run_id=entry.delegated_run_id,
                        tool_name=entry.tool_name,
                        tool_outcome=entry.tool_outcome,
                        tool_iteration=entry.tool_iteration,
                        tool_phase=entry.tool_phase,
                        policy_rule_id=entry.policy_rule_id,
                        policy_reason=entry.policy_reason,
                    )
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise StateConflictError(
                f"state record already exists: {kind}/{key}"
            ) from error
        return StateRecord(kind, key, status, json.loads(encoded), 1)

    def put_many(
        self,
        records: Sequence[tuple[str, str, str, Mapping[str, object]]],
        *,
        expected: Sequence[tuple[str, str, str, int]] = (),
        history: Sequence[RunHistoryEntry] = (),
        expected_policy_rule_ids: Sequence[str] | None = None,
    ) -> tuple[StateRecord, ...]:
        """Insert or replace several documents in one transaction."""

        if self.read_only:
            raise ValueError("state store is read-only")
        prepared: list[tuple[str, str, str, str]] = []
        for kind, key, status, payload in records:
            self._validate_identity(kind, key, status)
            prepared.append((kind, key, status, self._encode_payload(payload)))

        self.initialize()
        stored: list[StateRecord] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_policy_rule_snapshot(connection, expected_policy_rule_ids)
            for kind, key, status, revision in expected:
                row = connection.execute(
                    "SELECT status, revision FROM state_records WHERE kind = ? AND key = ?",
                    (kind, key),
                ).fetchone()
                if row is None or str(row[0]) != status or int(row[1]) != revision:
                    raise StateConflictError(f"state {kind} transition conflict: {key}")
            for kind, key, status, encoded in prepared:
                stored.append(
                    self._put_on_connection(connection, kind, key, status, encoded)
                )
            for entry in history:
                self._append_run_history(
                    connection,
                    entry.run_id,
                    transition=entry.transition,
                    status=entry.status,
                    step_id=entry.step_id,
                    agent_id=entry.agent_id,
                    execution_kind=entry.execution_kind,
                    retried_step_id=entry.retried_step_id,
                    context_step_ids=entry.context_step_ids,
                    memory_names=entry.memory_names,
                    plan_id=entry.plan_id,
                    required_capability=entry.required_capability,
                    resolved_provider=entry.resolved_provider,
                    resolved_model=entry.resolved_model,
                    routing_reason=entry.routing_reason,
                    artifact_name=entry.artifact_name,
                    parent_run_id=entry.parent_run_id,
                    parent_step_id=entry.parent_step_id,
                    delegated_run_id=entry.delegated_run_id,
                    tool_name=entry.tool_name,
                    tool_outcome=entry.tool_outcome,
                    tool_iteration=entry.tool_iteration,
                    tool_phase=entry.tool_phase,
                    policy_rule_id=entry.policy_rule_id,
                    policy_reason=entry.policy_reason,
                )
            connection.commit()
        return tuple(stored)

    def claim_run(self, run_id: str, agent_id: str) -> StateRecord:
        """Assign one queued, unassigned run in a write transaction."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("run", run_id)
        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, payload, revision
                FROM state_records
                WHERE kind = 'run' AND key = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"state record does not exist: run/{run_id}")

            status, encoded, revision = str(row[0]), str(row[1]), int(row[2])
            payload = json.loads(encoded)
            if status != "queued" or payload.get("agent_id") is not None:
                raise StateConflictError(f"state run cannot be claimed: {run_id}")

            claimed_payload = {**payload, "agent_id": agent_id}
            claimed_encoded = self._encode_payload(claimed_payload)
            claimed_revision = revision + 1
            connection.execute(
                """
                UPDATE state_records
                SET payload = ?, revision = ?
                WHERE kind = 'run' AND key = ?
                """,
                (claimed_encoded, claimed_revision, run_id),
            )
            self._append_run_history(
                connection,
                run_id,
                transition="claimed",
                status=status,
                agent_id=agent_id,
                execution_kind=None,
            )
            connection.commit()
        return StateRecord("run", run_id, status, claimed_payload, claimed_revision)

    def release_run_claim(self, run_id: str, agent_id: str) -> StateRecord:
        """Clear an exact queued run assignment in a write transaction."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("run", run_id)
        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, payload, revision
                FROM state_records
                WHERE kind = 'run' AND key = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"state record does not exist: run/{run_id}")

            status, encoded, revision = str(row[0]), str(row[1]), int(row[2])
            payload = json.loads(encoded)
            if status != "queued" or payload.get("agent_id") != agent_id:
                raise StateConflictError(f"state run claim cannot be released: {run_id}")

            released_payload = {**payload, "agent_id": None}
            released_encoded = self._encode_payload(released_payload)
            released_revision = revision + 1
            connection.execute(
                """
                UPDATE state_records
                SET payload = ?, revision = ?
                WHERE kind = 'run' AND key = ?
                """,
                (released_encoded, released_revision, run_id),
            )
            self._append_run_history(
                connection,
                run_id,
                transition="claim_released",
                status=status,
                agent_id=agent_id,
                execution_kind=None,
            )
            connection.commit()
        return StateRecord("run", run_id, status, released_payload, released_revision)

    def reassign_stale_run_claim(
        self,
        run_id: str,
        *,
        expected_agent_id: str,
        expected_revision: int,
        replacement_agent_id: str,
        threshold_seconds: float,
        evaluated_at: datetime,
    ) -> StateRecord:
        """Transfer a stale run claim through one heartbeat-aware transaction."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("run", run_id)
        for label, agent_id in (
            ("expected agent id", expected_agent_id),
            ("replacement agent id", replacement_agent_id),
        ):
            if not agent_id.strip():
                raise ValueError(f"{label} must not be empty")
        if expected_agent_id == replacement_agent_id:
            raise ValueError("replacement agent must differ from the current owner")
        if expected_revision < 1:
            raise ValueError("expected revision must be positive")
        if threshold_seconds <= 0:
            raise ValueError("staleness threshold must be a positive number of seconds")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluation time must include an unambiguous timezone")
        evaluated_at = evaluated_at.astimezone(timezone.utc)
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, payload, revision
                FROM state_records
                WHERE kind = 'run' AND key = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"state record does not exist: run/{run_id}")
            status, encoded, revision = str(row[0]), str(row[1]), int(row[2])
            payload = json.loads(encoded)
            if (
                status not in {"queued", "running"}
                or payload.get("agent_id") != expected_agent_id
                or revision != expected_revision
            ):
                raise StateConflictError(f"state run reassignment conflict: {run_id}")

            owner = connection.execute(
                "SELECT payload FROM state_records WHERE kind = 'agent' AND key = ?",
                (expected_agent_id,),
            ).fetchone()
            if owner is None:
                raise StateConflictError(f"state run owner is not registered: {expected_agent_id}")
            replacement = connection.execute(
                "SELECT 1 FROM state_records WHERE kind = 'agent' AND key = ?",
                (replacement_agent_id,),
            ).fetchone()
            if replacement is None:
                raise StateConflictError(
                    f"state replacement agent is not registered: {replacement_agent_id}"
                )

            last_seen_value = json.loads(str(owner[0])).get("last_seen")
            if not isinstance(last_seen_value, str) or not last_seen_value.strip():
                raise StateConflictError(
                    f"state run owner has no recorded heartbeat: {expected_agent_id}"
                )
            try:
                last_seen = datetime.fromisoformat(last_seen_value)
            except ValueError as error:
                raise StateConflictError(
                    f"state run owner has invalid last_seen: {expected_agent_id}"
                ) from error
            if last_seen.tzinfo is None or last_seen.utcoffset() is None:
                raise StateConflictError(
                    f"state run owner has ambiguous last_seen: {expected_agent_id}"
                )
            elapsed = (evaluated_at - last_seen.astimezone(timezone.utc)).total_seconds()
            if elapsed <= threshold_seconds:
                raise StateConflictError(f"state run owner is not stale: {run_id}")

            reassigned_payload = {**payload, "agent_id": replacement_agent_id}
            reassigned_revision = revision + 1
            connection.execute(
                """
                UPDATE state_records SET payload = ?, revision = ?
                WHERE kind = 'run' AND key = ?
                """,
                (self._encode_payload(reassigned_payload), reassigned_revision, run_id),
            )
            self._append_run_history(
                connection,
                run_id,
                transition="claim_reassigned",
                status=status,
                agent_id=replacement_agent_id,
                execution_kind=None,
            )
            connection.commit()
        return StateRecord(
            "run", run_id, status, reassigned_payload, reassigned_revision
        )

    def retry_failed_step(
        self,
        step_id: str,
        new_step_id: str,
        *,
        expected_step_revision: int,
        expected_run_revision: int,
    ) -> tuple[StateRecord, StateRecord, StateRecord]:
        """Atomically requeue one failed step as a new attempt in one transaction."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("step", step_id)
        self._validate_identity("step", new_step_id)
        if step_id == new_step_id:
            raise ValueError("new step id must differ from the retried step id")
        if expected_step_revision < 1:
            raise ValueError("expected step revision must be positive")
        if expected_run_revision < 1:
            raise ValueError("expected run revision must be positive")
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            step_row = connection.execute(
                """
                SELECT status, payload, revision
                FROM state_records WHERE kind = 'step' AND key = ?
                """,
                (step_id,),
            ).fetchone()
            if step_row is None:
                raise KeyError(f"state record does not exist: step/{step_id}")
            step_status, step_encoded, step_revision = (
                str(step_row[0]), str(step_row[1]), int(step_row[2])
            )
            if step_status != "failed" or step_revision != expected_step_revision:
                raise StateConflictError(f"state step retry conflict: {step_id}")
            step_payload = json.loads(step_encoded)

            run_id = step_payload.get("run_id")
            run_row = connection.execute(
                """
                SELECT status, payload, revision
                FROM state_records WHERE kind = 'run' AND key = ?
                """,
                (run_id,),
            ).fetchone()
            if run_row is None:  # Defensive: durable step records must reference an existing run.
                raise KeyError(f"state record does not exist: run/{run_id}")
            run_status, run_encoded, run_revision = (
                str(run_row[0]), str(run_row[1]), int(run_row[2])
            )
            if run_status != "failed" or run_revision != expected_run_revision:
                raise StateConflictError(f"state run retry conflict: {run_id}")
            run_payload = json.loads(run_encoded)

            if connection.execute(
                "SELECT 1 FROM state_records WHERE kind = 'step' AND key = ?",
                (new_step_id,),
            ).fetchone() is not None:
                raise StateConflictError(f"state record already exists: step/{new_step_id}")

            position_rows = connection.execute(
                "SELECT payload FROM state_records WHERE kind = 'step'"
            ).fetchall()
            positions = [
                int(document["position"])
                for (encoded,) in position_rows
                if (document := json.loads(str(encoded))).get("run_id") == run_id
            ]
            new_step_payload: dict[str, object] = {
                "run_id": run_id,
                "position": max(positions, default=0) + 1,
                "objective": step_payload["objective"],
                "retried_step_id": step_id,
            }
            for key in (
                "command",
                "timeout",
                "message",
                "sandbox_policy",
                "context_step_ids",
                "memory_names",
                "tools",
                "tool_iteration_budget",
                "response_artifact_name",
            ):
                if key in step_payload:
                    new_step_payload[key] = step_payload[key]
            approval_required = bool(step_payload.get("approval_required", False))
            new_step_payload["approval_required"] = approval_required
            if approval_required:
                new_step_payload["approval_status"] = "pending"
            new_step_encoded = self._encode_payload(new_step_payload)
            connection.execute(
                """
                INSERT INTO state_records (kind, key, status, payload, revision)
                VALUES ('step', ?, 'queued', ?, 1)
                """,
                (new_step_id, new_step_encoded),
            )

            reopened_run_payload = {
                key: value for key, value in run_payload.items() if key != "output"
            }
            reopened_run_revision = run_revision + 1
            connection.execute(
                """
                UPDATE state_records SET status = 'queued', payload = ?, revision = ?
                WHERE kind = 'run' AND key = ?
                """,
                (
                    self._encode_payload(reopened_run_payload),
                    reopened_run_revision,
                    run_id,
                ),
            )
            self._append_run_history(
                connection,
                run_id,
                transition="step_retried",
                status="queued",
                step_id=new_step_id,
                agent_id=run_payload.get("agent_id"),
                execution_kind=None,
                retried_step_id=step_id,
            )
            connection.commit()
        return (
            StateRecord("step", step_id, step_status, step_payload, step_revision),
            StateRecord("step", new_step_id, "queued", new_step_payload, 1),
            StateRecord("run", run_id, "queued", reopened_run_payload, reopened_run_revision),
        )

    def dispatch_delegation_step(
        self,
        step_id: str,
        child_run_id: str,
        *,
        expected_step_revision: int,
        step_payload: Mapping[str, object],
        run_id: str,
        expected_run_status: str,
        expected_run_revision: int,
        run_payload: Mapping[str, object] | None,
        child_payload: Mapping[str, object],
        target_agent_id: str | None,
        expected_policy_rule_ids: Sequence[str] | None = None,
    ) -> tuple[StateRecord, StateRecord, StateRecord]:
        """Atomically dispatch one queued delegation step and insert its linked child run.

        Reading the parent step and run under one ``BEGIN IMMEDIATE`` write
        lock, then inserting the new child run and updating the parent step
        (and, when it was still queued, the parent run) in the same
        transaction, guarantees a competing dispatch of the same queued step
        conflicts on the step's expected revision rather than racing to
        create two child runs. ``run_payload`` is ``None`` when the parent
        run is already running and needs no rewrite.
        """

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("step", step_id)
        self._validate_identity("run", run_id)
        self._validate_identity("run", child_run_id)
        if expected_step_revision < 1:
            raise ValueError("expected step revision must be positive")
        if expected_run_revision < 1:
            raise ValueError("expected run revision must be positive")
        if not expected_run_status.strip():
            raise ValueError("expected run status must not be empty")
        if target_agent_id is not None and not target_agent_id.strip():
            raise ValueError("target agent id must not be empty")
        encoded_step = self._encode_payload(step_payload)
        encoded_child = self._encode_payload(child_payload)
        encoded_run = None if run_payload is None else self._encode_payload(run_payload)
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_policy_rule_snapshot(connection, expected_policy_rule_ids)
            step_row = connection.execute(
                "SELECT status, revision FROM state_records WHERE kind = 'step' AND key = ?",
                (step_id,),
            ).fetchone()
            if step_row is None:
                raise KeyError(f"state record does not exist: step/{step_id}")
            step_status, step_revision = str(step_row[0]), int(step_row[1])
            if step_status != "queued" or step_revision != expected_step_revision:
                raise StateConflictError(
                    f"state step delegation dispatch conflict: {step_id}"
                )

            run_row = connection.execute(
                "SELECT status, payload, revision FROM state_records WHERE kind = 'run' AND key = ?",
                (run_id,),
            ).fetchone()
            if run_row is None:  # Defensive: durable step records must reference an existing run.
                raise KeyError(f"state record does not exist: run/{run_id}")
            run_status, run_encoded, run_revision = (
                str(run_row[0]), str(run_row[1]), int(run_row[2])
            )
            if run_status != expected_run_status or run_revision != expected_run_revision:
                raise StateConflictError(f"state run transition conflict: {run_id}")
            run_agent_id = json.loads(run_encoded).get("agent_id")

            if target_agent_id is not None and connection.execute(
                "SELECT 1 FROM state_records WHERE kind = 'agent' AND key = ?",
                (target_agent_id,),
            ).fetchone() is None:
                raise StateConflictError(
                    f"state delegation target agent is not registered: {target_agent_id}"
                )

            if connection.execute(
                "SELECT 1 FROM state_records WHERE kind = 'run' AND key = ?",
                (child_run_id,),
            ).fetchone() is not None:
                raise StateConflictError(f"state record already exists: run/{child_run_id}")

            connection.execute(
                """
                INSERT INTO state_records (kind, key, status, payload, revision)
                VALUES ('run', ?, 'queued', ?, 1)
                """,
                (child_run_id, encoded_child),
            )
            self._append_run_history(
                connection,
                child_run_id,
                transition="created",
                status="queued",
                agent_id=target_agent_id,
                execution_kind=None,
                parent_run_id=run_id,
                parent_step_id=step_id,
            )

            new_step_revision = step_revision + 1
            connection.execute(
                """
                UPDATE state_records SET status = 'running', payload = ?, revision = ?
                WHERE kind = 'step' AND key = ?
                """,
                (encoded_step, new_step_revision, step_id),
            )

            if run_payload is not None:
                final_run_status = "running"
                final_run_payload = json.loads(encoded_run)
                final_run_revision = run_revision + 1
                connection.execute(
                    """
                    UPDATE state_records SET status = 'running', payload = ?, revision = ?
                    WHERE kind = 'run' AND key = ?
                    """,
                    (encoded_run, final_run_revision, run_id),
                )
                self._append_run_history(
                    connection,
                    run_id,
                    transition="run_started",
                    status="running",
                    agent_id=run_agent_id,
                    execution_kind=None,
                )
            else:
                final_run_status = run_status
                final_run_payload = json.loads(run_encoded)
                final_run_revision = run_revision
            self._append_run_history(
                connection,
                run_id,
                transition="step_delegated",
                status="running",
                step_id=step_id,
                agent_id=run_agent_id,
                execution_kind="delegation",
                delegated_run_id=child_run_id,
            )
            connection.commit()
        return (
            StateRecord("step", step_id, "running", json.loads(encoded_step), new_step_revision),
            StateRecord("run", run_id, final_run_status, final_run_payload, final_run_revision),
            StateRecord("run", child_run_id, "queued", json.loads(encoded_child), 1),
        )

    def transition_run(
        self,
        run_id: str,
        *,
        expected_status: str,
        expected_revision: int,
        status: str,
        payload: Mapping[str, object],
        execution_kind: str | None = None,
    ) -> StateRecord:
        """Advance one run in a write transaction when it matches an expected state."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("run", run_id, status)
        if not expected_status.strip():
            raise ValueError("expected status must not be empty")
        if expected_revision < 1:
            raise ValueError("expected revision must be positive")
        encoded = self._encode_payload(payload)
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, revision
                FROM state_records
                WHERE kind = 'run' AND key = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"state record does not exist: run/{run_id}")

            current_status, current_revision = str(row[0]), int(row[1])
            if current_status != expected_status or current_revision != expected_revision:
                raise StateConflictError(f"state run transition conflict: {run_id}")

            new_revision = current_revision + 1
            connection.execute(
                """
                UPDATE state_records
                SET status = ?, payload = ?, revision = ?
                WHERE kind = 'run' AND key = ?
                """,
                (status, encoded, new_revision, run_id),
            )
            self._append_run_history(
                connection,
                run_id,
                transition="transitioned",
                status=status,
                agent_id=payload.get("agent_id"),
                execution_kind=execution_kind,
            )
            connection.commit()
        return StateRecord("run", run_id, status, json.loads(encoded), new_revision)

    def transition_step(
        self,
        step_id: str,
        *,
        expected_status: str,
        expected_revision: int,
        status: str,
        payload: Mapping[str, object],
        run_id: str | None = None,
        agent_id: str | None = None,
        execution_kind: str | None = None,
        context_step_ids: Sequence[str] | None = None,
        memory_names: Sequence[str] | None = None,
        required_capability: str | None = None,
        resolved_provider: str | None = None,
        resolved_model: str | None = None,
        routing_reason: str | None = None,
        expected_policy_rule_ids: Sequence[str] | None = None,
    ) -> StateRecord:
        """Advance one step in a write transaction when it matches an expected state."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("step", step_id, status)
        if not expected_status.strip():
            raise ValueError("expected status must not be empty")
        if expected_revision < 1:
            raise ValueError("expected revision must be positive")
        encoded = self._encode_payload(payload)
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_policy_rule_snapshot(connection, expected_policy_rule_ids)
            row = connection.execute(
                """
                SELECT status, revision
                FROM state_records
                WHERE kind = 'step' AND key = ?
                """,
                (step_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"state record does not exist: step/{step_id}")

            current_status, current_revision = str(row[0]), int(row[1])
            if current_status != expected_status or current_revision != expected_revision:
                raise StateConflictError(f"state step transition conflict: {step_id}")

            new_revision = current_revision + 1
            connection.execute(
                """
                UPDATE state_records
                SET status = ?, payload = ?, revision = ?
                WHERE kind = 'step' AND key = ?
                """,
                (status, encoded, new_revision, step_id),
            )
            if run_id is not None:
                self._append_run_history(
                    connection,
                    run_id,
                    transition=("step_started" if status == "running" else f"step_{status}"),
                    status=status,
                    step_id=step_id,
                    agent_id=agent_id,
                    execution_kind=execution_kind,
                    context_step_ids=context_step_ids,
                    memory_names=memory_names,
                    required_capability=required_capability,
                    resolved_provider=resolved_provider,
                    resolved_model=resolved_model,
                    routing_reason=routing_reason,
                )
            connection.commit()
        return StateRecord("step", step_id, status, json.loads(encoded), new_revision)

    def claim_next_run(self, agent_id: str) -> StateRecord | None:
        """Assign the first queued, unassigned run in stable key order."""

        if self.read_only:
            raise ValueError("state store is read-only")
        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT key, status, payload, revision
                FROM state_records
                WHERE kind = 'run'
                  AND status = 'queued'
                  AND json_extract(payload, '$.agent_id') IS NULL
                ORDER BY key
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            run_id, status, encoded, revision = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                int(row[3]),
            )
            payload = json.loads(encoded)
            claimed_payload = {**payload, "agent_id": agent_id}
            claimed_encoded = self._encode_payload(claimed_payload)
            claimed_revision = revision + 1
            connection.execute(
                """
                UPDATE state_records
                SET payload = ?, revision = ?
                WHERE kind = 'run' AND key = ?
                """,
                (claimed_encoded, claimed_revision, run_id),
            )
            self._append_run_history(
                connection,
                run_id,
                transition="claimed",
                status=status,
                agent_id=agent_id,
                execution_kind=None,
            )
            connection.commit()
        return StateRecord("run", run_id, status, claimed_payload, claimed_revision)

    def prune_run(
        self, run_id: str, *, terminal_statuses: frozenset[str]
    ) -> tuple[StateRecord, tuple[StateRecord, ...]]:
        """Atomically delete one terminal run and all of its durable steps."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("run", run_id)
        if not terminal_statuses:
            raise ValueError("terminal statuses must not be empty")
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT kind, key, status, payload, revision
                FROM state_records WHERE kind = 'run' AND key = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"state record does not exist: run/{run_id}")
            run = self._record(row)
            if run.status not in terminal_statuses:
                raise StateConflictError(f"state run is not terminal: {run_id}")

            step_rows = connection.execute(
                """
                SELECT kind, key, status, payload, revision
                FROM state_records
                WHERE kind = 'step' AND json_extract(payload, '$.run_id') = ?
                """,
                (run_id,),
            ).fetchall()
            steps = tuple(
                sorted(
                    (self._record(step_row) for step_row in step_rows),
                    key=lambda record: int(record.payload["position"]),
                )
            )
            for step in steps:
                self._delete_on_connection(connection, "step", step.key)
            self._delete_on_connection(connection, "run", run_id)
            connection.commit()
        return run, steps

    def append_step(
        self,
        step_id: str,
        run_id: str,
        *,
        status: str,
        payload: Mapping[str, object],
    ) -> StateRecord:
        """Insert one step at its run's next position in a write transaction."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("step", step_id, status)
        self._validate_identity("run", run_id)
        base_payload = dict(payload)
        base_payload.pop("run_id", None)
        base_payload.pop("position", None)
        self._encode_payload(base_payload)
        self.initialize()

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM state_records WHERE kind = 'step' AND key = ?",
                (step_id,),
            ).fetchone() is not None:
                raise StateConflictError(f"state record already exists: step/{step_id}")

            rows = connection.execute(
                "SELECT payload FROM state_records WHERE kind = 'step'"
            ).fetchall()
            positions = [
                int(document["position"])
                for (encoded,) in rows
                if (document := json.loads(str(encoded))).get("run_id") == run_id
            ]
            stored_payload = {
                **base_payload,
                "run_id": run_id,
                "position": max(positions, default=0) + 1,
            }
            encoded = self._encode_payload(stored_payload)
            connection.execute(
                """
                INSERT INTO state_records (kind, key, status, payload, revision)
                VALUES ('step', ?, ?, ?, 1)
                """,
                (step_id, status, encoded),
            )
            connection.commit()
        return StateRecord("step", step_id, status, stored_payload, 1)

    def decide_plan(
        self,
        plan_id: str,
        run_id: str,
        *,
        status: str,
        payload: Mapping[str, object],
        expected_plan_status: str,
        expected_plan_revision: int,
        expected_run_status: str,
        expected_run_revision: int,
        steps: Sequence[tuple[str, str, Mapping[str, object]]] = (),
        history: Sequence[RunHistoryEntry] = (),
    ) -> tuple[StateRecord, tuple[StateRecord, ...]]:
        """Atomically decide one draft and optionally materialize its queued steps."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity("plan", plan_id, status)
        self._validate_identity("run", run_id)
        encoded_plan = self._encode_payload(payload)
        prepared_steps: list[tuple[str, str, str]] = []
        for step_id, step_status, step_payload in steps:
            self._validate_identity("step", step_id, step_status)
            base_payload = dict(step_payload)
            base_payload.pop("run_id", None)
            base_payload.pop("position", None)
            prepared_steps.append(
                (step_id, step_status, self._encode_payload(base_payload))
            )
        if len({step_id for step_id, _, _ in prepared_steps}) != len(prepared_steps):
            raise ValueError("materialized plan step ids must be unique")

        self.initialize()
        stored_steps: list[StateRecord] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for kind, key, expected_status, expected_revision in (
                ("plan", plan_id, expected_plan_status, expected_plan_revision),
                ("run", run_id, expected_run_status, expected_run_revision),
            ):
                row = connection.execute(
                    "SELECT status, revision FROM state_records WHERE kind = ? AND key = ?",
                    (kind, key),
                ).fetchone()
                if (
                    row is None
                    or str(row[0]) != expected_status
                    or int(row[1]) != expected_revision
                ):
                    raise StateConflictError(f"state {kind} transition conflict: {key}")

            for step_id, _, _ in prepared_steps:
                if connection.execute(
                    "SELECT 1 FROM state_records WHERE kind = 'step' AND key = ?",
                    (step_id,),
                ).fetchone() is not None:
                    raise StateConflictError(
                        f"state record already exists: step/{step_id}"
                    )

            rows = connection.execute(
                "SELECT payload FROM state_records WHERE kind = 'step'"
            ).fetchall()
            positions = [
                int(document["position"])
                for (encoded,) in rows
                if (document := json.loads(str(encoded))).get("run_id") == run_id
            ]
            next_position = max(positions, default=0) + 1
            for offset, (step_id, step_status, encoded_base) in enumerate(prepared_steps):
                stored_payload = {
                    **json.loads(encoded_base),
                    "run_id": run_id,
                    "position": next_position + offset,
                }
                encoded_step = self._encode_payload(stored_payload)
                connection.execute(
                    """
                    INSERT INTO state_records (kind, key, status, payload, revision)
                    VALUES ('step', ?, ?, ?, 1)
                    """,
                    (step_id, step_status, encoded_step),
                )
                stored_steps.append(
                    StateRecord("step", step_id, step_status, stored_payload, 1)
                )

            stored_plan = self._put_on_connection(
                connection, "plan", plan_id, status, encoded_plan
            )
            for entry in history:
                self._append_run_history(
                    connection,
                    entry.run_id,
                    transition=entry.transition,
                    status=entry.status,
                    step_id=entry.step_id,
                    agent_id=entry.agent_id,
                    execution_kind=entry.execution_kind,
                    retried_step_id=entry.retried_step_id,
                    context_step_ids=entry.context_step_ids,
                    memory_names=entry.memory_names,
                    plan_id=entry.plan_id,
                    required_capability=entry.required_capability,
                    resolved_provider=entry.resolved_provider,
                    resolved_model=entry.resolved_model,
                    routing_reason=entry.routing_reason,
                    artifact_name=entry.artifact_name,
                    parent_run_id=entry.parent_run_id,
                    parent_step_id=entry.parent_step_id,
                    delegated_run_id=entry.delegated_run_id,
                    tool_name=entry.tool_name,
                    tool_outcome=entry.tool_outcome,
                    tool_iteration=entry.tool_iteration,
                    tool_phase=entry.tool_phase,
                    policy_rule_id=entry.policy_rule_id,
                    policy_reason=entry.policy_reason,
                )
            connection.commit()
        return stored_plan, tuple(stored_steps)

    def list_run_history(self, run_id: str) -> tuple[RunHistoryEntry, ...]:
        """Return one run's durable history entries in stable sequence order."""

        self._validate_identity("run", run_id)
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT run_id, sequence, transition, status, step_id, agent_id,
                       execution_kind, retried_step_id, context_step_ids, memory_names, plan_id,
                       required_capability, resolved_provider, resolved_model,
                       routing_reason, artifact_name, parent_run_id, parent_step_id,
                       delegated_run_id, tool_name, tool_outcome,
                       tool_iteration, tool_phase, policy_rule_id, policy_reason
                FROM run_history WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            RunHistoryEntry(
                run_id=str(row[0]),
                sequence=int(row[1]),
                transition=str(row[2]),
                status=str(row[3]),
                step_id=row[4],
                agent_id=row[5],
                execution_kind=row[6],
                retried_step_id=row[7],
                context_step_ids=None if row[8] is None else tuple(json.loads(row[8])),
                memory_names=None if row[9] is None else tuple(json.loads(row[9])),
                plan_id=row[10],
                required_capability=row[11],
                resolved_provider=row[12],
                resolved_model=row[13],
                routing_reason=row[14],
                artifact_name=row[15],
                parent_run_id=row[16],
                parent_step_id=row[17],
                delegated_run_id=row[18],
                tool_name=row[19],
                tool_outcome=row[20],
                tool_iteration=row[21],
                tool_phase=row[22],
                policy_rule_id=row[23],
                policy_reason=row[24],
            )
            for row in rows
        )

    def get(self, kind: str, key: str) -> StateRecord | None:
        """Return a stored document, or ``None`` when it is absent."""

        self._validate_identity(kind, key)
        self.initialize()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT kind, key, status, payload, revision
                FROM state_records WHERE kind = ? AND key = ?
                """,
                (kind, key),
            ).fetchone()
        return None if row is None else self._record(row)

    def list(self, kind: str) -> tuple[StateRecord, ...]:
        """Return documents of one kind in stable key order."""

        self._validate_identity(kind)
        self.initialize()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT kind, key, status, payload, revision
                FROM state_records WHERE kind = ? ORDER BY key
                """,
                (kind,),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def delete(self, kind: str, key: str) -> bool:
        """Delete a document and report whether it existed."""

        if self.read_only:
            raise ValueError("state store is read-only")
        self._validate_identity(kind, key)
        self.initialize()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM state_records WHERE kind = ? AND key = ?", (kind, key)
            )
            connection.commit()
            return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        target = f"file:{self.path}?mode=ro" if self.read_only else self.path
        connection = sqlite3.connect(target, timeout=30, uri=self.read_only)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _check_policy_rule_snapshot(
        connection: sqlite3.Connection,
        expected_policy_rule_ids: Sequence[str] | None,
    ) -> None:
        """Reject a step-dispatch transaction when its evaluated rule set changed."""

        if expected_policy_rule_ids is None:
            return
        current = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT key FROM state_records WHERE kind = 'policy_rule' ORDER BY key"
            ).fetchall()
        )
        if current != tuple(expected_policy_rule_ids):
            raise StateConflictError("execution policy rule snapshot changed")

    def _put_on_connection(
        self,
        connection: sqlite3.Connection,
        kind: str,
        key: str,
        status: str,
        encoded: str,
    ) -> StateRecord:
        """Write one prepared record on a caller-owned transaction."""

        current = connection.execute(
            "SELECT revision FROM state_records WHERE kind = ? AND key = ?",
            (kind, key),
        ).fetchone()
        revision = 1 if current is None else int(current[0]) + 1
        connection.execute(
            """
            INSERT INTO state_records (kind, key, status, payload, revision)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(kind, key) DO UPDATE SET
                status = excluded.status,
                payload = excluded.payload,
                revision = excluded.revision
            """,
            (kind, key, status, encoded, revision),
        )
        return StateRecord(kind, key, status, json.loads(encoded), revision)

    @staticmethod
    def _append_run_history(
        connection: sqlite3.Connection,
        run_id: str,
        *,
        transition: str,
        status: str,
        agent_id: object,
        execution_kind: str | None,
        step_id: str | None = None,
        retried_step_id: str | None = None,
        context_step_ids: Sequence[str] | None = None,
        memory_names: Sequence[str] | None = None,
        plan_id: str | None = None,
        required_capability: str | None = None,
        resolved_provider: str | None = None,
        resolved_model: str | None = None,
        routing_reason: str | None = None,
        artifact_name: str | None = None,
        parent_run_id: str | None = None,
        parent_step_id: str | None = None,
        delegated_run_id: str | None = None,
        tool_name: str | None = None,
        tool_outcome: str | None = None,
        tool_iteration: int | None = None,
        tool_phase: str | None = None,
        policy_rule_id: str | None = None,
        policy_reason: str | None = None,
    ) -> None:
        """Append one ordered history entry on a caller-owned run mutation transaction."""

        row = connection.execute(
            "SELECT MAX(sequence) FROM run_history WHERE run_id = ?", (run_id,)
        ).fetchone()
        sequence = 1 if row is None or row[0] is None else int(row[0]) + 1
        connection.execute(
            """
            INSERT INTO run_history
                (run_id, sequence, transition, status, step_id, agent_id,
                 execution_kind, retried_step_id, context_step_ids, memory_names, plan_id,
                 required_capability, resolved_provider, resolved_model,
                 routing_reason, artifact_name, parent_run_id, parent_step_id,
                 delegated_run_id, tool_name, tool_outcome, tool_iteration, tool_phase,
                 policy_rule_id, policy_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                transition,
                status,
                step_id,
                agent_id if isinstance(agent_id, str) else None,
                execution_kind,
                retried_step_id,
                None if context_step_ids is None else json.dumps(list(context_step_ids)),
                None if memory_names is None else json.dumps(list(memory_names)),
                plan_id,
                required_capability,
                resolved_provider,
                resolved_model,
                routing_reason,
                artifact_name,
                parent_run_id,
                parent_step_id,
                delegated_run_id,
                tool_name,
                tool_outcome,
                tool_iteration,
                tool_phase,
                policy_rule_id,
                policy_reason,
            ),
        )

    @staticmethod
    def _delete_on_connection(
        connection: sqlite3.Connection, kind: str, key: str
    ) -> None:
        """Delete one known record on a caller-owned transaction."""

        cursor = connection.execute(
            "DELETE FROM state_records WHERE kind = ? AND key = ?", (kind, key)
        )
        if cursor.rowcount != 1:
            raise StateConflictError(f"state record disappeared: {kind}/{key}")

    @classmethod
    def _validate_identity(
        cls, kind: str, key: str | None = None, status: str | None = None
    ) -> None:
        if kind not in cls.KINDS:
            raise ValueError(f"unsupported state kind: {kind}")
        if key is not None and not key.strip():
            raise ValueError("state key must not be empty")
        if status is not None and not status.strip():
            raise ValueError("state status must not be empty")

    @staticmethod
    def _encode_payload(payload: Mapping[str, object]) -> str:
        try:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("state payload must be JSON serializable") from error

    @staticmethod
    def _record(row: tuple[object, ...]) -> StateRecord:
        kind, key, status, payload, revision = row
        return StateRecord(
            kind=str(kind),
            key=str(key),
            status=str(status),
            payload=json.loads(str(payload)),
            revision=int(revision),
        )
