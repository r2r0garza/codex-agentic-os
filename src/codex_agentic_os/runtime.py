"""Runtime selection and durable run-lifecycle coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence

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


class StepStatus(StrEnum):
    """Portable lifecycle states for one ordered run step."""

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


@dataclass(frozen=True, slots=True)
class RunStep:
    """Typed view of a durable, ordered unit of work within a run."""

    step_id: str
    run_id: str
    position: int
    objective: str
    status: StepStatus
    revision: int
    output: Mapping[str, object] | None = None


class ExecutionResult(Protocol):
    """Backend-neutral command result accepted by run coordination."""

    command: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


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
    _STEP_TRANSITIONS = {
        StepStatus.QUEUED: frozenset({StepStatus.RUNNING, StepStatus.CANCELLED}),
        StepStatus.RUNNING: frozenset(
            {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED}
        ),
        StepStatus.SUCCEEDED: frozenset(),
        StepStatus.FAILED: frozenset(),
        StepStatus.CANCELLED: frozenset(),
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

    def cancel(self, run_id: str) -> AgentRun:
        """Cancel a run and each of its queued or running steps."""

        current = self.get(run_id)
        if current is None:
            raise KeyError(f"run does not exist: {run_id}")
        if RunStatus.CANCELLED not in self._TRANSITIONS[current.status]:
            raise ValueError(
                f"invalid run transition: {current.status} -> {RunStatus.CANCELLED}"
            )
        for step in self.list_steps(run_id):
            if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
                self.transition_step(step.step_id, StepStatus.CANCELLED)
        return self.transition(run_id, RunStatus.CANCELLED)

    def add_step(self, run_id: str, step_id: str, *, objective: str) -> RunStep:
        """Append a queued step to a non-terminal run."""

        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"cannot add a step to terminal run: {run_id}")
        if not objective.strip():
            raise ValueError("step objective must not be empty")
        if self.store.get("step", step_id) is not None:
            raise ValueError(f"step already exists: {step_id}")
        position = len(self.list_steps(run_id)) + 1
        return self._step(
            self.store.put(
                "step",
                step_id,
                status=StepStatus.QUEUED,
                payload={
                    "run_id": run_id,
                    "position": position,
                    "objective": objective,
                },
            )
        )

    def get_step(self, step_id: str) -> RunStep | None:
        """Return a step when it exists."""

        record = self.store.get("step", step_id)
        return None if record is None else self._step(record)

    def list_steps(self, run_id: str) -> tuple[RunStep, ...]:
        """Return a run's steps in durable position order."""

        if self.get(run_id) is None:
            raise KeyError(f"run does not exist: {run_id}")
        steps = (
            self._step(record)
            for record in self.store.list("step")
            if record.payload.get("run_id") == run_id
        )
        return tuple(sorted(steps, key=lambda step: (step.position, step.step_id)))

    def transition_step(
        self,
        step_id: str,
        status: StepStatus,
        *,
        output: Mapping[str, object] | None = None,
    ) -> RunStep:
        """Advance a step through an allowed lifecycle edge."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        if status not in self._STEP_TRANSITIONS[current.status]:
            raise ValueError(f"invalid step transition: {current.status} -> {status}")
        if output is not None and status not in {StepStatus.SUCCEEDED, StepStatus.FAILED}:
            raise ValueError("step output is only valid for succeeded or failed steps")
        payload: dict[str, object] = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
        }
        if output is not None:
            payload["output"] = dict(output)
        return self._step(
            self.store.put("step", step_id, status=status, payload=payload)
        )

    def complete_step_from_result(
        self, step_id: str, result: ExecutionResult
    ) -> tuple[RunStep, AgentRun]:
        """Persist a command result and complete its step and, when final, its run."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        run = self.get(current.run_id)
        if run is None:  # Defensive: durable step records must reference an existing run.
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status is not RunStatus.RUNNING:
            raise ValueError(f"run must be running to complete a step: {run.run_id}")
        if current.status is not StepStatus.RUNNING:
            raise ValueError(f"step must be running to record a result: {step_id}")

        output: dict[str, object] = {
            "command": list(result.command),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        step_status = (
            StepStatus.SUCCEEDED if result.returncode == 0 else StepStatus.FAILED
        )
        step = self.transition_step(step_id, step_status, output=output)

        if step_status is StepStatus.FAILED:
            run = self.transition(
                run.run_id,
                RunStatus.FAILED,
                output={"failed_step_id": step_id, "exit_code": result.returncode},
            )
        elif all(
            candidate.status is StepStatus.SUCCEEDED
            for candidate in self.list_steps(run.run_id)
        ):
            run = self.transition(
                run.run_id,
                RunStatus.SUCCEEDED,
                output={"completed_steps": len(self.list_steps(run.run_id))},
            )
        return step, run

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

    @staticmethod
    def _step(record: StateRecord) -> RunStep:
        run_id = record.payload.get("run_id")
        position = record.payload.get("position")
        objective = record.payload.get("objective")
        output = record.payload.get("output")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(f"step record has invalid run id: {record.key}")
        if not isinstance(position, int) or isinstance(position, bool) or position < 1:
            raise ValueError(f"step record has invalid position: {record.key}")
        if not isinstance(objective, str):
            raise ValueError(f"step record has invalid objective: {record.key}")
        if output is not None and not isinstance(output, dict):
            raise ValueError(f"step record has invalid output: {record.key}")
        try:
            status = StepStatus(record.status)
        except ValueError as error:
            raise ValueError(f"step record has invalid status: {record.key}") from error
        return RunStep(
            step_id=record.key,
            run_id=run_id,
            position=position,
            objective=objective,
            status=status,
            revision=record.revision,
            output=output,
        )
