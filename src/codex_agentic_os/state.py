"""Durable SQLite state for plans, decisions, runs, and agents."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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

    KINDS = frozenset({"plan", "decision", "run", "agent"})

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        """Create the database and schema if they do not exist."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state_records (
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY (kind, key),
                    CHECK (kind IN ('plan', 'decision', 'run', 'agent')),
                    CHECK (revision > 0)
                )
                """
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

        self._validate_identity(kind, key)
        self.initialize()
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "DELETE FROM state_records WHERE kind = ? AND key = ?", (kind, key)
            )
            connection.commit()
            return cursor.rowcount == 1

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

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
