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
