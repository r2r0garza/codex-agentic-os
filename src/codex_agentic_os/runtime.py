"""Runtime selection and durable run-lifecycle coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence

from .state import StateConflictError, StateRecord, StateStore


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


class StepRecoveryReason(StrEnum):
    """Explicit reasons for failing a running step with an uncertain result."""

    INTERRUPTED = "interrupted"
    TIMED_OUT = "timed_out"


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
    command: tuple[str, ...] | None = None
    timeout: float | None = None


class ExecutionResult(Protocol):
    """Backend-neutral command result accepted by run coordination."""

    command: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


class SandboxExecutor(Protocol):
    """Injected boundary for executing one durable command."""

    def execute(
        self, argv: Sequence[str], *, timeout: float | None = None
    ) -> ExecutionResult: ...


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
        payload: dict[str, object] = {"objective": objective}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        try:
            record = self.store.insert(
                "run", run_id, status=RunStatus.QUEUED, payload=payload
            )
        except StateConflictError as error:
            raise ValueError(f"run already exists: {run_id}") from error
        return self._run(record)

    def get(self, run_id: str) -> AgentRun | None:
        """Return a run when it exists."""

        record = self.store.get("run", run_id)
        return None if record is None else self._run(record)

    def claim(self, run_id: str, agent_id: str) -> AgentRun:
        """Atomically assign one queued, unassigned run to an agent."""

        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        try:
            record = self.store.claim_run(run_id, agent_id)
        except StateConflictError as error:
            raise ValueError(f"run cannot be claimed: {run_id}") from error
        except KeyError as error:
            raise KeyError(f"run does not exist: {run_id}") from error
        return self._run(record)

    def release_claim(self, run_id: str, agent_id: str) -> AgentRun:
        """Atomically release an exact queued run assignment."""

        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        try:
            record = self.store.release_run_claim(run_id, agent_id)
        except StateConflictError as error:
            raise ValueError(f"run claim cannot be released: {run_id}") from error
        except KeyError as error:
            raise KeyError(f"run does not exist: {run_id}") from error
        return self._run(record)

    def claim_next(self, agent_id: str) -> AgentRun | None:
        """Atomically assign the first eligible queued run to an agent."""

        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        record = self.store.claim_next_run(agent_id)
        return None if record is None else self._run(record)

    def list_runs(self) -> tuple[AgentRun, ...]:
        """Return all durable runs in stable run identifier order."""

        return tuple(self._run(record) for record in self.store.list("run"))

    def prune(self, run_id: str) -> tuple[AgentRun, tuple[RunStep, ...]]:
        """Atomically remove one terminal run and its durable step history."""

        terminal_statuses = frozenset(
            {RunStatus.SUCCEEDED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value}
        )
        try:
            run_record, step_records = self.store.prune_run(
                run_id, terminal_statuses=terminal_statuses
            )
        except StateConflictError as error:
            raise ValueError(f"run is not terminal: {run_id}") from error
        except KeyError as error:
            raise KeyError(f"run does not exist: {run_id}") from error
        return self._run(run_record), tuple(self._step(record) for record in step_records)

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
        """Atomically cancel a run and each of its queued or running steps."""

        current = self.get(run_id)
        if current is None:
            raise KeyError(f"run does not exist: {run_id}")
        if RunStatus.CANCELLED not in self._TRANSITIONS[current.status]:
            raise ValueError(
                f"invalid run transition: {current.status} -> {RunStatus.CANCELLED}"
            )
        active_steps = tuple(
            step
            for step in self.list_steps(run_id)
            if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}
        )
        for step in active_steps:
            if StepStatus.CANCELLED not in self._STEP_TRANSITIONS[step.status]:
                raise ValueError(
                    f"invalid step transition: {step.status} -> {StepStatus.CANCELLED}"
                )

        records: list[tuple[str, str, str, Mapping[str, object]]] = []
        for step in active_steps:
            payload: dict[str, object] = {
                "run_id": step.run_id,
                "position": step.position,
                "objective": step.objective,
            }
            if step.command is not None:
                payload["command"] = list(step.command)
            if step.timeout is not None:
                payload["timeout"] = step.timeout
            records.append(("step", step.step_id, StepStatus.CANCELLED, payload))

        run_payload: dict[str, object] = {"objective": current.objective}
        if current.agent_id is not None:
            run_payload["agent_id"] = current.agent_id
        records.append(("run", run_id, RunStatus.CANCELLED, run_payload))
        stored = self.store.put_many(records)
        return self._run(stored[-1])

    def start_next_step(self, run_id: str) -> RunStep | None:
        """Start the next queued step, preserving single-step execution order."""

        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"cannot dispatch a step for terminal run: {run_id}")

        steps = self.list_steps(run_id)
        if any(step.status is StepStatus.RUNNING for step in steps):
            raise ValueError(f"run already has a running step: {run_id}")

        next_step = next(
            (step for step in steps if step.status is StepStatus.QUEUED), None
        )
        if next_step is None:
            return None
        if run.status is RunStatus.QUEUED:
            run_payload: dict[str, object] = {"objective": run.objective}
            if run.agent_id is not None:
                run_payload["agent_id"] = run.agent_id
            step_payload: dict[str, object] = {
                "run_id": next_step.run_id,
                "position": next_step.position,
                "objective": next_step.objective,
            }
            if next_step.command is not None:
                step_payload["command"] = list(next_step.command)
            if next_step.timeout is not None:
                step_payload["timeout"] = next_step.timeout
            stored = self.store.put_many(
                (
                    ("run", run_id, RunStatus.RUNNING, run_payload),
                    ("step", next_step.step_id, StepStatus.RUNNING, step_payload),
                )
            )
            return self._step(stored[1])
        return self.transition_step(next_step.step_id, StepStatus.RUNNING)

    def execute_next_step(
        self, run_id: str, executor: SandboxExecutor
    ) -> tuple[RunStep, AgentRun] | None:
        """Execute and complete the next queued command through an injected sandbox."""

        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"cannot execute a step for terminal run: {run_id}")

        steps = self.list_steps(run_id)
        if any(step.status is StepStatus.RUNNING for step in steps):
            raise ValueError(f"run already has a running step: {run_id}")
        next_step = next(
            (step for step in steps if step.status is StepStatus.QUEUED), None
        )
        if next_step is None:
            return None
        if next_step.command is None:
            raise ValueError(f"next step does not have a command: {next_step.step_id}")

        running_step = self.start_next_step(run_id)
        if running_step is None:  # Defensive: next_step proved queued above.
            return None
        result = executor.execute(running_step.command, timeout=running_step.timeout)
        return self.complete_step_from_result(running_step.step_id, result)

    def add_step(
        self,
        run_id: str,
        step_id: str,
        *,
        objective: str,
        command: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> RunStep:
        """Append a queued step to a non-terminal run."""

        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"cannot add a step to terminal run: {run_id}")
        if not objective.strip():
            raise ValueError("step objective must not be empty")
        normalized_command = self._validate_command(command, timeout)
        payload: dict[str, object] = {
            "objective": objective,
        }
        if normalized_command is not None:
            payload["command"] = list(normalized_command)
        if timeout is not None:
            payload["timeout"] = timeout
        try:
            record = self.store.append_step(
                step_id,
                run_id,
                status=StepStatus.QUEUED,
                payload=payload,
            )
        except StateConflictError as error:
            raise ValueError(f"step already exists: {step_id}") from error
        return self._step(record)

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
        if current.command is not None:
            payload["command"] = list(current.command)
        if current.timeout is not None:
            payload["timeout"] = current.timeout
        if output is not None:
            payload["output"] = dict(output)
        return self._step(
            self.store.put("step", step_id, status=status, payload=payload)
        )

    def cancel_step(self, step_id: str) -> RunStep:
        """Cancel one queued step without changing its active parent run."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        run = self.get(current.run_id)
        if run is None:
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
            raise ValueError(f"run must be active to cancel a step: {run.run_id}")
        if current.status is not StepStatus.QUEUED:
            raise ValueError(f"step must be queued to cancel it: {step_id}")

        payload: dict[str, object] = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
        }
        if current.command is not None:
            payload["command"] = list(current.command)
        if current.timeout is not None:
            payload["timeout"] = current.timeout
        return self._step(
            self.store.put(
                "step", step_id, status=StepStatus.CANCELLED, payload=payload
            )
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
        step_payload: dict[str, object] = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
            "output": output,
        }
        if current.command is not None:
            step_payload["command"] = list(current.command)
        if current.timeout is not None:
            step_payload["timeout"] = current.timeout

        run_status: RunStatus | None = None
        run_output: dict[str, object] | None = None
        if step_status is StepStatus.FAILED:
            run_status = RunStatus.FAILED
            run_output = {"failed_step_id": step_id, "exit_code": result.returncode}
        elif all(
            candidate.step_id == step_id
            or candidate.status is StepStatus.SUCCEEDED
            for candidate in self.list_steps(run.run_id)
        ):
            run_status = RunStatus.SUCCEEDED
            run_output = {"completed_steps": len(self.list_steps(run.run_id))}

        if run_status is None:
            step = self._step(
                self.store.put("step", step_id, status=step_status, payload=step_payload)
            )
            return step, run

        run_payload: dict[str, object] = {
            "objective": run.objective,
            "output": run_output,
        }
        if run.agent_id is not None:
            run_payload["agent_id"] = run.agent_id
        stored = self.store.put_many(
            (
                ("step", step_id, step_status, step_payload),
                ("run", run.run_id, run_status, run_payload),
            )
        )
        step = self._step(stored[0])
        run = self._run(stored[1])
        return step, run

    def recover_running_step(
        self,
        step_id: str,
        reason: StepRecoveryReason,
        *,
        detail: str | None = None,
    ) -> tuple[RunStep, AgentRun]:
        """Fail a running step whose execution ended without a durable result."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        run = self.get(current.run_id)
        if run is None:  # Defensive: durable step records must reference an existing run.
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status is not RunStatus.RUNNING:
            raise ValueError(f"run must be running to recover a step: {run.run_id}")
        if current.status is not StepStatus.RUNNING:
            raise ValueError(f"step must be running to recover it: {step_id}")
        if not isinstance(reason, StepRecoveryReason):
            raise ValueError("recovery reason must be a StepRecoveryReason")
        if detail is not None and not detail.strip():
            raise ValueError("recovery detail must not be empty")

        output: dict[str, object] = {"recovery_reason": reason.value}
        if detail is not None:
            output["recovery_detail"] = detail
        step_payload: dict[str, object] = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
            "output": output,
        }
        if current.command is not None:
            step_payload["command"] = list(current.command)
        if current.timeout is not None:
            step_payload["timeout"] = current.timeout

        run_payload: dict[str, object] = {
            "objective": run.objective,
            "output": {
                "failed_step_id": step_id,
                "recovery_reason": reason.value,
            },
        }
        if run.agent_id is not None:
            run_payload["agent_id"] = run.agent_id
        stored = self.store.put_many(
            (
                ("step", step_id, StepStatus.FAILED, step_payload),
                ("run", run.run_id, RunStatus.FAILED, run_payload),
            )
        )
        step = self._step(stored[0])
        run = self._run(stored[1])
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
        command = record.payload.get("command")
        timeout = record.payload.get("timeout")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(f"step record has invalid run id: {record.key}")
        if not isinstance(position, int) or isinstance(position, bool) or position < 1:
            raise ValueError(f"step record has invalid position: {record.key}")
        if not isinstance(objective, str):
            raise ValueError(f"step record has invalid objective: {record.key}")
        if output is not None and not isinstance(output, dict):
            raise ValueError(f"step record has invalid output: {record.key}")
        normalized_command = RunCoordinator._validate_command(command, timeout)
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
            command=normalized_command,
            timeout=timeout,
        )

    @staticmethod
    def _validate_command(
        command: Sequence[str] | object | None, timeout: object | None
    ) -> tuple[str, ...] | None:
        if command is None:
            if timeout is not None:
                raise ValueError("step timeout requires a command")
            return None
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise ValueError("step command must be a sequence of arguments")
        normalized = tuple(command)
        if not normalized:
            raise ValueError("step command must not be empty")
        if any(not isinstance(argument, str) or not argument for argument in normalized):
            raise ValueError("step command arguments must be non-empty strings")
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise ValueError("step timeout must be positive")
        return normalized
