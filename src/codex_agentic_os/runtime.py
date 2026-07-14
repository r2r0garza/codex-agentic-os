"""Runtime selection and durable run-lifecycle coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import posixpath
from typing import Callable, Mapping, Protocol, Sequence

from .chat import ChatAdapter, ChatMessage, ChatRequest, ChatResponse
from .sandboxes import SandboxKind

from .state import RunHistoryEntry, StateConflictError, StateRecord, StateStore


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


class ApprovalStatus(StrEnum):
    """Durable operator decision state for an approval-gated step."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequiredError(ValueError):
    """Raised when dispatch reaches a step awaiting operator approval."""


class ContextReferencesUnresolvedError(ValueError):
    """Raised when dispatch reaches a provider step with unresolved context references."""


class PlanProposalError(ValueError):
    """Raised when a provider's plan proposal is malformed or unparseable.

    The malformed proposal and its raw provider evidence are durably recorded
    as an ``invalid`` plan draft before this error is raised, so the failure
    remains operator-inspectable.
    """


class StepRecoveryReason(StrEnum):
    """Explicit reasons for failing a running step with an uncertain result."""

    INTERRUPTED = "interrupted"
    TIMED_OUT = "timed_out"


class StepFailureKind(StrEnum):
    """Operator-visible certainty of a durable failed-step outcome."""

    DEFINITE = "definite"
    UNCERTAIN = "uncertain"


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
class SandboxPolicy:
    """Durable per-step sandbox execution policy without resolved environment values."""

    kind: SandboxKind
    image: str = "python:3.12-slim"
    mounts: tuple[tuple[str, str], ...] = ()
    working_dir: str | None = None
    env_passthrough: tuple[str, ...] = ()
    network_enabled: bool = False


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
    message: ProviderMessage | None = None
    context_step_ids: tuple[str, ...] = ()
    approval_required: bool = False
    approval_status: ApprovalStatus | None = None
    sandbox_policy: SandboxPolicy | None = None

    @property
    def failure_kind(self) -> StepFailureKind | None:
        """Classify known execution failures without changing durable state."""

        if self.status is not StepStatus.FAILED or self.output is None:
            return None
        if "recovery_reason" in self.output:
            return StepFailureKind.UNCERTAIN
        if self.approval_status is ApprovalStatus.REJECTED:
            return None
        exit_code = self.output.get("exit_code")
        if (
            self.command is not None
            and isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and exit_code != 0
        ):
            return StepFailureKind.DEFINITE
        if (
            self.message is not None
            and isinstance(self.output.get("error"), str)
            and isinstance(self.output.get("error_type"), str)
        ):
            return StepFailureKind.DEFINITE
        return None

    @property
    def retry_eligible(self) -> bool | None:
        """Report explicit-retry eligibility for failed steps only."""

        if self.status is not StepStatus.FAILED:
            return None
        return self.failure_kind is StepFailureKind.DEFINITE


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    """Provider-neutral input for one durable model-backed step."""

    provider: str
    content: str
    model: str | None = None
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class PlanStepProposal:
    """One model-proposed ordered step within a durable plan draft.

    Carries the same executable materialization fields ``add_step`` requires
    (command argv plus persisted sandbox policy, or a complete provider
    message) so an accept decision can pass a step directly to the existing
    queued-step creation path without guessing or synthesizing execution
    details. ``step_id`` is a deterministic, collision-free identity derived
    from the plan id and step position, not proposed by the model.
    """

    step_id: str
    objective: str
    execution_kind: str
    command: tuple[str, ...] | None = None
    timeout: float | None = None
    sandbox_policy: SandboxPolicy | None = None
    message: ProviderMessage | None = None


@dataclass(frozen=True, slots=True)
class PlanDraft:
    """Typed view of a durable model-proposed plan draft attached to a run."""

    plan_id: str
    run_id: str
    status: str
    revision: int
    steps: tuple[PlanStepProposal, ...] = ()
    evidence: Mapping[str, object] | None = None
    error: str | None = None
    decision_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class Agent:
    """Typed view of a durable agent registry record."""

    agent_id: str
    label: str | None
    revision: int
    last_seen: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimStaleness:
    """Deterministic, read-only evaluation of a claimed run's owner staleness."""

    run_id: str
    agent_id: str
    last_seen: str
    threshold_seconds: float
    evaluated_at: str
    stale: bool


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


class SandboxPolicyResolver(Protocol):
    """Build an executor from one persisted sandbox policy at dispatch time."""

    def __call__(self, policy: SandboxPolicy) -> SandboxExecutor: ...


class ChatAdapterResolver(Protocol):
    """Resolve the configured adapter for one persisted provider message."""

    def __call__(self, message: ProviderMessage) -> ChatAdapter: ...


PLAN_PROPOSAL_SYSTEM_PROMPT = (
    "Decompose the operator objective into an ordered list of durable "
    "execution steps. Respond with a single JSON object of exactly this "
    'shape and no other text: {"steps": [<step>, ...]}. Propose at least '
    "one step. Each <step> is a command step or a provider step. A command "
    'step is {"objective": "<step objective>", "execution_kind": "command", '
    '"command": ["<argv0>", "<argv1>", ...], "sandbox_policy": {"kind": '
    '"docker" or "podman", "image": "<optional image, default '
    'python:3.12-slim>", "mounts": [["<host path>", "<container path>"], '
    '...], "working_dir": "<optional absolute path>", "env_passthrough": '
    '["<ENV_VAR_NAME>", ...], "network_enabled": <optional boolean, default '
    'false>}, "timeout": <optional positive number of seconds>} and must not '
    'include "message". A provider step is {"objective": "<step '
    'objective>", "execution_kind": "provider", "message": {"provider": '
    '"<provider name>", "content": "<message content>", "model": "<optional '
    'model>", "system": "<optional system prompt>", "temperature": <optional '
    'number>, "max_tokens": <optional positive integer>}} and must not '
    'include "command", "timeout", or "sandbox_policy".'
)


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

    def __init__(
        self,
        store: StateStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def create(
        self, run_id: str, *, objective: str, agent_id: str | None = None
    ) -> AgentRun:
        """Create a queued run, rejecting duplicate identifiers."""

        if not objective.strip():
            raise ValueError("run objective must not be empty")
        if agent_id is not None and not agent_id.strip():
            raise ValueError("agent id must not be empty")
        if agent_id is not None:
            self._require_registered_agent(agent_id)
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
        self._require_registered_agent(agent_id)
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
        self._require_registered_agent(agent_id)
        record = self.store.claim_next_run(agent_id)
        return None if record is None else self._run(record)

    def evaluate_claim_staleness(
        self, run_id: str, *, threshold_seconds: float
    ) -> ClaimStaleness:
        """Report whether a claimed run's owning agent is stale, without mutation.

        Staleness compares the owning agent's durable ``last_seen`` heartbeat
        against the coordinator's injected clock. A gap strictly greater than
        ``threshold_seconds`` is stale; a gap equal to or under it is fresh.
        """

        if (
            not isinstance(threshold_seconds, (int, float))
            or isinstance(threshold_seconds, bool)
            or threshold_seconds <= 0
        ):
            raise ValueError("staleness threshold must be a positive number of seconds")
        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.agent_id is None:
            raise ValueError(f"run is not claimed: {run_id}")
        agent_record = self.store.get("agent", run.agent_id)
        if agent_record is None:
            raise ValueError(f"agent is not registered: {run.agent_id}")
        last_seen = agent_record.payload.get("last_seen")
        if last_seen is None:
            raise ValueError(f"agent has no recorded heartbeat: {run.agent_id}")
        if not isinstance(last_seen, str):
            raise ValueError(f"agent record has invalid last_seen: {run.agent_id}")
        last_seen_at = self._parse_last_seen(last_seen)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("run coordinator clock must return a timezone-aware datetime")
        evaluated_at = now.astimezone(timezone.utc)
        elapsed_seconds = (evaluated_at - last_seen_at).total_seconds()
        return ClaimStaleness(
            run_id=run_id,
            agent_id=run.agent_id,
            last_seen=last_seen,
            threshold_seconds=threshold_seconds,
            evaluated_at=evaluated_at.isoformat(),
            stale=elapsed_seconds > threshold_seconds,
        )

    def reassign_stale_claim(
        self,
        run_id: str,
        replacement_agent_id: str,
        *,
        expected_agent_id: str,
        expected_revision: int,
        threshold_seconds: float,
    ) -> AgentRun:
        """Atomically transfer a demonstrably stale claim to a replacement agent."""

        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("coordinator clock must include an unambiguous timezone")
        try:
            stored = self.store.reassign_stale_run_claim(
                run_id,
                expected_agent_id=expected_agent_id,
                expected_revision=expected_revision,
                replacement_agent_id=replacement_agent_id,
                threshold_seconds=threshold_seconds,
                evaluated_at=now,
            )
        except StateConflictError as error:
            raise ValueError(f"run claim cannot be reassigned: {run_id}") from error
        return self._run(stored)

    def retry_step(
        self,
        step_id: str,
        new_step_id: str,
        *,
        expected_step_revision: int,
        expected_run_revision: int,
    ) -> tuple[RunStep, AgentRun]:
        """Atomically requeue one retry-eligible failed step as a new attempt."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        if current.status is not StepStatus.FAILED or current.retry_eligible is not True:
            raise ValueError(f"step is not retry-eligible: {step_id}")
        try:
            _, new_step_record, run_record = self.store.retry_failed_step(
                step_id,
                new_step_id,
                expected_step_revision=expected_step_revision,
                expected_run_revision=expected_run_revision,
            )
        except StateConflictError as error:
            raise ValueError(f"step retry conflict: {step_id}") from error
        return self._step(new_step_record), self._run(run_record)

    @staticmethod
    def _parse_last_seen(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"agent record has invalid last_seen: {value}") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"agent record has an ambiguous last_seen: {value}")
        return parsed.astimezone(timezone.utc)

    def _require_registered_agent(self, agent_id: str) -> None:
        if self.store.get("agent", agent_id) is None:
            raise ValueError(f"agent is not registered: {agent_id}")

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
        execution_kind: str | None = None,
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
        try:
            record = self.store.transition_run(
                run_id,
                expected_status=current.status,
                expected_revision=current.revision,
                status=status,
                payload=payload,
                execution_kind=execution_kind,
            )
        except StateConflictError as error:
            raise ValueError(f"run transition conflict: {run_id}") from error
        except KeyError as error:
            raise KeyError(f"run does not exist: {run_id}") from error
        return self._run(record)

    def list_history(self, run_id: str) -> tuple[RunHistoryEntry, ...]:
        """Return one run's durable lifecycle history in order."""

        if self.get(run_id) is None:
            raise KeyError(f"run does not exist: {run_id}")
        return self.store.list_run_history(run_id)

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
            if step.message is not None:
                payload["message"] = self._message_payload(step.message)
            if step.sandbox_policy is not None:
                payload["sandbox_policy"] = self._sandbox_policy_payload(step.sandbox_policy)
            self._add_context_step_ids_payload(payload, step)
            self._add_approval_payload(payload, step)
            records.append(("step", step.step_id, StepStatus.CANCELLED, payload))

        run_payload: dict[str, object] = {"objective": current.objective}
        if current.agent_id is not None:
            run_payload["agent_id"] = current.agent_id
        records.append(("run", run_id, RunStatus.CANCELLED, run_payload))
        stored = self.store.put_many(
            records,
            expected=(
                *(("step", step.step_id, step.status, step.revision) for step in active_steps),
                ("run", run_id, current.status, current.revision),
            ),
            history=(
                *(
                    RunHistoryEntry(
                        run_id, 0, "step_cancelled", StepStatus.CANCELLED,
                        step_id=step.step_id, agent_id=current.agent_id,
                        execution_kind=self._execution_kind(step),
                    )
                    for step in active_steps
                ),
                RunHistoryEntry(
                    run_id, 0, "run_cancelled", RunStatus.CANCELLED,
                    agent_id=current.agent_id,
                ),
            ),
        )
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
        if next_step.approval_status is ApprovalStatus.PENDING:
            raise ApprovalRequiredError(
                f"step requires approval before dispatch: {next_step.step_id}"
            )
        if next_step.context_step_ids:
            step_by_id = {step.step_id: step for step in steps}
            unresolved = tuple(
                context_step_id
                for context_step_id in next_step.context_step_ids
                if step_by_id[context_step_id].status is not StepStatus.SUCCEEDED
            )
            if unresolved:
                raise ContextReferencesUnresolvedError(
                    "step has unresolved context references: "
                    f"{next_step.step_id} ({', '.join(unresolved)})"
                )
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
            if next_step.message is not None:
                step_payload["message"] = self._message_payload(next_step.message)
            if next_step.sandbox_policy is not None:
                step_payload["sandbox_policy"] = self._sandbox_policy_payload(
                    next_step.sandbox_policy
                )
            self._add_context_step_ids_payload(step_payload, next_step)
            self._add_approval_payload(step_payload, next_step)
            stored = self.store.put_many(
                (
                    ("run", run_id, RunStatus.RUNNING, run_payload),
                    ("step", next_step.step_id, StepStatus.RUNNING, step_payload),
                ),
                expected=(
                    ("run", run_id, run.status, run.revision),
                    ("step", next_step.step_id, next_step.status, next_step.revision),
                ),
                history=(
                    RunHistoryEntry(
                        run_id, 0, "run_started", RunStatus.RUNNING,
                        agent_id=run.agent_id,
                    ),
                    RunHistoryEntry(
                        run_id, 0, "step_started", StepStatus.RUNNING,
                        step_id=next_step.step_id, agent_id=run.agent_id,
                        execution_kind=self._execution_kind(next_step),
                        context_step_ids=next_step.context_step_ids or None,
                    ),
                ),
            )
            return self._step(stored[1])
        return self.transition_step(
            next_step.step_id,
            StepStatus.RUNNING,
            resolved_context_step_ids=next_step.context_step_ids or None,
        )

    def execute_next_step(
        self,
        run_id: str,
        executor: SandboxExecutor | None = None,
        *,
        sandbox_resolver: SandboxPolicyResolver | None = None,
        adapter_resolver: ChatAdapterResolver | None = None,
    ) -> tuple[RunStep, AgentRun] | None:
        """Execute and complete the next queued command or provider message."""

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
        resolved_executor = executor
        if next_step.command is not None:
            if next_step.sandbox_policy is not None:
                if executor is not None:
                    raise ValueError(
                        "persisted sandbox policy conflicts with an injected executor"
                    )
                if sandbox_resolver is None:
                    raise ValueError(
                        "next command step requires its persisted sandbox policy: "
                        f"{next_step.step_id}"
                    )
                if next_step.approval_status is ApprovalStatus.PENDING:
                    raise ApprovalRequiredError(
                        "step requires approval before dispatch: "
                        f"{next_step.step_id}"
                    )
                resolved_executor = sandbox_resolver(next_step.sandbox_policy)
            elif executor is None:
                raise ValueError(
                    f"next command step requires a sandbox: {next_step.step_id}"
                )
        elif next_step.message is not None:
            if adapter_resolver is None:
                raise ValueError(
                    f"next provider-message step requires an adapter: {next_step.step_id}"
                )
        else:  # Defensive: durable step validation rejects missing execution input.
            raise ValueError(f"next step has no execution input: {next_step.step_id}")

        running_step = self.start_next_step(run_id)
        if running_step is None:  # Defensive: next_step proved queued above.
            return None
        if running_step.command is not None:
            assert resolved_executor is not None
            result = resolved_executor.execute(
                running_step.command, timeout=running_step.timeout
            )
            return self.complete_step_from_result(running_step.step_id, result)

        assert adapter_resolver is not None and running_step.message is not None
        try:
            adapter = adapter_resolver(running_step.message)
            system_messages = (
                (ChatMessage("system", running_step.message.system),)
                if running_step.message.system is not None
                else ()
            )
            context_messages = self._resolve_context_messages(running_step)
            messages = (
                system_messages
                + context_messages
                + (ChatMessage("user", running_step.message.content),)
            )
            request = ChatRequest(
                messages,
                temperature=running_step.message.temperature,
                max_tokens=running_step.message.max_tokens,
            )
            response = adapter.complete(request)
        except (ValueError, RuntimeError, NotImplementedError) as error:
            return self.fail_step_from_error(running_step.step_id, error)
        return self.complete_step_from_chat_response(running_step.step_id, response)

    def add_step(
        self,
        run_id: str,
        step_id: str,
        *,
        objective: str,
        command: Sequence[str] | None = None,
        timeout: float | None = None,
        message: ProviderMessage | Mapping[str, object] | None = None,
        context_step_ids: Sequence[str] | None = None,
        approval_required: bool = False,
        sandbox_policy: SandboxPolicy | Mapping[str, object] | None = None,
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
        normalized_message = self._validate_message(message)
        if (normalized_command is None) == (normalized_message is None):
            raise ValueError("step requires exactly one of command or provider message")
        normalized_context_step_ids = self._validate_context_step_ids(
            run_id,
            context_step_ids,
            has_message=normalized_message is not None,
        )
        if not isinstance(approval_required, bool):
            raise ValueError("approval_required must be a boolean")
        normalized_sandbox_policy = self._validate_sandbox_policy(
            sandbox_policy, has_command=normalized_command is not None
        )
        payload: dict[str, object] = {
            "objective": objective,
            "approval_required": approval_required,
        }
        if approval_required:
            payload["approval_status"] = ApprovalStatus.PENDING
        if normalized_command is not None:
            payload["command"] = list(normalized_command)
        if timeout is not None:
            payload["timeout"] = timeout
        if normalized_message is not None:
            payload["message"] = self._message_payload(normalized_message)
        if normalized_context_step_ids:
            payload["context_step_ids"] = list(normalized_context_step_ids)
        if normalized_sandbox_policy is not None:
            payload["sandbox_policy"] = self._sandbox_policy_payload(normalized_sandbox_policy)
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

    def propose_plan(
        self,
        run_id: str,
        plan_id: str,
        *,
        adapter_resolver: ChatAdapterResolver,
        provider: str,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        objective: str | None = None,
    ) -> PlanDraft:
        """Dispatch a run's objective through a provider adapter and persist a durable draft.

        Queues no steps. A successful, well-formed proposal is persisted as a
        ``draft`` plan awaiting an explicit operator acceptance decision. A
        malformed or unparseable proposal is instead persisted as an
        ``invalid`` plan carrying the raw provider evidence, and
        ``PlanProposalError`` is raised naming the recorded draft.
        """

        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"cannot propose a plan for terminal run: {run_id}")
        if not provider.strip():
            raise ValueError("plan provider must be a non-empty string")
        if self.store.get("plan", plan_id) is not None:
            raise ValueError(f"plan already exists: {plan_id}")
        planning_objective = run.objective if objective is None else objective
        if not planning_objective.strip():
            raise ValueError("plan objective must not be empty")

        message = ProviderMessage(
            provider=provider,
            content=planning_objective,
            model=model,
            system=PLAN_PROPOSAL_SYSTEM_PROMPT,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        adapter = adapter_resolver(message)
        request = ChatRequest(
            (ChatMessage("system", message.system), ChatMessage("user", message.content)),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        response = adapter.complete(request)

        evidence: dict[str, object] = {
            "provider": provider,
            "requested_model": model,
            "response_model": response.model,
            "content": response.content,
        }
        if response.raw is not None:
            evidence["raw"] = dict(response.raw)

        try:
            steps = self._parse_plan_proposal(response.content, plan_id)
        except ValueError as error:
            payload: dict[str, object] = {
                "run_id": run_id,
                "objective": planning_objective,
                "evidence": evidence,
                "error": str(error),
            }
            try:
                self.store.insert("plan", plan_id, status="invalid", payload=payload)
            except StateConflictError as conflict:
                raise ValueError(f"plan already exists: {plan_id}") from conflict
            raise PlanProposalError(
                f"plan proposal is malformed and was recorded as plan/{plan_id} "
                f"(invalid): {error}"
            ) from error

        payload = {
            "run_id": run_id,
            "objective": planning_objective,
            "steps": [self._plan_step_proposal_payload(step) for step in steps],
            "evidence": evidence,
        }
        try:
            record = self.store.insert("plan", plan_id, status="draft", payload=payload)
        except StateConflictError as error:
            raise ValueError(f"plan already exists: {plan_id}") from error
        return self._plan_draft(record)

    def get_plan(self, plan_id: str) -> PlanDraft | None:
        """Return a durable plan draft when it exists, read-only.

        Covers both a reviewable ``draft`` and an ``invalid`` malformed
        proposal recorded by :meth:`propose_plan`; distinguish them via the
        returned draft's ``status``/``error``. Never mutates state.
        """

        record = self.store.get("plan", plan_id)
        return None if record is None else self._plan_draft(record)

    def accept_plan(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        agent_id: str | None = None,
    ) -> tuple[PlanDraft, tuple[RunStep, ...]]:
        """Atomically accept one draft and materialize all proposed steps."""

        record, draft, run = self._reviewable_plan_decision(
            plan_id, expected_revision=expected_revision, agent_id=agent_id
        )
        step_records = tuple(
            (
                step.step_id,
                StepStatus.QUEUED,
                self._materialized_plan_step_payload(step),
            )
            for step in draft.steps
        )
        payload = dict(record.payload)
        if agent_id is not None:
            payload["decision_agent_id"] = agent_id
        try:
            stored_plan, stored_steps = self.store.decide_plan(
                plan_id,
                run.run_id,
                status="accepted",
                payload=payload,
                expected_plan_status="draft",
                expected_plan_revision=expected_revision,
                expected_run_status=run.status,
                expected_run_revision=run.revision,
                steps=step_records,
                history=(
                    RunHistoryEntry(
                        run.run_id,
                        0,
                        "plan_accepted",
                        "accepted",
                        agent_id=agent_id,
                        plan_id=plan_id,
                    ),
                ),
            )
        except StateConflictError as error:
            if "state record already exists: step/" in str(error):
                step_id = str(error).rsplit("/", 1)[-1]
                raise ValueError(f"plan step already exists: {step_id}") from error
            raise ValueError(f"plan acceptance conflict: {plan_id}") from error
        return self._plan_draft(stored_plan), tuple(
            self._step(step) for step in stored_steps
        )

    def reject_plan(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        agent_id: str | None = None,
    ) -> PlanDraft:
        """Atomically reject one draft without materializing any steps."""

        record, _, run = self._reviewable_plan_decision(
            plan_id, expected_revision=expected_revision, agent_id=agent_id
        )
        payload = dict(record.payload)
        if agent_id is not None:
            payload["decision_agent_id"] = agent_id
        try:
            stored_plan, stored_steps = self.store.decide_plan(
                plan_id,
                run.run_id,
                status="rejected",
                payload=payload,
                expected_plan_status="draft",
                expected_plan_revision=expected_revision,
                expected_run_status=run.status,
                expected_run_revision=run.revision,
                history=(
                    RunHistoryEntry(
                        run.run_id,
                        0,
                        "plan_rejected",
                        "rejected",
                        agent_id=agent_id,
                        plan_id=plan_id,
                    ),
                ),
            )
        except StateConflictError as error:
            raise ValueError(f"plan rejection conflict: {plan_id}") from error
        assert stored_steps == ()
        return self._plan_draft(stored_plan)

    def get_step(self, step_id: str) -> RunStep | None:
        """Return a step when it exists."""

        record = self.store.get("step", step_id)
        return None if record is None else self._step(record)

    def _reviewable_plan_decision(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        agent_id: str | None,
    ) -> tuple[StateRecord, PlanDraft, AgentRun]:
        if expected_revision <= 0:
            raise ValueError("expected plan revision must be positive")
        record = self.store.get("plan", plan_id)
        if record is None:
            raise KeyError(f"plan does not exist: {plan_id}")
        if record.status != "draft":
            raise ValueError(f"plan is not a reviewable draft: {plan_id}")
        if record.revision != expected_revision:
            raise ValueError(f"plan decision conflict: {plan_id}")
        draft = self._plan_draft(record)
        run = self.get(draft.run_id)
        if run is None:
            raise KeyError(f"run does not exist: {draft.run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"run must be active to decide a plan: {run.run_id}")
        if agent_id is not None:
            self._require_registered_agent(agent_id)
        return record, draft, run

    @staticmethod
    def _materialized_plan_step_payload(step: PlanStepProposal) -> dict[str, object]:
        payload: dict[str, object] = {
            "objective": step.objective,
            "approval_required": False,
        }
        if step.command is not None:
            payload["command"] = list(step.command)
        if step.timeout is not None:
            payload["timeout"] = step.timeout
        if step.message is not None:
            payload["message"] = RunCoordinator._message_payload(step.message)
        if step.sandbox_policy is not None:
            payload["sandbox_policy"] = RunCoordinator._sandbox_policy_payload(
                step.sandbox_policy
            )
        return payload

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
        resolved_context_step_ids: Sequence[str] | None = None,
    ) -> RunStep:
        """Advance a step through an allowed lifecycle edge."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        if status not in self._STEP_TRANSITIONS[current.status]:
            raise ValueError(f"invalid step transition: {current.status} -> {status}")
        if output is not None and status not in {StepStatus.SUCCEEDED, StepStatus.FAILED}:
            raise ValueError("step output is only valid for succeeded or failed steps")
        if resolved_context_step_ids is not None and status is not StepStatus.RUNNING:
            raise ValueError(
                "resolved context step ids are only valid when starting a step"
            )
        payload: dict[str, object] = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
        }
        if current.command is not None:
            payload["command"] = list(current.command)
        if current.timeout is not None:
            payload["timeout"] = current.timeout
        if current.message is not None:
            payload["message"] = self._message_payload(current.message)
        self._add_context_step_ids_payload(payload, current)
        self._add_approval_payload(payload, current)
        if output is not None:
            payload["output"] = dict(output)
        run = self.get(current.run_id)
        if run is None:
            raise KeyError(f"run does not exist: {current.run_id}")
        try:
            record = self.store.transition_step(
                step_id,
                expected_status=current.status,
                expected_revision=current.revision,
                status=status,
                payload=payload,
                run_id=current.run_id,
                agent_id=run.agent_id,
                execution_kind=self._execution_kind(current),
                context_step_ids=resolved_context_step_ids,
            )
        except StateConflictError as error:
            raise ValueError(f"step transition conflict: {step_id}") from error
        return self._step(record)

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

        return self.transition_step(step_id, StepStatus.CANCELLED)

    def approve_step(self, step_id: str, *, agent_id: str | None = None) -> RunStep:
        """Approve a pending step so a subsequent dispatch executes it normally."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        if current.approval_status is not ApprovalStatus.PENDING:
            raise ValueError(f"step is not pending approval: {step_id}")
        if agent_id is not None:
            self._require_registered_agent(agent_id)

        payload = self._decision_payload(current, ApprovalStatus.APPROVED)
        try:
            stored = self.store.put_many(
                (("step", step_id, current.status, payload),),
                expected=(("step", step_id, current.status, current.revision),),
                history=(
                    RunHistoryEntry(
                        current.run_id, 0, "step_approved", current.status,
                        step_id=step_id, agent_id=agent_id,
                        execution_kind=self._execution_kind(current),
                    ),
                ),
            )
        except StateConflictError as error:
            raise ValueError(f"step approval conflict: {step_id}") from error
        return self._step(stored[0])

    def reject_step(
        self, step_id: str, *, agent_id: str | None = None
    ) -> tuple[RunStep, AgentRun]:
        """Reject a pending step, producing a terminal outcome without executing it."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        if current.approval_status is not ApprovalStatus.PENDING:
            raise ValueError(f"step is not pending approval: {step_id}")
        run = self.get(current.run_id)
        if run is None:  # Defensive: durable step records must reference an existing run.
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"run must be active to reject a step: {run.run_id}")
        if agent_id is not None:
            self._require_registered_agent(agent_id)

        output: dict[str, object] = {
            "error": "step rejected by operator",
            "error_type": "ApprovalRejectedError",
        }
        step_payload = self._decision_payload(current, ApprovalStatus.REJECTED)
        step_payload["output"] = output
        run_payload: dict[str, object] = {
            "objective": run.objective,
            "output": {"failed_step_id": step_id, "error": output["error"]},
        }
        if run.agent_id is not None:
            run_payload["agent_id"] = run.agent_id

        try:
            stored = self.store.put_many(
                (
                    ("step", step_id, StepStatus.FAILED, step_payload),
                    ("run", run.run_id, RunStatus.FAILED, run_payload),
                ),
                expected=(
                    ("step", step_id, current.status, current.revision),
                    ("run", run.run_id, run.status, run.revision),
                ),
                history=(
                    RunHistoryEntry(
                        run.run_id, 0, "step_rejected", StepStatus.FAILED,
                        step_id=step_id, agent_id=agent_id,
                        execution_kind=self._execution_kind(current),
                    ),
                    RunHistoryEntry(
                        run.run_id, 0, "run_failed", RunStatus.FAILED,
                        agent_id=agent_id,
                        execution_kind=self._execution_kind(current),
                    ),
                ),
            )
        except StateConflictError as error:
            raise ValueError(f"step rejection conflict: {step_id}") from error
        return self._step(stored[0]), self._run(stored[1])

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

        result_command = list(result.command)
        if current.sandbox_policy is not None:
            passthrough_names = frozenset(current.sandbox_policy.env_passthrough)
            for index, argument in enumerate(result_command):
                if index == 0 or result_command[index - 1] != "--env":
                    continue
                name, separator, _ = argument.partition("=")
                if separator and name in passthrough_names:
                    result_command[index] = name

        output: dict[str, object] = {
            "command": result_command,
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
        if current.message is not None:
            step_payload["message"] = self._message_payload(current.message)
        if current.sandbox_policy is not None:
            step_payload["sandbox_policy"] = self._sandbox_policy_payload(current.sandbox_policy)
        self._add_context_step_ids_payload(step_payload, current)
        self._add_approval_payload(step_payload, current)

        run_status: RunStatus | None = None
        run_output: dict[str, object] | None = None
        if step_status is StepStatus.FAILED:
            run_status = RunStatus.FAILED
            run_output = {"failed_step_id": step_id, "exit_code": result.returncode}
        else:
            superseded_step_ids = self._superseded_step_ids(run.run_id)
            if all(
                candidate.step_id == step_id
                or candidate.status is StepStatus.SUCCEEDED
                or candidate.step_id in superseded_step_ids
                for candidate in self.list_steps(run.run_id)
            ):
                run_status = RunStatus.SUCCEEDED
                run_output = {"completed_steps": len(self.list_steps(run.run_id))}

        if run_status is None:
            step = self.transition_step(step_id, step_status, output=output)
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
            ),
            expected=(
                ("step", step_id, current.status, current.revision),
                ("run", run.run_id, run.status, run.revision),
            ),
            history=(
                RunHistoryEntry(
                    run.run_id, 0, f"step_{step_status}", step_status,
                    step_id=step_id, agent_id=run.agent_id, execution_kind="command",
                ),
                RunHistoryEntry(
                    run.run_id, 0, f"run_{run_status}", run_status,
                    agent_id=run.agent_id, execution_kind="command",
                ),
            ),
        )
        step = self._step(stored[0])
        run = self._run(stored[1])
        return step, run

    def complete_step_from_chat_response(
        self, step_id: str, response: ChatResponse
    ) -> tuple[RunStep, AgentRun]:
        """Persist a normalized adapter response and complete a model-backed step."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        if current.message is None:
            raise ValueError(f"step does not have a provider message: {step_id}")
        output: dict[str, object] = {"content": response.content, "model": response.model}
        if response.raw is not None:
            output["raw"] = dict(response.raw)
        output["usage"] = {
            "available": response.usage.available,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "raw": None if response.usage.raw is None else dict(response.usage.raw),
            "unavailable_reason": response.usage.unavailable_reason,
        }
        run = self.get(current.run_id)
        if run is None:
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status is not RunStatus.RUNNING or current.status is not StepStatus.RUNNING:
            raise ValueError(f"step and run must be running to record a response: {step_id}")
        step_payload = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
            "message": self._message_payload(current.message),
            "output": output,
        }
        self._add_context_step_ids_payload(step_payload, current)
        self._add_approval_payload(step_payload, current)
        superseded_step_ids = self._superseded_step_ids(run.run_id)
        final = all(
            candidate.step_id == step_id
            or candidate.status is StepStatus.SUCCEEDED
            or candidate.step_id in superseded_step_ids
            for candidate in self.list_steps(run.run_id)
        )
        if not final:
            step = self.transition_step(step_id, StepStatus.SUCCEEDED, output=output)
            return step, run
        run_payload: dict[str, object] = {
            "objective": run.objective,
            "output": {"completed_steps": len(self.list_steps(run.run_id))},
        }
        if run.agent_id is not None:
            run_payload["agent_id"] = run.agent_id
        stored = self.store.put_many(
            (
                ("step", step_id, StepStatus.SUCCEEDED, step_payload),
                ("run", run.run_id, RunStatus.SUCCEEDED, run_payload),
            ),
            expected=(
                ("step", step_id, current.status, current.revision),
                ("run", run.run_id, run.status, run.revision),
            ),
            history=(
                RunHistoryEntry(
                    run.run_id, 0, "step_succeeded", StepStatus.SUCCEEDED,
                    step_id=step_id, agent_id=run.agent_id, execution_kind="provider",
                ),
                RunHistoryEntry(
                    run.run_id, 0, "run_succeeded", RunStatus.SUCCEEDED,
                    agent_id=run.agent_id, execution_kind="provider",
                ),
            ),
        )
        return self._step(stored[0]), self._run(stored[1])

    def fail_step_from_error(
        self, step_id: str, error: Exception
    ) -> tuple[RunStep, AgentRun]:
        """Fail a running provider-message step on an adapter error, without orphaning it."""

        current = self.get_step(step_id)
        if current is None:
            raise KeyError(f"step does not exist: {step_id}")
        run = self.get(current.run_id)
        if run is None:  # Defensive: durable step records must reference an existing run.
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status is not RunStatus.RUNNING:
            raise ValueError(f"run must be running to fail a step: {run.run_id}")
        if current.status is not StepStatus.RUNNING:
            raise ValueError(f"step must be running to record a failure: {step_id}")

        output: dict[str, object] = {
            "error": str(error),
            "error_type": type(error).__name__,
        }
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
        if current.message is not None:
            step_payload["message"] = self._message_payload(current.message)
        if current.sandbox_policy is not None:
            step_payload["sandbox_policy"] = self._sandbox_policy_payload(current.sandbox_policy)
        self._add_context_step_ids_payload(step_payload, current)
        self._add_approval_payload(step_payload, current)

        run_payload: dict[str, object] = {
            "objective": run.objective,
            "output": {"failed_step_id": step_id, "error": str(error)},
        }
        if run.agent_id is not None:
            run_payload["agent_id"] = run.agent_id
        stored = self.store.put_many(
            (
                ("step", step_id, StepStatus.FAILED, step_payload),
                ("run", run.run_id, RunStatus.FAILED, run_payload),
            ),
            expected=(
                ("step", step_id, current.status, current.revision),
                ("run", run.run_id, run.status, run.revision),
            ),
            history=(
                RunHistoryEntry(
                    run.run_id, 0, "step_failed", StepStatus.FAILED,
                    step_id=step_id, agent_id=run.agent_id, execution_kind="provider",
                ),
                RunHistoryEntry(
                    run.run_id, 0, "run_failed", RunStatus.FAILED,
                    agent_id=run.agent_id, execution_kind="provider",
                ),
            ),
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
        if current.message is not None:
            step_payload["message"] = self._message_payload(current.message)
        if current.sandbox_policy is not None:
            step_payload["sandbox_policy"] = self._sandbox_policy_payload(current.sandbox_policy)
        self._add_context_step_ids_payload(step_payload, current)
        self._add_approval_payload(step_payload, current)

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
            ),
            expected=(
                ("step", step_id, current.status, current.revision),
                ("run", run.run_id, run.status, run.revision),
            ),
            history=(
                RunHistoryEntry(
                    run.run_id, 0, "step_recovered", StepStatus.FAILED,
                    step_id=step_id, agent_id=run.agent_id,
                    execution_kind=self._execution_kind(current),
                ),
                RunHistoryEntry(
                    run.run_id, 0, "run_failed", RunStatus.FAILED,
                    agent_id=run.agent_id,
                    execution_kind=self._execution_kind(current),
                ),
            ),
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
        message = record.payload.get("message")
        context_step_ids = record.payload.get("context_step_ids")
        sandbox_policy = record.payload.get("sandbox_policy")
        approval_required = record.payload.get("approval_required", False)
        approval_status_value = record.payload.get("approval_status")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(f"step record has invalid run id: {record.key}")
        if not isinstance(position, int) or isinstance(position, bool) or position < 1:
            raise ValueError(f"step record has invalid position: {record.key}")
        if not isinstance(objective, str):
            raise ValueError(f"step record has invalid objective: {record.key}")
        if output is not None and not isinstance(output, dict):
            raise ValueError(f"step record has invalid output: {record.key}")
        normalized_command = RunCoordinator._validate_command(command, timeout)
        normalized_message = RunCoordinator._validate_message(message)
        normalized_context_step_ids = RunCoordinator._validate_stored_context_step_ids(
            context_step_ids, has_message=normalized_message is not None
        )
        if normalized_command is not None and normalized_message is not None:
            raise ValueError(f"step record has ambiguous execution input: {record.key}")
        normalized_sandbox_policy = RunCoordinator._validate_sandbox_policy(
            sandbox_policy, has_command=normalized_command is not None
        )
        if not isinstance(approval_required, bool):
            raise ValueError(f"step record has invalid approval requirement: {record.key}")
        if approval_status_value is None:
            approval_status = None
        else:
            try:
                approval_status = ApprovalStatus(approval_status_value)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"step record has invalid approval status: {record.key}"
                ) from error
        if approval_required != (approval_status is not None):
            raise ValueError(f"step record has inconsistent approval state: {record.key}")
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
            message=normalized_message,
            context_step_ids=normalized_context_step_ids,
            approval_required=approval_required,
            approval_status=approval_status,
            sandbox_policy=normalized_sandbox_policy,
        )

    @staticmethod
    def _add_approval_payload(payload: dict[str, object], step: RunStep) -> None:
        """Preserve approval metadata across durable lifecycle rewrites."""

        payload["approval_required"] = step.approval_required
        if step.approval_status is not None:
            payload["approval_status"] = step.approval_status

    @staticmethod
    def _add_context_step_ids_payload(
        payload: dict[str, object], step: RunStep
    ) -> None:
        """Preserve declared context references without resolving their outputs."""

        if step.context_step_ids:
            payload["context_step_ids"] = list(step.context_step_ids)

    def _resolve_context_messages(self, step: RunStep) -> tuple[ChatMessage, ...]:
        """Map a step's resolved context references into provider-neutral turns.

        Each reference replays as one alternating (user, assistant) pair so
        every supported adapter family, including Anthropic's strict
        user/assistant alternation, receives a valid ordered sequence ending
        immediately before the step's own current user message.
        """

        messages: list[ChatMessage] = []
        for context_step_id in step.context_step_ids:
            referenced = self.get_step(context_step_id)
            if referenced is None:  # Defensive: dispatch-time gate already resolved this.
                raise ValueError(f"context step does not exist: {context_step_id}")
            messages.append(ChatMessage("user", referenced.objective))
            messages.append(
                ChatMessage("assistant", self._step_output_text(referenced))
            )
        return tuple(messages)

    @staticmethod
    def _step_output_text(step: RunStep) -> str:
        """Render a succeeded step's durable output as prior-turn text."""

        output = step.output or {}
        if step.message is not None:
            content = output.get("content")
            return content if isinstance(content, str) else ""
        return (
            f"exit_code={output.get('exit_code')}\n"
            f"stdout:\n{output.get('stdout', '')}\n"
            f"stderr:\n{output.get('stderr', '')}"
        )

    @staticmethod
    def _decision_payload(
        step: RunStep, approval_status: ApprovalStatus
    ) -> dict[str, object]:
        """Build a fresh step payload recording an operator approval decision."""

        payload: dict[str, object] = {
            "run_id": step.run_id,
            "position": step.position,
            "objective": step.objective,
        }
        if step.command is not None:
            payload["command"] = list(step.command)
        if step.timeout is not None:
            payload["timeout"] = step.timeout
        if step.message is not None:
            payload["message"] = RunCoordinator._message_payload(step.message)
        RunCoordinator._add_context_step_ids_payload(payload, step)
        if step.sandbox_policy is not None:
            payload["sandbox_policy"] = RunCoordinator._sandbox_policy_payload(
                step.sandbox_policy
            )
        payload["approval_required"] = step.approval_required
        payload["approval_status"] = approval_status
        return payload

    @staticmethod
    def _message_payload(message: ProviderMessage) -> dict[str, object]:
        return {key: value for key, value in asdict(message).items() if value is not None}

    @staticmethod
    def _parse_plan_proposal(content: str, plan_id: str) -> tuple[PlanStepProposal, ...]:
        """Validate the accepted plan proposal shape, or raise ``ValueError``.

        Each step's executable payload is validated with the same
        ``_validate_command``/``_validate_sandbox_policy``/``_validate_message``
        rules ``add_step`` enforces, so a persisted draft step is always
        compatible with the existing queued-step creation path. ``step_id``
        is materialized deterministically from ``plan_id`` and the step's
        1-based position, never taken from the model.
        """

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(f"plan proposal is not valid JSON: {error}") from error
        if not isinstance(parsed, Mapping):
            raise ValueError("plan proposal must be a JSON object")
        steps = parsed.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("plan proposal must include a non-empty 'steps' list")

        proposals: list[PlanStepProposal] = []
        for index, item in enumerate(steps):
            if not isinstance(item, Mapping):
                raise ValueError(f"plan proposal step {index} must be a JSON object")
            step_objective = item.get("objective")
            execution_kind = item.get("execution_kind")
            if not isinstance(step_objective, str) or not step_objective.strip():
                raise ValueError(
                    f"plan proposal step {index} objective must be a non-empty string"
                )
            if execution_kind not in ("command", "provider"):
                raise ValueError(
                    f"plan proposal step {index} execution_kind must be "
                    "'command' or 'provider'"
                )
            command = item.get("command")
            timeout = item.get("timeout")
            sandbox_policy = item.get("sandbox_policy")
            message = item.get("message")
            try:
                if execution_kind == "command":
                    if message is not None:
                        raise ValueError("command execution must not include 'message'")
                    if command is None:
                        raise ValueError("command execution requires 'command'")
                    if sandbox_policy is None:
                        raise ValueError("command execution requires 'sandbox_policy'")
                    normalized_command = RunCoordinator._validate_command(command, timeout)
                    normalized_sandbox_policy = RunCoordinator._validate_sandbox_policy(
                        sandbox_policy, has_command=True
                    )
                    normalized_message = None
                    normalized_timeout = timeout
                else:
                    if command is not None or timeout is not None or sandbox_policy is not None:
                        raise ValueError(
                            "provider execution must not include 'command', "
                            "'timeout', or 'sandbox_policy'"
                        )
                    if message is None:
                        raise ValueError("provider execution requires 'message'")
                    normalized_message = RunCoordinator._validate_message(message)
                    normalized_command = None
                    normalized_sandbox_policy = None
                    normalized_timeout = None
            except ValueError as error:
                raise ValueError(f"plan proposal step {index} {error}") from error
            proposals.append(
                PlanStepProposal(
                    step_id=f"{plan_id}-step-{index + 1}",
                    objective=step_objective,
                    execution_kind=execution_kind,
                    command=normalized_command,
                    timeout=normalized_timeout,
                    sandbox_policy=normalized_sandbox_policy,
                    message=normalized_message,
                )
            )
        return tuple(proposals)

    @staticmethod
    def _plan_step_proposal_payload(step: PlanStepProposal) -> dict[str, object]:
        payload: dict[str, object] = {
            "step_id": step.step_id,
            "objective": step.objective,
            "execution_kind": step.execution_kind,
        }
        if step.command is not None:
            payload["command"] = list(step.command)
        if step.timeout is not None:
            payload["timeout"] = step.timeout
        if step.sandbox_policy is not None:
            payload["sandbox_policy"] = RunCoordinator._sandbox_policy_payload(
                step.sandbox_policy
            )
        if step.message is not None:
            payload["message"] = RunCoordinator._message_payload(step.message)
        return payload

    @staticmethod
    def _plan_step_proposal(item: Mapping[str, object]) -> PlanStepProposal:
        command = item.get("command")
        timeout = item.get("timeout")
        message = item.get("message")
        normalized_command = RunCoordinator._validate_command(command, timeout)
        normalized_message = RunCoordinator._validate_message(message)
        normalized_sandbox_policy = RunCoordinator._validate_sandbox_policy(
            item.get("sandbox_policy"), has_command=normalized_command is not None
        )
        return PlanStepProposal(
            step_id=str(item["step_id"]),
            objective=str(item["objective"]),
            execution_kind=str(item["execution_kind"]),
            command=normalized_command,
            timeout=timeout,
            sandbox_policy=normalized_sandbox_policy,
            message=normalized_message,
        )

    @staticmethod
    def _plan_draft(record: StateRecord) -> PlanDraft:
        payload = record.payload
        raw_steps = payload.get("steps")
        steps = (
            tuple(RunCoordinator._plan_step_proposal(item) for item in raw_steps)
            if isinstance(raw_steps, list)
            else ()
        )
        return PlanDraft(
            plan_id=record.key,
            run_id=str(payload["run_id"]),
            status=record.status,
            revision=record.revision,
            steps=steps,
            evidence=payload.get("evidence"),
            error=payload.get("error"),
            decision_agent_id=payload.get("decision_agent_id"),
        )

    def _validate_context_step_ids(
        self,
        run_id: str,
        context_step_ids: Sequence[str] | None,
        *,
        has_message: bool,
    ) -> tuple[str, ...]:
        normalized = self._validate_stored_context_step_ids(
            context_step_ids, has_message=has_message
        )
        for context_step_id in normalized:
            referenced = self.get_step(context_step_id)
            if referenced is None:
                raise ValueError(f"context step does not exist: {context_step_id}")
            if referenced.run_id != run_id:
                raise ValueError(
                    f"context step belongs to another run: {context_step_id}"
                )
        return normalized

    @staticmethod
    def _validate_stored_context_step_ids(
        context_step_ids: Sequence[str] | object | None,
        *,
        has_message: bool,
    ) -> tuple[str, ...]:
        if context_step_ids is None:
            return ()
        if isinstance(context_step_ids, (str, bytes)) or not isinstance(
            context_step_ids, Sequence
        ):
            raise ValueError("context step ids must be a sequence")
        normalized = tuple(context_step_ids)
        if any(not isinstance(step_id, str) or not step_id for step_id in normalized):
            raise ValueError("context step ids must be non-empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("context step ids must be unique")
        if normalized and not has_message:
            raise ValueError("context step ids require a provider message")
        return normalized

    @staticmethod
    def _sandbox_policy_payload(policy: SandboxPolicy) -> dict[str, object]:
        return {
            "kind": policy.kind.value,
            "image": policy.image,
            "mounts": [list(mount) for mount in policy.mounts],
            "working_dir": policy.working_dir,
            "env_passthrough": list(policy.env_passthrough),
            "network_enabled": policy.network_enabled,
        }

    @staticmethod
    def _execution_kind(step: RunStep) -> str:
        """Return the non-sensitive execution category persisted in history."""

        return "command" if step.command is not None else "provider"

    def _superseded_step_ids(self, run_id: str) -> frozenset[str]:
        """Return failed attempts durably superseded by explicit retries."""

        return frozenset(
            entry.retried_step_id
            for entry in self.list_history(run_id)
            if entry.transition == "step_retried" and entry.retried_step_id is not None
        )

    @staticmethod
    def _validate_message(
        message: ProviderMessage | Mapping[str, object] | object | None,
    ) -> ProviderMessage | None:
        if message is None:
            return None
        if isinstance(message, ProviderMessage):
            values = asdict(message)
        elif isinstance(message, Mapping):
            allowed = {"provider", "content", "model", "system", "temperature", "max_tokens"}
            if set(message) - allowed:
                raise ValueError("step provider message has unknown fields")
            values = dict(message)
        else:
            raise ValueError("step provider message must be an object")
        provider = values.get("provider")
        content = values.get("content")
        model = values.get("model")
        system = values.get("system")
        temperature = values.get("temperature")
        max_tokens = values.get("max_tokens")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("step provider must be a non-empty string")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("step message content must be a non-empty string")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("step model must be a non-empty string")
        if system is not None and (not isinstance(system, str) or not system.strip()):
            raise ValueError("step system message must be a non-empty string")
        if temperature is not None and (
            not isinstance(temperature, (int, float))
            or isinstance(temperature, bool)
            or temperature < 0
        ):
            raise ValueError("step temperature must be non-negative")
        if max_tokens is not None and (
            not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1
        ):
            raise ValueError("step max tokens must be a positive integer")
        return ProviderMessage(
            provider=provider,
            content=content,
            model=model,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
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

    @staticmethod
    def _validate_sandbox_policy(
        policy: SandboxPolicy | Mapping[str, object] | object | None,
        *,
        has_command: bool,
    ) -> SandboxPolicy | None:
        if policy is None:
            return None
        if not has_command:
            raise ValueError("sandbox policy is only valid for command steps")
        if isinstance(policy, SandboxPolicy):
            values = asdict(policy)
        elif isinstance(policy, Mapping):
            allowed = {
                "kind", "image", "mounts", "working_dir", "env_passthrough", "network_enabled",
            }
            if set(policy) - allowed:
                raise ValueError("step sandbox policy has unknown fields")
            values = dict(policy)
        else:
            raise ValueError("step sandbox policy must be an object")
        try:
            kind = SandboxKind(values.get("kind"))
        except (TypeError, ValueError) as error:
            raise ValueError("step sandbox policy kind is invalid") from error
        image = values.get("image", "python:3.12-slim")
        if not isinstance(image, str) or not image.strip():
            raise ValueError("step sandbox policy image must be a non-empty string")
        mounts = tuple(tuple(mount) for mount in values.get("mounts", ()))
        for mount in mounts:
            if len(mount) != 2 or not all(
                isinstance(part, str) and part for part in mount
            ):
                raise ValueError(
                    "step sandbox policy mounts require non-empty host and container paths"
                )
        working_dir = values.get("working_dir")
        if working_dir is not None and (
            not isinstance(working_dir, str)
            or not working_dir.strip()
            or not posixpath.isabs(working_dir)
        ):
            raise ValueError(
                "step sandbox policy working directory must be a non-empty absolute path"
            )
        env_passthrough = tuple(values.get("env_passthrough", ()))
        if any(
            not isinstance(name, str) or not name.isidentifier() for name in env_passthrough
        ):
            raise ValueError(
                "step sandbox policy env passthrough names must be valid identifiers"
            )
        if len(set(env_passthrough)) != len(env_passthrough):
            raise ValueError("step sandbox policy env passthrough names must be unique")
        network_enabled = values.get("network_enabled", False)
        if not isinstance(network_enabled, bool):
            raise ValueError("step sandbox policy network option must be a boolean")
        return SandboxPolicy(
            kind=kind,
            image=image,
            mounts=mounts,
            working_dir=working_dir,
            env_passthrough=env_passthrough,
            network_enabled=network_enabled,
        )


class AgentRegistry:
    """Register, inspect, heartbeat, and list identities backed by ``StateStore``."""

    def __init__(
        self,
        store: StateStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def register(self, agent_id: str, *, label: str | None = None) -> Agent:
        """Create a durable agent record at revision one, rejecting a duplicate id."""

        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        if label is not None and not label.strip():
            raise ValueError("agent label must not be empty")
        payload: dict[str, object] = {"last_seen": self._timestamp()}
        if label is not None:
            payload["label"] = label
        try:
            record = self.store.insert(
                "agent", agent_id, status="registered", payload=payload
            )
        except StateConflictError as error:
            raise ValueError(f"agent already exists: {agent_id}") from error
        return self._agent(record)

    def heartbeat(self, agent_id: str) -> Agent:
        """Refresh an existing agent's UTC liveness timestamp."""

        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        record = self.store.get("agent", agent_id)
        if record is None:
            raise ValueError(f"agent does not exist: {agent_id}")
        payload = {**record.payload, "last_seen": self._timestamp()}
        return self._agent(
            self.store.put("agent", agent_id, status=record.status, payload=payload)
        )

    def get(self, agent_id: str) -> Agent | None:
        """Return one registered agent without mutating its durable record."""

        if not agent_id.strip():
            raise ValueError("agent id must not be empty")
        record = self.store.get("agent", agent_id)
        return None if record is None else self._agent(record)

    def list_agents(self) -> tuple[Agent, ...]:
        """Return all registered agents in stable identifier order."""

        return tuple(self._agent(record) for record in self.store.list("agent"))

    @staticmethod
    def _agent(record: StateRecord) -> Agent:
        label = record.payload.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError(f"agent record has invalid label: {record.key}")
        last_seen = record.payload.get("last_seen")
        if last_seen is not None and not isinstance(last_seen, str):
            raise ValueError(f"agent record has invalid last_seen: {record.key}")
        return Agent(
            agent_id=record.key,
            label=label,
            revision=record.revision,
            last_seen=last_seen,
        )

    def _timestamp(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("agent registry clock must return a timezone-aware datetime")
        return moment.astimezone(timezone.utc).isoformat()
