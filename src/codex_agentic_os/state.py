"""Durable SQLite state for plans, decisions, runs, and agents."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
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


class StateConflictError(ValueError):
    """Raised when insert-only persistence finds an existing identity."""


class StateStore:
    """Persist runtime state in a repository-local SQLite database."""

    KINDS = frozenset({"plan", "decision", "run", "step", "agent"})
    _CREATE_TABLE = """
        CREATE TABLE {clause} state_records (
            kind TEXT NOT NULL,
            key TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            revision INTEGER NOT NULL,
            PRIMARY KEY (kind, key),
            CHECK (kind IN ('plan', 'decision', 'run', 'step', 'agent')),
            CHECK (revision > 0)
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
            if table is None or "'step'" not in str(table[0]):
                raise ValueError(f"state database has an incompatible schema: {self.path}")
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(self._CREATE_TABLE.format(clause="IF NOT EXISTS"))
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'state_records'"
            ).fetchone()[0]
            if "'step'" not in schema:
                connection.execute("ALTER TABLE state_records RENAME TO state_records_old")
                connection.execute(self._CREATE_TABLE.format(clause=""))
                connection.execute(
                    "INSERT INTO state_records SELECT * FROM state_records_old"
                )
                connection.execute("DROP TABLE state_records_old")
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
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise StateConflictError(
                f"state record already exists: {kind}/{key}"
            ) from error
        return StateRecord(kind, key, status, json.loads(encoded), 1)

    def put_many(
        self,
        records: Sequence[tuple[str, str, str, Mapping[str, object]]],
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
            for kind, key, status, encoded in prepared:
                stored.append(
                    self._put_on_connection(connection, kind, key, status, encoded)
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
            connection.commit()
        return StateRecord("run", run_id, status, released_payload, released_revision)

    def transition_run(
        self,
        run_id: str,
        *,
        expected_status: str,
        expected_revision: int,
        status: str,
        payload: Mapping[str, object],
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
