"""Runtime selection and durable run-lifecycle coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Mapping

from .state import StateRecord, StateStore


class RuntimeKind(StrEnum):
    """Candidate orchestration runtimes."""

    INTERNAL = "internal"
    LANGCHAIN_DEEPAGENTS = "langchain_deepagents"


@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    """Describes how agents are orchestrated."""

    kind: RuntimeKind = RuntimeKind.INTERNAL
    rationale: str = "Small internal core first; adapters can wrap external runtimes."

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        data = asdict(self)
        data["kind"] = self.kind.value
        return data


class RunStatus(StrEnum):
    """Portable lifecycle states for one agent run."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentRun:
    """Typed view of a durable run record."""

    run_id: str
    objective: str
    status: RunStatus
    revision: int
    agent_id: str | None = None
    output: Mapping[str, object] | None = None


class RunCoordinator:
    """Create runs and enforce their provider-neutral lifecycle transitions."""

    _TRANSITIONS = {
        RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
        RunStatus.RUNNING: frozenset(
            {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
        ),
        RunStatus.SUCCEEDED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.CANCELLED: frozenset(),
    }

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def create(
        self, run_id: str, *, objective: str, agent_id: str | None = None
    ) -> AgentRun:
        """Create a queued run, rejecting duplicate identifiers."""

        if not objective.strip():
            raise ValueError("run objective must not be empty")
        if agent_id is not None and not agent_id.strip():
            raise ValueError("agent id must not be empty")
        if self.store.get("run", run_id) is not None:
            raise ValueError(f"run already exists: {run_id}")
        payload: dict[str, object] = {"objective": objective}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        return self._run(
            self.store.put("run", run_id, status=RunStatus.QUEUED, payload=payload)
        )

    def get(self, run_id: str) -> AgentRun | None:
        """Return a run when it exists."""

        record = self.store.get("run", run_id)
        return None if record is None else self._run(record)

    def transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        output: Mapping[str, object] | None = None,
    ) -> AgentRun:
        """Advance a run through an allowed lifecycle edge."""

        current = self.get(run_id)
        if current is None:
            raise KeyError(f"run does not exist: {run_id}")
        if status not in self._TRANSITIONS[current.status]:
            raise ValueError(f"invalid run transition: {current.status} -> {status}")
        if output is not None and status not in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
            raise ValueError("run output is only valid for succeeded or failed runs")
        payload: dict[str, object] = {"objective": current.objective}
        if current.agent_id is not None:
            payload["agent_id"] = current.agent_id
        if output is not None:
            payload["output"] = dict(output)
        return self._run(
            self.store.put("run", run_id, status=status, payload=payload)
        )

    @staticmethod
    def _run(record: StateRecord) -> AgentRun:
        objective = record.payload.get("objective")
        agent_id = record.payload.get("agent_id")
        output = record.payload.get("output")
        if not isinstance(objective, str):
            raise ValueError(f"run record has invalid objective: {record.key}")
        if agent_id is not None and not isinstance(agent_id, str):
            raise ValueError(f"run record has invalid agent id: {record.key}")
        if output is not None and not isinstance(output, dict):
            raise ValueError(f"run record has invalid output: {record.key}")
        try:
            status = RunStatus(record.status)
        except ValueError as error:
            raise ValueError(f"run record has invalid status: {record.key}") from error
        return AgentRun(
            run_id=record.key,
            objective=objective,
            status=status,
            revision=record.revision,
            agent_id=agent_id,
            output=output,
        )
