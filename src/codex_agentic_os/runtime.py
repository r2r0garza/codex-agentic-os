"""Runtime selection and durable run-lifecycle coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import posixpath
import re
from typing import Callable, Mapping, Protocol, Sequence

from .chat import (
    ChatAdapter,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatToolCall,
    ChatToolDeclaration,
)
from .providers import (
    DEFAULT_PROVIDER_ROUTING_POLICY,
    DEFAULT_PROVIDER_SPECS,
    ProviderRoute,
    ProviderRoutingPolicy,
    ProviderSpec,
)
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


class DelegationPendingError(ValueError):
    """Raised when dispatch reaches a run whose delegation step awaits its child run.

    A delegation step stays ``running`` after its child run is atomically
    spawned and resolves only after its linked child reaches a terminal
    status. This error lets callers like the worker loop distinguish that
    legitimate parked state from the unexpected "another step is already
    running" conflict.
    """


class PlanProposalError(ValueError):
    """Raised when a provider's plan proposal is malformed or unparseable.

    The malformed proposal and its raw provider evidence are durably recorded
    as an ``invalid`` plan draft before this error is raised, so the failure
    remains operator-inspectable.
    """


class _ToolLoopCancelled(Exception):
    """Internal signal that a concurrent cancellation superseded a durable tool-loop write.

    Raised only by the tool-loop's own CAS-guarded persist helpers when their
    write conflicts because the run or step left ``RUNNING``; caught inside
    the loop to stop cleanly instead of surfacing a generic conflict error.
    """


class StepRecoveryReason(StrEnum):
    """Explicit reasons for failing a running step with an uncertain result."""

    INTERRUPTED = "interrupted"
    TIMED_OUT = "timed_out"


class StepFailureKind(StrEnum):
    """Operator-visible certainty of a durable failed-step outcome."""

    DEFINITE = "definite"
    UNCERTAIN = "uncertain"


class ArtifactStatus(StrEnum):
    """Durable outcome of one declared command-step artifact after execution."""

    CAPTURED = "captured"
    ABSENT = "absent"
    REJECTED = "rejected"


DEFAULT_ARTIFACT_SIZE_LIMIT_BYTES = 10_000_000


@dataclass(frozen=True, slots=True)
class AgentRun:
    """Typed view of a durable run record."""

    run_id: str
    objective: str
    status: RunStatus
    revision: int
    agent_id: str | None = None
    output: Mapping[str, object] | None = None
    parent_run_id: str | None = None
    parent_step_id: str | None = None


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
class ArtifactDeclaration:
    """One named workspace path a command step declares for artifact capture."""

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Typed view of a durable command-step artifact outcome."""

    artifact_id: str
    run_id: str
    step_id: str
    name: str
    source_path: str
    status: ArtifactStatus
    content_hash: str | None = None
    size_bytes: int | None = None
    size_limit_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class DelegationSpec:
    """Durable declaration of one step's child-run delegation."""

    child_objective: str
    target_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """Durable declaration of one named tool a provider step may invoke.

    Bound to a persisted sandboxed command template that executes under the
    declaring step's own sandbox policy once a model requests this tool by
    name; only the declaration is persisted here, not its execution.
    """

    name: str
    command: tuple[str, ...]
    description: str | None = None
    parameters: Mapping[str, object] | None = None


class ToolCallPhase(StrEnum):
    """Durable phase of one in-flight or completed tool call within a step.

    ``REQUESTED`` is written before the tool's sandboxed command executes;
    ``EXECUTED`` is written once its result is durable. A step found
    ``RUNNING`` whose last iteration is still ``REQUESTED`` is a genuinely
    uncertain in-progress execution and fails definitively through the
    existing recovery contract instead of silently re-executing the tool.
    A step whose last iteration is durably ``EXECUTED`` is a safe boundary:
    a replacement worker resumes provider continuation from there without
    repeating the completed sandbox execution.
    """

    REQUESTED = "requested"
    EXECUTED = "executed"
    REJECTED_UNDECLARED = "rejected_undeclared"
    REJECTED_BUDGET = "rejected_budget"


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """Durable evidence of one model-requested tool invocation and its sandboxed outcome."""

    tool_name: str
    arguments: Mapping[str, object]
    call_id: str | None
    phase: ToolCallPhase
    command: tuple[str, ...] | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None


@dataclass(frozen=True, slots=True)
class ToolIterationRecord:
    """One ordered provider response and its requested tool-call outcome."""

    response: Mapping[str, object]
    tool_call: ToolCallRecord


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
    tool_declarations: tuple[ToolDeclaration, ...] = ()
    tool_iteration_budget: int | None = None
    tool_iterations: tuple[ToolIterationRecord, ...] = ()
    artifact_declarations: tuple[ArtifactDeclaration, ...] = ()
    response_artifact_name: str | None = None
    delegation: DelegationSpec | None = None
    delegated_run_id: str | None = None

    @property
    def tool_call(self) -> ToolCallRecord | None:
        """Return the latest call for compatibility with single-round callers."""

        return self.tool_iterations[-1].tool_call if self.tool_iterations else None

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


# Capability names declared by ``DEFAULT_PROVIDER_SPECS``, checked against a
# step's ``required_capability`` before any state mutation. There is no
# operator-configured provider registry beyond these defaults yet.
_KNOWN_PROVIDER_CAPABILITIES: frozenset[str] = frozenset(
    capability for spec in DEFAULT_PROVIDER_SPECS for capability in spec.capabilities
)


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    """Provider-neutral input for one durable model-backed step.

    Exactly one of ``provider`` (a fixed dispatch target) or
    ``required_capability`` (resolved to a provider at dispatch time) is set.
    """

    provider: str | None
    content: str
    model: str | None = None
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    required_capability: str | None = None


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
    'objective>", "execution_kind": "provider", "message": {"content": '
    '"<message content>", "model": "<optional model>", "system": "<optional '
    'system prompt>", "temperature": <optional number>, "max_tokens": '
    '<optional positive integer>, and exactly one of "provider": "<provider '
    'name>" or "required_capability": "<capability name>"}} and must not '
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
        artifact_storage_dir: str | Path | None = None,
        artifact_size_limit_bytes: int = DEFAULT_ARTIFACT_SIZE_LIMIT_BYTES,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._artifact_storage_dir = (
            Path(artifact_storage_dir)
            if artifact_storage_dir is not None
            else Path(store.path).parent / "artifacts"
        )
        if artifact_size_limit_bytes <= 0:
            raise ValueError("artifact size limit must be positive")
        self._artifact_size_limit_bytes = artifact_size_limit_bytes

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
        payload: dict[str, object] = self._base_run_payload(current)
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

    def list_artifacts(
        self, run_id: str, *, step_id: str | None = None
    ) -> tuple[ArtifactRecord, ...]:
        """Return one run's durable artifact records, read-only, in stable order."""

        if self.get(run_id) is None:
            raise KeyError(f"run does not exist: {run_id}")
        records = (
            self._artifact(record)
            for record in self.store.list("artifact")
            if record.payload.get("run_id") == run_id
            and (step_id is None or record.payload.get("step_id") == step_id)
        )
        return tuple(sorted(records, key=lambda artifact: (artifact.step_id, artifact.name)))

    def read_artifact_content(self, artifact_id: str) -> bytes:
        """Return one captured artifact's stored content bytes, read-only.

        Raises ``KeyError`` when the artifact does not exist and ``ValueError``
        when it exists but has no exportable content (absent, rejected, or
        missing from local storage despite a captured record).
        """

        record = self.store.get("artifact", artifact_id)
        if record is None:
            raise KeyError(f"artifact does not exist: {artifact_id}")
        artifact = self._artifact(record)
        if artifact.status is not ArtifactStatus.CAPTURED:
            raise ValueError(
                f"artifact has no exportable content: {artifact_id} "
                f"({artifact.status.value})"
            )
        content_path = self._artifact_storage_dir / artifact_id
        if not content_path.is_file():
            raise ValueError(f"artifact content is missing from local storage: {artifact_id}")
        return content_path.read_bytes()

    def cancel(self, run_id: str) -> AgentRun:
        """Atomically cancel a run, its active steps, and any active delegated child.

        Cancelling a run is the one automatic child-cancellation policy this
        runtime applies: an active delegated child left behind by a
        cancelled parent would otherwise keep running unattended, with no
        parent step left to reconcile its eventual outcome. A child that has
        already reached a terminal status is left untouched rather than
        overwritten.
        """

        records: list[tuple[str, str, str, Mapping[str, object]]] = []
        expected: list[tuple[str, str, str, int]] = []
        history: list[RunHistoryEntry] = []
        target_index = self._collect_cancel_closure(
            run_id, set(), records, expected, history
        )
        stored = self.store.put_many(records, expected=expected, history=history)
        return self._run(stored[target_index])

    def _collect_cancel_closure(
        self,
        run_id: str,
        visited: set[str],
        records: list[tuple[str, str, str, Mapping[str, object]]],
        expected: list[tuple[str, str, str, int]],
        history: list[RunHistoryEntry],
    ) -> int:
        """Append cancel records/history for ``run_id`` and any active delegated child.

        Returns the index into ``records`` of ``run_id``'s own cancelled run
        record, so a single atomic :meth:`StateStore.put_many` call can cover
        the whole parent/child cancellation closure and the caller can still
        read back the run it originally asked to cancel.
        """

        if run_id in visited:
            raise ValueError(f"delegation cycle detected while cancelling: {run_id}")
        visited.add(run_id)

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
            if step.tool_declarations:
                payload["tools"] = self._tool_declarations_payload(step.tool_declarations)
                payload["tool_iteration_budget"] = step.tool_iteration_budget
            self._add_tool_iterations_payload(payload, step)
            if step.delegation is not None:
                payload["delegation"] = self._delegation_payload(step.delegation)
            if step.delegated_run_id is not None:
                payload["delegated_run_id"] = step.delegated_run_id
            self._add_context_step_ids_payload(payload, step)
            self._add_approval_payload(payload, step)
            records.append(("step", step.step_id, StepStatus.CANCELLED, payload))
            expected.append(("step", step.step_id, step.status, step.revision))
            history.append(
                RunHistoryEntry(
                    run_id, 0, "step_cancelled", StepStatus.CANCELLED,
                    step_id=step.step_id, agent_id=current.agent_id,
                    execution_kind=self._execution_kind(step),
                )
            )
            if step.delegated_run_id is not None:
                child = self.get(step.delegated_run_id)
                if child is not None and child.status in {
                    RunStatus.QUEUED, RunStatus.RUNNING,
                }:
                    if (
                        child.parent_run_id != run_id
                        or child.parent_step_id != step.step_id
                    ):
                        raise ValueError(
                            "delegated child run linkage does not match parent: "
                            f"{step.delegated_run_id}"
                        )
                    self._collect_cancel_closure(
                        step.delegated_run_id, visited, records, expected, history
                    )

        run_payload: dict[str, object] = self._base_run_payload(current)
        records.append(("run", run_id, RunStatus.CANCELLED, run_payload))
        expected.append(("run", run_id, current.status, current.revision))
        history.append(
            RunHistoryEntry(
                run_id, 0, "run_cancelled", RunStatus.CANCELLED,
                agent_id=current.agent_id,
            )
        )
        return len(records) - 1

    def start_next_step(
        self, run_id: str, *, provider_route: ProviderRoute | None = None
    ) -> RunStep | None:
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
        if next_step.delegation is not None:
            raise ValueError(
                "delegation step must be dispatched through execute_next_step, "
                f"which atomically spawns its linked child run: {next_step.step_id}"
            )
        if provider_route is not None:
            if (
                next_step.message is None
                or next_step.message.required_capability
                != provider_route.required_capability
            ):
                raise ValueError(
                    "provider route does not match the next capability-routed step"
                )
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
            run_payload: dict[str, object] = self._base_run_payload(run)
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
            if next_step.tool_declarations:
                step_payload["tools"] = self._tool_declarations_payload(
                    next_step.tool_declarations
                )
                step_payload["tool_iteration_budget"] = next_step.tool_iteration_budget
            if next_step.artifact_declarations:
                step_payload["artifacts"] = self._artifact_declarations_payload(
                    next_step.artifact_declarations
                )
            if next_step.response_artifact_name is not None:
                step_payload["response_artifact_name"] = next_step.response_artifact_name
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
                        required_capability=(
                            None
                            if next_step.message is None
                            else next_step.message.required_capability
                        ),
                        resolved_provider=(
                            None
                            if provider_route is None
                            else provider_route.provider.value
                        ),
                        resolved_model=(
                            None if provider_route is None else provider_route.model
                        ),
                        routing_reason=(
                            None if provider_route is None else provider_route.reason
                        ),
                    ),
                ),
            )
            return self._step(stored[1])
        return self.transition_step(
            next_step.step_id,
            StepStatus.RUNNING,
            resolved_context_step_ids=next_step.context_step_ids or None,
            provider_route=provider_route,
        )

    def execute_next_step(
        self,
        run_id: str,
        executor: SandboxExecutor | None = None,
        *,
        sandbox_resolver: SandboxPolicyResolver | None = None,
        adapter_resolver: ChatAdapterResolver | None = None,
        routing_policy: ProviderRoutingPolicy = DEFAULT_PROVIDER_ROUTING_POLICY,
        provider_specs: Sequence[ProviderSpec] = DEFAULT_PROVIDER_SPECS,
    ) -> tuple[RunStep, AgentRun] | None:
        """Execute or reconcile the run's current command, provider, or delegation step."""

        run = self.get(run_id)
        if run is None:
            raise KeyError(f"run does not exist: {run_id}")
        if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError(f"cannot execute a step for terminal run: {run_id}")

        steps = self.list_steps(run_id)
        running_step = next(
            (step for step in steps if step.status is StepStatus.RUNNING), None
        )
        if running_step is not None:
            if running_step.delegation is not None:
                return self._reconcile_delegation_step(run, running_step)
            if (
                running_step.tool_iterations
                and running_step.tool_iterations[-1].tool_call.phase
                is ToolCallPhase.EXECUTED
            ):
                return self._resume_tool_loop(
                    run,
                    running_step,
                    sandbox_resolver=sandbox_resolver,
                    adapter_resolver=adapter_resolver,
                    routing_policy=routing_policy,
                    provider_specs=provider_specs,
                )
            raise ValueError(f"run already has a running step: {run_id}")
        next_step = next(
            (step for step in steps if step.status is StepStatus.QUEUED), None
        )
        if next_step is None:
            return None
        if next_step.delegation is not None:
            return self._dispatch_delegation_step(run, next_step)
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

        provider_route = None
        routing_error: ValueError | None = None
        if (
            next_step.message is not None
            and next_step.message.required_capability is not None
        ):
            try:
                provider_route = routing_policy.resolve(
                    next_step.message.required_capability,
                    tuple(provider_specs),
                    model=next_step.message.model,
                )
            except ValueError as error:
                routing_error = error

        running_step = self.start_next_step(run_id, provider_route=provider_route)
        if running_step is None:  # Defensive: next_step proved queued above.
            return None
        if routing_error is not None:
            return self.fail_step_from_error(running_step.step_id, routing_error)
        if running_step.command is not None:
            assert resolved_executor is not None
            result = resolved_executor.execute(
                running_step.command, timeout=running_step.timeout
            )
            return self.complete_step_from_result(running_step.step_id, result)

        assert adapter_resolver is not None and running_step.message is not None
        resolved_message = running_step.message
        if provider_route is not None:
            resolved_message = ProviderMessage(
                provider=provider_route.provider.value,
                content=running_step.message.content,
                model=provider_route.model,
                system=running_step.message.system,
                temperature=running_step.message.temperature,
                max_tokens=running_step.message.max_tokens,
            )
        try:
            adapter = adapter_resolver(resolved_message)
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
            tool_declarations = tuple(
                ChatToolDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                )
                for tool in running_step.tool_declarations
            )
            response = adapter.complete(
                ChatRequest(
                    messages,
                    temperature=running_step.message.temperature,
                    max_tokens=running_step.message.max_tokens,
                    tools=tool_declarations,
                )
            )
        except (ValueError, RuntimeError, NotImplementedError) as error:
            return self.fail_step_from_error(running_step.step_id, error)

        return self._advance_tool_loop(
            run, running_step, adapter, messages, tool_declarations, response, sandbox_resolver
        )

    def _resume_tool_loop(
        self,
        run: AgentRun,
        running_step: RunStep,
        *,
        sandbox_resolver: SandboxPolicyResolver | None,
        adapter_resolver: ChatAdapterResolver | None,
        routing_policy: ProviderRoutingPolicy,
        provider_specs: Sequence[ProviderSpec],
    ) -> tuple[RunStep, AgentRun]:
        """Resume a tool-declaring step whose durable iterations end at a completed boundary.

        Only reached when the step's last persisted iteration is
        :attr:`ToolCallPhase.EXECUTED`, which durably implies every prior
        iteration is too, so the full turn history safely replays from
        stored evidence without re-executing any sandboxed tool call.
        """

        assert running_step.message is not None
        if adapter_resolver is None:
            raise ValueError(
                f"next provider-message step requires an adapter: {running_step.step_id}"
            )
        provider_route = None
        if running_step.message.required_capability is not None:
            try:
                provider_route = routing_policy.resolve(
                    running_step.message.required_capability,
                    tuple(provider_specs),
                    model=running_step.message.model,
                )
            except ValueError as error:
                return self.fail_step_from_error(running_step.step_id, error)
        resolved_message = running_step.message
        if provider_route is not None:
            resolved_message = ProviderMessage(
                provider=provider_route.provider.value,
                content=running_step.message.content,
                model=provider_route.model,
                system=running_step.message.system,
                temperature=running_step.message.temperature,
                max_tokens=running_step.message.max_tokens,
            )
        try:
            adapter = adapter_resolver(resolved_message)
            system_messages = (
                (ChatMessage("system", running_step.message.system),)
                if running_step.message.system is not None
                else ()
            )
            context_messages = self._resolve_context_messages(running_step)
            tool_declarations = tuple(
                ChatToolDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                )
                for tool in running_step.tool_declarations
            )
            messages = (
                system_messages
                + context_messages
                + (ChatMessage("user", running_step.message.content),)
                + self._replay_tool_iteration_messages(running_step)
            )
            response = adapter.complete(
                ChatRequest(
                    messages,
                    temperature=running_step.message.temperature,
                    max_tokens=running_step.message.max_tokens,
                    tools=tool_declarations,
                )
            )
        except (ValueError, RuntimeError, NotImplementedError) as error:
            return self.fail_step_from_error(running_step.step_id, error)

        return self._advance_tool_loop(
            run, running_step, adapter, messages, tool_declarations, response, sandbox_resolver
        )

    @staticmethod
    def _replay_tool_iteration_messages(step: RunStep) -> tuple[ChatMessage, ...]:
        """Rebuild the assistant/tool turn history from durable completed iterations."""

        messages: list[ChatMessage] = []
        for iteration in step.tool_iterations:
            call = iteration.tool_call
            content = iteration.response.get("content")
            messages.append(
                ChatMessage(
                    "assistant",
                    content if isinstance(content, str) else "",
                    tool_call=ChatToolCall(
                        name=call.tool_name,
                        arguments=dict(call.arguments),
                        call_id=call.call_id,
                    ),
                )
            )
            tool_result_text = json.dumps(
                {
                    "exit_code": call.exit_code,
                    "stdout": call.stdout,
                    "stderr": call.stderr,
                }
            )
            messages.append(
                ChatMessage(
                    "tool",
                    tool_result_text,
                    tool_result_for=call.call_id or call.tool_name,
                )
            )
        return tuple(messages)

    def _tool_loop_cancelled(
        self, run_id: str, step_id: str
    ) -> tuple[RunStep, AgentRun] | None:
        """Return the current (step, run) pair if either left ``RUNNING`` mid-loop, else ``None``."""

        current_run = self.get(run_id)
        current_step = self.get_step(step_id)
        if current_run is None or current_step is None:
            return None  # Defensive: a durably referenced run/step must still exist.
        if (
            current_run.status is RunStatus.RUNNING
            and current_step.status is StepStatus.RUNNING
        ):
            return None
        return current_step, current_run

    def _advance_tool_loop(
        self,
        run: AgentRun,
        running_step: RunStep,
        adapter: ChatAdapter,
        messages: tuple[ChatMessage, ...],
        tool_declarations: tuple[ChatToolDeclaration, ...],
        response: ChatResponse,
        sandbox_resolver: SandboxPolicyResolver | None,
    ) -> tuple[RunStep, AgentRun]:
        """Run, or continue, the bounded tool-call loop from a fresh provider response.

        Checked at the top of every pass so a concurrent cancellation
        discovered before acting on ``response`` stops the loop without
        persisting another requested call or executing another sandboxed
        command; completed iterations already durable are left untouched.
        """

        while response.tool_call is not None:
            cancelled = self._tool_loop_cancelled(run.run_id, running_step.step_id)
            if cancelled is not None:
                return cancelled
            declaration = next(
                (
                    tool
                    for tool in running_step.tool_declarations
                    if tool.name == response.tool_call.name
                ),
                None,
            )
            if declaration is None:
                running_step = self._persist_tool_call_requested(
                    run,
                    running_step,
                    response,
                    phase=ToolCallPhase.REJECTED_UNDECLARED,
                )
                return self.fail_step_from_error(
                    running_step.step_id,
                    ValueError(
                        f"model requested an undeclared tool: {response.tool_call.name}"
                    ),
                    tool_name=response.tool_call.name,
                    tool_outcome="rejected_undeclared",
                )
            completed_iterations = sum(
                iteration.tool_call.phase is ToolCallPhase.EXECUTED
                for iteration in running_step.tool_iterations
            )
            if running_step.tool_iteration_budget is None:
                return self.fail_step_from_error(
                    running_step.step_id,
                    ValueError(
                        "tool-declaring provider step has no durable iteration budget: "
                        f"{running_step.step_id}"
                    ),
                )
            if completed_iterations >= running_step.tool_iteration_budget:
                running_step = self._persist_tool_call_requested(
                    run,
                    running_step,
                    response,
                    phase=ToolCallPhase.REJECTED_BUDGET,
                )
                return self.fail_step_from_error(
                    running_step.step_id,
                    ValueError(
                        "tool iteration budget exhausted before a final response: "
                        f"{running_step.step_id} "
                        f"({running_step.tool_iteration_budget})"
                    ),
                    tool_name=response.tool_call.name,
                    tool_outcome="rejected_budget",
                )
            if sandbox_resolver is None:
                running_step = self._persist_tool_call_requested(
                    run, running_step, response
                )
                return self.fail_step_from_error(
                    running_step.step_id,
                    ValueError(
                        "tool call requires a sandbox resolver: "
                        f"{running_step.step_id}"
                    ),
                )
            assert running_step.sandbox_policy is not None  # enforced by _validate_tool_declarations
            try:
                running_step = self._persist_tool_call_requested(
                    run, running_step, response
                )
            except _ToolLoopCancelled:
                cancelled = self._tool_loop_cancelled(run.run_id, running_step.step_id)
                assert cancelled is not None
                return cancelled
            tool_executor = sandbox_resolver(running_step.sandbox_policy)
            cancelled = self._tool_loop_cancelled(run.run_id, running_step.step_id)
            if cancelled is not None:
                return cancelled
            tool_result = tool_executor.execute(declaration.command)
            try:
                running_step = self._persist_tool_call_executed(
                    run, running_step, tool_result
                )
            except _ToolLoopCancelled:
                cancelled = self._tool_loop_cancelled(run.run_id, running_step.step_id)
                assert cancelled is not None
                return cancelled
            tool_call_record = running_step.tool_call
            assert tool_call_record is not None
            tool_result_text = json.dumps(
                {
                    "exit_code": tool_call_record.exit_code,
                    "stdout": tool_call_record.stdout,
                    "stderr": tool_call_record.stderr,
                }
            )
            messages = messages + (
                ChatMessage("assistant", response.content, tool_call=response.tool_call),
                ChatMessage(
                    "tool",
                    tool_result_text,
                    tool_result_for=response.tool_call.call_id or response.tool_call.name,
                ),
            )
            try:
                response = adapter.complete(
                    ChatRequest(
                        messages,
                        temperature=running_step.message.temperature,
                        max_tokens=running_step.message.max_tokens,
                        tools=tool_declarations,
                    )
                )
            except (ValueError, RuntimeError, NotImplementedError) as error:
                return self.fail_step_from_error(running_step.step_id, error)

        return self.complete_step_from_chat_response(running_step.step_id, response)

    @staticmethod
    def _running_provider_step_payload(step: RunStep) -> dict[str, object]:
        """Build the durable payload prefix preserved across in-flight tool-call phase writes."""

        assert step.message is not None
        payload: dict[str, object] = {
            "run_id": step.run_id,
            "position": step.position,
            "objective": step.objective,
            "message": RunCoordinator._message_payload(step.message),
        }
        if step.sandbox_policy is not None:
            payload["sandbox_policy"] = RunCoordinator._sandbox_policy_payload(
                step.sandbox_policy
            )
        if step.tool_declarations:
            payload["tools"] = RunCoordinator._tool_declarations_payload(
                step.tool_declarations
            )
            payload["tool_iteration_budget"] = step.tool_iteration_budget
        RunCoordinator._add_tool_iterations_payload(payload, step)
        if step.response_artifact_name is not None:
            payload["response_artifact_name"] = step.response_artifact_name
        RunCoordinator._add_context_step_ids_payload(payload, step)
        RunCoordinator._add_approval_payload(payload, step)
        return payload

    def _persist_tool_call_requested(
        self,
        run: AgentRun,
        step: RunStep,
        response: ChatResponse,
        *,
        phase: ToolCallPhase = ToolCallPhase.REQUESTED,
    ) -> RunStep:
        """Durably record a model's tool-call request before its command executes."""

        assert response.tool_call is not None
        call_record = ToolCallRecord(
            tool_name=response.tool_call.name,
            arguments=dict(response.tool_call.arguments),
            call_id=response.tool_call.call_id,
            phase=phase,
        )
        iteration = ToolIterationRecord(
            response=self._chat_response_payload(response),
            tool_call=call_record,
        )
        payload = self._running_provider_step_payload(step)
        payload["tool_iterations"] = self._tool_iterations_payload(
            step.tool_iterations + (iteration,)
        )
        history_entry = RunHistoryEntry(
            step.run_id,
            0,
            (
                "tool_call_requested"
                if phase is ToolCallPhase.REQUESTED
                else "tool_response_recorded"
            ),
            StepStatus.RUNNING,
            step_id=step.step_id,
            agent_id=run.agent_id,
            execution_kind="provider",
            tool_name=(
                call_record.tool_name if phase is ToolCallPhase.REQUESTED else None
            ),
            tool_outcome=(phase.value if phase is ToolCallPhase.REQUESTED else None),
        )
        try:
            stored = self.store.put_many(
                (("step", step.step_id, StepStatus.RUNNING, payload),),
                expected=(("step", step.step_id, StepStatus.RUNNING, step.revision),),
                history=(history_entry,),
            )
        except StateConflictError as error:
            if self._tool_loop_cancelled(step.run_id, step.step_id) is not None:
                raise _ToolLoopCancelled from error
            raise ValueError(f"step transition conflict: {step.step_id}") from error
        return self._step(stored[0])

    def _persist_tool_call_executed(
        self, run: AgentRun, step: RunStep, result: ExecutionResult
    ) -> RunStep:
        """Durably record a tool's sandboxed result before the follow-up model request."""

        assert step.tool_iterations
        assert step.tool_call is not None
        result_command = list(result.command)
        if step.sandbox_policy is not None:
            passthrough_names = frozenset(step.sandbox_policy.env_passthrough)
            for index, argument in enumerate(result_command):
                if index == 0 or result_command[index - 1] != "--env":
                    continue
                name, separator, _ = argument.partition("=")
                if separator and name in passthrough_names:
                    result_command[index] = name
        call_record = ToolCallRecord(
            tool_name=step.tool_call.tool_name,
            arguments=step.tool_call.arguments,
            call_id=step.tool_call.call_id,
            phase=ToolCallPhase.EXECUTED,
            command=tuple(result_command),
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        iteration = ToolIterationRecord(
            response=step.tool_iterations[-1].response,
            tool_call=call_record,
        )
        payload = self._running_provider_step_payload(step)
        payload["tool_iterations"] = self._tool_iterations_payload(
            step.tool_iterations[:-1] + (iteration,)
        )
        try:
            stored = self.store.put_many(
                (("step", step.step_id, StepStatus.RUNNING, payload),),
                expected=(("step", step.step_id, StepStatus.RUNNING, step.revision),),
                history=(
                    RunHistoryEntry(
                        step.run_id, 0, "tool_call_executed", StepStatus.RUNNING,
                        step_id=step.step_id, agent_id=run.agent_id,
                        execution_kind="provider", tool_name=call_record.tool_name,
                        tool_outcome=("succeeded" if result.returncode == 0 else "failed"),
                    ),
                ),
            )
        except StateConflictError as error:
            if self._tool_loop_cancelled(step.run_id, step.step_id) is not None:
                raise _ToolLoopCancelled from error
            raise ValueError(f"step transition conflict: {step.step_id}") from error
        return self._step(stored[0])

    def _reconcile_delegation_step(
        self, run: AgentRun, step: RunStep
    ) -> tuple[RunStep, AgentRun]:
        """Resolve a running delegation step from its linked child's terminal outcome."""

        child_run_id = step.delegated_run_id
        if child_run_id is None:  # Defensive: dispatched delegation steps always record it.
            raise ValueError(
                f"running delegation step has no linked child run: {step.step_id}"
            )
        child = self.get(child_run_id)
        if child is None:
            raise ValueError(f"delegated child run does not exist: {child_run_id}")
        if child.parent_run_id != run.run_id or child.parent_step_id != step.step_id:
            raise ValueError(
                f"delegated child run linkage does not match parent: {child_run_id}"
            )
        if child.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise DelegationPendingError(
                "step is delegated to a pending child run: "
                f"{step.step_id} -> {child_run_id} ({child.status.value})"
            )

        output: dict[str, object] = {
            "child_run_id": child.run_id,
            "child_status": child.status.value,
            "child_output": None if child.output is None else dict(child.output),
        }
        if child.agent_id is not None:
            output["child_agent_id"] = child.agent_id
        step_status = (
            StepStatus.SUCCEEDED
            if child.status is RunStatus.SUCCEEDED
            else StepStatus.FAILED
        )
        step_payload: dict[str, object] = {
            "run_id": step.run_id,
            "position": step.position,
            "objective": step.objective,
            "delegation": self._delegation_payload(step.delegation),
            "delegated_run_id": child_run_id,
            "output": output,
        }
        self._add_context_step_ids_payload(step_payload, step)
        self._add_approval_payload(step_payload, step)

        superseded_step_ids = self._superseded_step_ids(run.run_id)
        final = step_status is StepStatus.FAILED or all(
            candidate.step_id == step.step_id
            or candidate.status is StepStatus.SUCCEEDED
            or candidate.step_id in superseded_step_ids
            for candidate in self.list_steps(run.run_id)
        )
        if not final:
            try:
                stored = self.store.put_many(
                    (("step", step.step_id, step_status, step_payload),),
                    expected=(("step", step.step_id, step.status, step.revision),),
                    history=(
                        RunHistoryEntry(
                            run.run_id,
                            0,
                            "step_succeeded",
                            StepStatus.SUCCEEDED,
                            step_id=step.step_id,
                            agent_id=run.agent_id,
                            execution_kind="delegation",
                        ),
                    ),
                )
            except StateConflictError as error:
                raise ValueError(
                    f"delegation outcome conflict: {step.step_id}"
                ) from error
            return self._step(stored[0]), run

        run_status = (
            RunStatus.SUCCEEDED
            if step_status is StepStatus.SUCCEEDED
            else RunStatus.FAILED
        )
        run_output: dict[str, object]
        if run_status is RunStatus.SUCCEEDED:
            run_output = {"completed_steps": len(self.list_steps(run.run_id))}
        else:
            run_output = {
                "failed_step_id": step.step_id,
                "child_run_id": child.run_id,
                "child_status": child.status.value,
                "child_output": None if child.output is None else dict(child.output),
            }
        run_payload = self._base_run_payload(run)
        run_payload["output"] = run_output
        try:
            stored = self.store.put_many(
                (
                    ("step", step.step_id, step_status, step_payload),
                    ("run", run.run_id, run_status, run_payload),
                ),
                expected=(
                    ("step", step.step_id, step.status, step.revision),
                    ("run", run.run_id, run.status, run.revision),
                ),
                history=(
                    RunHistoryEntry(
                        run.run_id,
                        0,
                        f"step_{step_status.value}",
                        step_status,
                        step_id=step.step_id,
                        agent_id=run.agent_id,
                        execution_kind="delegation",
                    ),
                    RunHistoryEntry(
                        run.run_id,
                        0,
                        f"run_{run_status.value}",
                        run_status,
                        agent_id=run.agent_id,
                        execution_kind="delegation",
                    ),
                ),
            )
        except StateConflictError as error:
            raise ValueError(f"delegation outcome conflict: {step.step_id}") from error
        return self._step(stored[0]), self._run(stored[1])

    def _dispatch_delegation_step(
        self, run: AgentRun, next_step: RunStep
    ) -> tuple[RunStep, AgentRun]:
        """Atomically dispatch one queued delegation step and spawn its linked child run.

        Leaves the step ``running`` with its child run's id recorded; a later
        execution reconciles the parent from the child's terminal outcome.
        Duplicate or competing dispatch of the same step is
        prevented by the step's own compare-and-swap revision check inside
        :meth:`StateStore.dispatch_delegation_step`.
        """

        assert next_step.delegation is not None
        if next_step.approval_status is ApprovalStatus.PENDING:
            raise ApprovalRequiredError(
                f"step requires approval before dispatch: {next_step.step_id}"
            )
        target_agent_id = next_step.delegation.target_agent_id
        if target_agent_id is not None:
            self._require_registered_agent(target_agent_id)
        child_run_id = f"{next_step.step_id}-child"

        step_payload: dict[str, object] = {
            "run_id": next_step.run_id,
            "position": next_step.position,
            "objective": next_step.objective,
            "delegation": self._delegation_payload(next_step.delegation),
            "delegated_run_id": child_run_id,
        }
        self._add_context_step_ids_payload(step_payload, next_step)
        self._add_approval_payload(step_payload, next_step)

        child_payload: dict[str, object] = {
            "objective": next_step.delegation.child_objective,
            "parent_run_id": next_step.run_id,
            "parent_step_id": next_step.step_id,
        }
        if target_agent_id is not None:
            child_payload["agent_id"] = target_agent_id

        run_payload: dict[str, object] | None = None
        if run.status is RunStatus.QUEUED:
            run_payload = self._base_run_payload(run)

        try:
            step_record, run_record, _child_record = self.store.dispatch_delegation_step(
                next_step.step_id,
                child_run_id,
                expected_step_revision=next_step.revision,
                step_payload=step_payload,
                run_id=run.run_id,
                expected_run_status=run.status.value,
                expected_run_revision=run.revision,
                run_payload=run_payload,
                child_payload=child_payload,
                target_agent_id=target_agent_id,
            )
        except StateConflictError as error:
            raise ValueError(
                f"delegation dispatch conflict: {next_step.step_id}"
            ) from error
        return self._step(step_record), self._run(run_record)

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
        tools: Sequence[ToolDeclaration | Mapping[str, object]] | None = None,
        tool_iteration_budget: int | None = None,
        artifacts: Sequence[ArtifactDeclaration | Mapping[str, object]] | None = None,
        response_artifact_name: str | None = None,
        delegation: DelegationSpec | Mapping[str, object] | None = None,
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
        normalized_delegation = self._validate_delegation(delegation)
        if sum(
            value is not None
            for value in (normalized_command, normalized_message, normalized_delegation)
        ) != 1:
            raise ValueError(
                "step requires exactly one of command, provider message, or delegation"
            )
        if normalized_delegation is not None and normalized_delegation.target_agent_id is not None:
            self._require_registered_agent(normalized_delegation.target_agent_id)
            self._validate_delegation_lineage(run, normalized_delegation.target_agent_id)
        normalized_context_step_ids = self._validate_context_step_ids(
            run_id,
            context_step_ids,
            has_message=normalized_message is not None,
        )
        if not isinstance(approval_required, bool):
            raise ValueError("approval_required must be a boolean")
        normalized_sandbox_policy = self._validate_sandbox_policy(
            sandbox_policy,
            has_command=normalized_command is not None,
            has_tools=bool(tools),
        )
        normalized_tools = self._validate_tool_declarations(
            tools,
            has_message=normalized_message is not None,
            sandbox_policy=normalized_sandbox_policy,
        )
        normalized_tool_iteration_budget = self._validate_tool_iteration_budget(
            tool_iteration_budget,
            has_tools=bool(normalized_tools),
            require_explicit=True,
        )
        normalized_artifacts = self._validate_artifact_declarations(
            artifacts, sandbox_policy=normalized_sandbox_policy
        )
        normalized_response_artifact_name = self._validate_response_artifact_name(
            response_artifact_name, has_message=normalized_message is not None
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
        if normalized_tools:
            payload["tools"] = self._tool_declarations_payload(normalized_tools)
            payload["tool_iteration_budget"] = normalized_tool_iteration_budget
        if normalized_artifacts:
            payload["artifacts"] = self._artifact_declarations_payload(normalized_artifacts)
        if normalized_response_artifact_name is not None:
            payload["response_artifact_name"] = normalized_response_artifact_name
        if normalized_delegation is not None:
            payload["delegation"] = self._delegation_payload(normalized_delegation)
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
        provider_route: ProviderRoute | None = None,
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
        if provider_route is not None:
            if status is not StepStatus.RUNNING:
                raise ValueError("provider route is only valid when starting a step")
            if (
                current.message is None
                or current.message.required_capability
                != provider_route.required_capability
            ):
                raise ValueError("provider route does not match the provider step")
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
        if current.sandbox_policy is not None:
            payload["sandbox_policy"] = self._sandbox_policy_payload(current.sandbox_policy)
        if current.tool_declarations:
            payload["tools"] = self._tool_declarations_payload(current.tool_declarations)
            payload["tool_iteration_budget"] = current.tool_iteration_budget
        self._add_tool_iterations_payload(payload, current)
        if current.artifact_declarations:
            payload["artifacts"] = self._artifact_declarations_payload(
                current.artifact_declarations
            )
        if current.response_artifact_name is not None:
            payload["response_artifact_name"] = current.response_artifact_name
        if current.delegation is not None:
            payload["delegation"] = self._delegation_payload(current.delegation)
        if current.delegated_run_id is not None:
            payload["delegated_run_id"] = current.delegated_run_id
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
                required_capability=(
                    None if current.message is None else current.message.required_capability
                ),
                resolved_provider=(
                    None if provider_route is None else provider_route.provider.value
                ),
                resolved_model=(None if provider_route is None else provider_route.model),
                routing_reason=(None if provider_route is None else provider_route.reason),
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
        run_payload: dict[str, object] = self._base_run_payload(run)
        run_payload["output"] = {"failed_step_id": step_id, "error": output["error"]}

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
        if step_status is StepStatus.SUCCEEDED:
            self._capture_artifacts(current)
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
        if current.tool_declarations:
            step_payload["tools"] = self._tool_declarations_payload(current.tool_declarations)
            step_payload["tool_iteration_budget"] = current.tool_iteration_budget
        self._add_tool_iterations_payload(step_payload, current)
        if current.artifact_declarations:
            step_payload["artifacts"] = self._artifact_declarations_payload(
                current.artifact_declarations
            )
        if current.response_artifact_name is not None:
            step_payload["response_artifact_name"] = current.response_artifact_name
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

        run_payload: dict[str, object] = self._base_run_payload(run)
        run_payload["output"] = run_output
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
        if current.tool_call is not None:
            output["tool_call"] = self._tool_call_payload(current.tool_call)
            output["tool_iterations"] = self._tool_iterations_payload(
                current.tool_iterations
            )
        run = self.get(current.run_id)
        if run is None:
            raise KeyError(f"run does not exist: {current.run_id}")
        if run.status is not RunStatus.RUNNING or current.status is not StepStatus.RUNNING:
            raise ValueError(f"step and run must be running to record a response: {step_id}")
        if current.response_artifact_name is not None:
            self._capture_response_artifact(current, response.content)
        step_payload = {
            "run_id": current.run_id,
            "position": current.position,
            "objective": current.objective,
            "message": self._message_payload(current.message),
            "output": output,
        }
        if current.sandbox_policy is not None:
            step_payload["sandbox_policy"] = self._sandbox_policy_payload(current.sandbox_policy)
        if current.tool_declarations:
            step_payload["tools"] = self._tool_declarations_payload(current.tool_declarations)
            step_payload["tool_iteration_budget"] = current.tool_iteration_budget
        self._add_tool_iterations_payload(step_payload, current)
        if current.response_artifact_name is not None:
            step_payload["response_artifact_name"] = current.response_artifact_name
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
        run_payload: dict[str, object] = self._base_run_payload(run)
        run_payload["output"] = {"completed_steps": len(self.list_steps(run.run_id))}
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
        self,
        step_id: str,
        error: Exception,
        *,
        tool_name: str | None = None,
        tool_outcome: str | None = None,
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
        if (tool_name is None) != (tool_outcome is None):
            raise ValueError("tool failure history requires both tool name and outcome")

        output: dict[str, object] = {
            "error": str(error),
            "error_type": type(error).__name__,
        }
        if current.tool_call is not None:
            output["tool_call"] = self._tool_call_payload(current.tool_call)
            output["tool_iterations"] = self._tool_iterations_payload(
                current.tool_iterations
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
        if current.tool_declarations:
            step_payload["tools"] = self._tool_declarations_payload(current.tool_declarations)
            step_payload["tool_iteration_budget"] = current.tool_iteration_budget
        self._add_tool_iterations_payload(step_payload, current)
        if current.artifact_declarations:
            step_payload["artifacts"] = self._artifact_declarations_payload(
                current.artifact_declarations
            )
        if current.response_artifact_name is not None:
            step_payload["response_artifact_name"] = current.response_artifact_name
        self._add_context_step_ids_payload(step_payload, current)
        self._add_approval_payload(step_payload, current)

        run_payload: dict[str, object] = self._base_run_payload(run)
        run_payload["output"] = {"failed_step_id": step_id, "error": str(error)}
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
                *(
                    (
                        RunHistoryEntry(
                            run.run_id,
                            0,
                            "tool_call_rejected",
                            StepStatus.FAILED,
                            step_id=step_id,
                            agent_id=run.agent_id,
                            execution_kind="provider",
                            tool_name=tool_name,
                            tool_outcome=tool_outcome,
                        ),
                    )
                    if tool_name is not None
                    else ()
                ),
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
        if current.delegation is not None:
            raise ValueError(
                "cannot recover a delegation step directly; recover the "
                "linked child run's own step instead: "
                f"{step_id} -> {current.delegated_run_id}"
            )
        if not isinstance(reason, StepRecoveryReason):
            raise ValueError("recovery reason must be a StepRecoveryReason")
        if detail is not None and not detail.strip():
            raise ValueError("recovery detail must not be empty")

        output: dict[str, object] = {"recovery_reason": reason.value}
        if detail is not None:
            output["recovery_detail"] = detail
        if current.tool_call is not None:
            output["tool_call"] = self._tool_call_payload(current.tool_call)
            output["tool_iterations"] = self._tool_iterations_payload(
                current.tool_iterations
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
        if current.tool_declarations:
            step_payload["tools"] = self._tool_declarations_payload(current.tool_declarations)
            step_payload["tool_iteration_budget"] = current.tool_iteration_budget
        self._add_tool_iterations_payload(step_payload, current)
        if current.artifact_declarations:
            step_payload["artifacts"] = self._artifact_declarations_payload(
                current.artifact_declarations
            )
        if current.response_artifact_name is not None:
            step_payload["response_artifact_name"] = current.response_artifact_name
        if current.delegation is not None:
            step_payload["delegation"] = self._delegation_payload(current.delegation)
        if current.delegated_run_id is not None:
            step_payload["delegated_run_id"] = current.delegated_run_id
        self._add_context_step_ids_payload(step_payload, current)
        self._add_approval_payload(step_payload, current)

        run_payload: dict[str, object] = self._base_run_payload(run)
        run_payload["output"] = {
            "failed_step_id": step_id,
            "recovery_reason": reason.value,
        }
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
        parent_run_id = record.payload.get("parent_run_id")
        parent_step_id = record.payload.get("parent_step_id")
        if not isinstance(objective, str):
            raise ValueError(f"run record has invalid objective: {record.key}")
        if agent_id is not None and not isinstance(agent_id, str):
            raise ValueError(f"run record has invalid agent id: {record.key}")
        if output is not None and not isinstance(output, dict):
            raise ValueError(f"run record has invalid output: {record.key}")
        if parent_run_id is not None and not isinstance(parent_run_id, str):
            raise ValueError(f"run record has invalid parent run id: {record.key}")
        if parent_step_id is not None and not isinstance(parent_step_id, str):
            raise ValueError(f"run record has invalid parent step id: {record.key}")
        if (parent_run_id is None) != (parent_step_id is None):
            raise ValueError(f"run record has inconsistent parent linkage: {record.key}")
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
            parent_run_id=parent_run_id,
            parent_step_id=parent_step_id,
        )

    @staticmethod
    def _base_run_payload(run: AgentRun) -> dict[str, object]:
        """Build the run payload fields every lifecycle rewrite must preserve."""

        payload: dict[str, object] = {"objective": run.objective}
        if run.agent_id is not None:
            payload["agent_id"] = run.agent_id
        if run.parent_run_id is not None:
            payload["parent_run_id"] = run.parent_run_id
        if run.parent_step_id is not None:
            payload["parent_step_id"] = run.parent_step_id
        return payload

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
        tools = record.payload.get("tools")
        tool_iteration_budget = record.payload.get("tool_iteration_budget")
        tool_call = record.payload.get("tool_call")
        tool_iterations = record.payload.get("tool_iterations")
        artifacts = record.payload.get("artifacts")
        response_artifact_name = record.payload.get("response_artifact_name")
        approval_required = record.payload.get("approval_required", False)
        approval_status_value = record.payload.get("approval_status")
        delegation = record.payload.get("delegation")
        delegated_run_id = record.payload.get("delegated_run_id")
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
        normalized_delegation = RunCoordinator._validate_delegation(delegation)
        normalized_context_step_ids = RunCoordinator._validate_stored_context_step_ids(
            context_step_ids, has_message=normalized_message is not None
        )
        if sum(
            value is not None
            for value in (normalized_command, normalized_message, normalized_delegation)
        ) > 1:
            raise ValueError(f"step record has ambiguous execution input: {record.key}")
        if delegated_run_id is not None and (
            not isinstance(delegated_run_id, str) or not delegated_run_id
        ):
            raise ValueError(f"step record has invalid delegated run id: {record.key}")
        if delegated_run_id is not None and normalized_delegation is None:
            raise ValueError(f"step record has an orphaned delegated run id: {record.key}")
        normalized_sandbox_policy = RunCoordinator._validate_sandbox_policy(
            sandbox_policy,
            has_command=normalized_command is not None,
            has_tools=bool(tools),
        )
        normalized_tools = RunCoordinator._validate_tool_declarations(
            tools,
            has_message=normalized_message is not None,
            sandbox_policy=normalized_sandbox_policy,
        )
        normalized_tool_iteration_budget = RunCoordinator._validate_tool_iteration_budget(
            tool_iteration_budget,
            has_tools=bool(normalized_tools),
            require_explicit=False,
        )
        normalized_tool_iterations = RunCoordinator._validate_tool_iterations(
            tool_iterations,
            legacy_tool_call=tool_call,
            output=output,
            tool_declarations=normalized_tools,
        )
        normalized_artifacts = RunCoordinator._validate_artifact_declarations(
            artifacts, sandbox_policy=normalized_sandbox_policy
        )
        normalized_response_artifact_name = RunCoordinator._validate_response_artifact_name(
            response_artifact_name, has_message=normalized_message is not None
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
            tool_declarations=normalized_tools,
            tool_iteration_budget=normalized_tool_iteration_budget,
            tool_iterations=normalized_tool_iterations,
            artifact_declarations=normalized_artifacts,
            response_artifact_name=normalized_response_artifact_name,
            delegation=normalized_delegation,
            delegated_run_id=delegated_run_id,
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
        if step.tool_declarations:
            payload["tools"] = RunCoordinator._tool_declarations_payload(
                step.tool_declarations
            )
            payload["tool_iteration_budget"] = step.tool_iteration_budget
        if step.artifact_declarations:
            payload["artifacts"] = RunCoordinator._artifact_declarations_payload(
                step.artifact_declarations
            )
        if step.response_artifact_name is not None:
            payload["response_artifact_name"] = step.response_artifact_name
        if step.delegation is not None:
            payload["delegation"] = RunCoordinator._delegation_payload(step.delegation)
        if step.delegated_run_id is not None:
            payload["delegated_run_id"] = step.delegated_run_id
        payload["approval_required"] = step.approval_required
        payload["approval_status"] = approval_status
        return payload

    @staticmethod
    def _message_payload(message: ProviderMessage) -> dict[str, object]:
        return {key: value for key, value in asdict(message).items() if value is not None}

    @staticmethod
    def _delegation_payload(delegation: DelegationSpec) -> dict[str, object]:
        return {key: value for key, value in asdict(delegation).items() if value is not None}

    @staticmethod
    def _validate_delegation(
        delegation: DelegationSpec | Mapping[str, object] | object | None,
    ) -> DelegationSpec | None:
        if delegation is None:
            return None
        if isinstance(delegation, DelegationSpec):
            values = asdict(delegation)
        elif isinstance(delegation, Mapping):
            allowed = {"child_objective", "target_agent_id"}
            if set(delegation) - allowed:
                raise ValueError("step delegation has unknown fields")
            values = dict(delegation)
        else:
            raise ValueError("step delegation must be an object")
        child_objective = values.get("child_objective")
        target_agent_id = values.get("target_agent_id")
        if not isinstance(child_objective, str) or not child_objective.strip():
            raise ValueError("step delegation child objective must be a non-empty string")
        if target_agent_id is not None and (
            not isinstance(target_agent_id, str) or not target_agent_id.strip()
        ):
            raise ValueError("step delegation target agent id must be a non-empty string")
        return DelegationSpec(
            child_objective=child_objective, target_agent_id=target_agent_id
        )

    def _validate_delegation_lineage(
        self, run: AgentRun, target_agent_id: str
    ) -> None:
        """Reject delegation to the current or an ancestor run's assigned agent."""

        current = run
        visited: set[str] = set()
        while True:
            if current.run_id in visited:
                raise ValueError(
                    f"delegation parent linkage contains a cycle: {current.run_id}"
                )
            visited.add(current.run_id)
            if current.agent_id == target_agent_id:
                if current.run_id == run.run_id:
                    raise ValueError(
                        f"delegation target agent matches the parent run agent: "
                        f"{target_agent_id}"
                    )
                raise ValueError(
                    f"delegation target agent would create an ancestor cycle: "
                    f"{target_agent_id}"
                )
            if current.parent_run_id is None:
                return
            parent = self.get(current.parent_run_id)
            if parent is None:
                raise ValueError(
                    f"delegation parent run does not exist: {current.parent_run_id}"
                )
            current = parent

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

        if step.command is not None:
            return "command"
        if step.delegation is not None:
            return "delegation"
        return "provider"

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
            allowed = {
                "provider",
                "content",
                "model",
                "system",
                "temperature",
                "max_tokens",
                "required_capability",
            }
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
        required_capability = values.get("required_capability")
        if provider is not None and (not isinstance(provider, str) or not provider.strip()):
            raise ValueError("step provider must be a non-empty string")
        if required_capability is not None and (
            not isinstance(required_capability, str) or not required_capability.strip()
        ):
            raise ValueError("step required capability must be a non-empty string")
        if (provider is None) == (required_capability is None):
            raise ValueError(
                "step provider message requires exactly one of provider or "
                "required_capability"
            )
        if (
            required_capability is not None
            and required_capability not in _KNOWN_PROVIDER_CAPABILITIES
        ):
            raise ValueError(
                "step required capability is not declared by any configured "
                f"provider: {required_capability}"
            )
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
            required_capability=required_capability,
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
        has_tools: bool = False,
    ) -> SandboxPolicy | None:
        if policy is None:
            return None
        if not has_command and not has_tools:
            raise ValueError(
                "sandbox policy is only valid for command steps or "
                "tool-declaring provider steps"
            )
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

    @staticmethod
    def _tool_declarations_payload(
        declarations: Sequence[ToolDeclaration],
    ) -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        for declaration in declarations:
            payload: dict[str, object] = {
                "name": declaration.name,
                "command": list(declaration.command),
            }
            if declaration.description is not None:
                payload["description"] = declaration.description
            if declaration.parameters is not None:
                payload["parameters"] = dict(declaration.parameters)
            payloads.append(payload)
        return payloads

    @staticmethod
    def _validate_tool_declarations(
        tools: Sequence[ToolDeclaration | Mapping[str, object]] | object | None,
        *,
        has_message: bool,
        sandbox_policy: SandboxPolicy | None,
    ) -> tuple[ToolDeclaration, ...]:
        if tools is None:
            return ()
        if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence):
            raise ValueError("step tool declarations must be a sequence")
        if not tools:
            return ()
        if not has_message:
            raise ValueError(
                "tool declarations are only valid for provider-message steps"
            )
        if sandbox_policy is None:
            raise ValueError("tool declarations require a persisted sandbox policy")
        normalized: list[ToolDeclaration] = []
        seen_names: set[str] = set()
        for item in tools:
            if isinstance(item, ToolDeclaration):
                name = item.name
                command = item.command
                description = item.description
                parameters = item.parameters
            elif isinstance(item, Mapping):
                allowed = {"name", "command", "description", "parameters"}
                if set(item) - allowed:
                    raise ValueError("step tool declaration has unknown fields")
                name = item.get("name")
                command = item.get("command")
                description = item.get("description")
                parameters = item.get("parameters")
            else:
                raise ValueError("step tool declaration must be an object")
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError(
                    "step tool declaration name must be a valid identifier"
                )
            if name in seen_names:
                raise ValueError(f"step tool declaration name is not unique: {name}")
            seen_names.add(name)
            if (
                command is None
                or isinstance(command, (str, bytes))
                or not isinstance(command, Sequence)
            ):
                raise ValueError(
                    "step tool declaration command must be a sequence of arguments"
                )
            normalized_command = tuple(command)
            if not normalized_command or any(
                not isinstance(argument, str) or not argument
                for argument in normalized_command
            ):
                raise ValueError(
                    "step tool declaration command arguments must be non-empty strings"
                )
            if description is not None and (
                not isinstance(description, str) or not description.strip()
            ):
                raise ValueError(
                    "step tool declaration description must be a non-empty string"
                )
            if parameters is not None:
                if not isinstance(parameters, Mapping) or any(
                    not isinstance(key, str) for key in parameters
                ):
                    raise ValueError(
                        "step tool declaration parameters must be a JSON object"
                    )
                parameters = dict(parameters)
            normalized.append(
                ToolDeclaration(
                    name=name,
                    command=normalized_command,
                    description=description,
                    parameters=parameters,
                )
            )
        return tuple(normalized)

    @staticmethod
    def _validate_tool_iteration_budget(
        budget: object,
        *,
        has_tools: bool,
        require_explicit: bool,
    ) -> int | None:
        """Validate a step's explicit maximum tool-iteration count.

        ``require_explicit`` distinguishes creating a new tool-declaring step
        (which must state a budget before any mutation, per the milestone's
        "no default unlimited loop" contract) from reading back an already
        durable record (where a tool-declaring step persisted before this
        budget existed must still load rather than fail closed).
        """

        if not has_tools:
            if budget is not None:
                raise ValueError(
                    "tool iteration budget is only valid for tool-declaring "
                    "provider steps"
                )
            return None
        if budget is None:
            if require_explicit:
                raise ValueError(
                    "tool-declaring provider steps require an explicit "
                    "maximum tool-iteration budget"
                )
            return None
        if (
            not isinstance(budget, int)
            or isinstance(budget, bool)
            or budget < 1
        ):
            raise ValueError("tool iteration budget must be a positive integer")
        return budget

    @staticmethod
    def _tool_call_payload(record: ToolCallRecord) -> dict[str, object]:
        payload: dict[str, object] = {
            "tool_name": record.tool_name,
            "arguments": dict(record.arguments),
            "phase": record.phase.value,
        }
        if record.call_id is not None:
            payload["call_id"] = record.call_id
        if record.command is not None:
            payload["command"] = list(record.command)
        if record.exit_code is not None:
            payload["exit_code"] = record.exit_code
        if record.stdout is not None:
            payload["stdout"] = record.stdout
        if record.stderr is not None:
            payload["stderr"] = record.stderr
        return payload

    @staticmethod
    def _chat_response_payload(response: ChatResponse) -> dict[str, object]:
        """Return the complete normalized response evidence for one tool iteration."""

        payload: dict[str, object] = {
            "content": response.content,
            "model": response.model,
            "usage": {
                "available": response.usage.available,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "raw": None if response.usage.raw is None else dict(response.usage.raw),
                "unavailable_reason": response.usage.unavailable_reason,
            },
        }
        if response.raw is not None:
            payload["raw"] = dict(response.raw)
        return payload

    @staticmethod
    def _tool_iterations_payload(
        iterations: Sequence[ToolIterationRecord],
    ) -> list[dict[str, object]]:
        return [
            {
                "response": dict(iteration.response),
                "tool_call": RunCoordinator._tool_call_payload(iteration.tool_call),
            }
            for iteration in iterations
        ]

    @staticmethod
    def _add_tool_iterations_payload(
        payload: dict[str, object], step: RunStep
    ) -> None:
        if step.tool_iterations:
            payload["tool_iterations"] = RunCoordinator._tool_iterations_payload(
                step.tool_iterations
            )

    @staticmethod
    def _validate_tool_iterations(
        value: object | None,
        *,
        legacy_tool_call: object | None,
        output: Mapping[str, object] | None,
        tool_declarations: Sequence[ToolDeclaration],
    ) -> tuple[ToolIterationRecord, ...]:
        """Validate ordered iteration evidence and upgrade legacy single-call records."""

        if value is None:
            if legacy_tool_call is None:
                return ()
            response = {
                "content": "" if output is None else output.get("content", ""),
                "model": None if output is None else output.get("model"),
                "usage": {} if output is None else output.get("usage", {}),
            }
            if output is not None and "raw" in output:
                response["raw"] = output["raw"]
            return (
                ToolIterationRecord(
                    response=RunCoordinator._validate_iteration_response(response),
                    tool_call=RunCoordinator._validate_tool_call(
                        legacy_tool_call, tool_declarations=tool_declarations
                    ),
                ),
            )
        if legacy_tool_call is not None:
            raise ValueError("step record has both legacy and ordered tool-call evidence")
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError("step tool iterations must be a sequence")
        normalized = []
        for item in value:
            if not isinstance(item, Mapping) or set(item) != {"response", "tool_call"}:
                raise ValueError("step tool iteration must contain response and tool_call")
            response = RunCoordinator._validate_iteration_response(item["response"])
            call = RunCoordinator._validate_tool_call(
                item["tool_call"], tool_declarations=tool_declarations
            )
            normalized.append(ToolIterationRecord(response=response, tool_call=call))
        if any(
            iteration.tool_call.phase is not ToolCallPhase.EXECUTED
            for iteration in normalized[:-1]
        ):
            raise ValueError("only the final tool iteration may be incomplete")
        return tuple(normalized)

    @staticmethod
    def _validate_iteration_response(value: object) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("tool iteration response must be an object")
        allowed = {"content", "model", "raw", "usage"}
        if set(value) - allowed:
            raise ValueError("tool iteration response has unknown fields")
        content = value.get("content")
        model = value.get("model")
        raw = value.get("raw")
        usage = value.get("usage", {})
        if not isinstance(content, str):
            raise ValueError("tool iteration response content must be a string")
        if model is not None and not isinstance(model, str):
            raise ValueError("tool iteration response model must be a string")
        if raw is not None and not isinstance(raw, Mapping):
            raise ValueError("tool iteration response raw evidence must be an object")
        if not isinstance(usage, Mapping):
            raise ValueError("tool iteration response usage must be an object")
        normalized: dict[str, object] = {
            "content": content,
            "model": model,
            "usage": dict(usage),
        }
        if raw is not None:
            normalized["raw"] = dict(raw)
        return normalized

    @staticmethod
    def _validate_tool_call(
        value: ToolCallRecord | Mapping[str, object] | object | None,
        *,
        tool_declarations: Sequence[ToolDeclaration],
    ) -> ToolCallRecord | None:
        if value is None:
            return None
        if isinstance(value, ToolCallRecord):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("step tool call must be an object")
        allowed = {
            "tool_name", "arguments", "call_id", "phase", "command",
            "exit_code", "stdout", "stderr",
        }
        if set(value) - allowed:
            raise ValueError("step tool call has unknown fields")
        tool_name = value.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("step tool call name must be a non-empty string")
        arguments = value.get("arguments")
        if not isinstance(arguments, Mapping):
            raise ValueError("step tool call arguments must be a JSON object")
        call_id = value.get("call_id")
        if call_id is not None and not isinstance(call_id, str):
            raise ValueError("step tool call id must be a string")
        try:
            phase = ToolCallPhase(value.get("phase"))
        except (TypeError, ValueError) as error:
            raise ValueError("step tool call phase is invalid") from error
        declared = any(
            declaration.name == tool_name for declaration in tool_declarations
        )
        if not declared and phase is not ToolCallPhase.REJECTED_UNDECLARED:
            raise ValueError(
                f"step tool call does not match a declared tool: {tool_name}"
            )
        if declared and phase is ToolCallPhase.REJECTED_UNDECLARED:
            raise ValueError("declared tool call cannot be rejected as undeclared")
        command = value.get("command")
        if command is not None:
            if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
                raise ValueError("step tool call command must be a sequence of arguments")
            command = tuple(command)
        exit_code = value.get("exit_code")
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)
        ):
            raise ValueError("step tool call exit code must be an integer")
        if phase is ToolCallPhase.EXECUTED and (command is None or exit_code is None):
            raise ValueError("executed tool call requires a command and exit code")
        stdout = value.get("stdout")
        stderr = value.get("stderr")
        if stdout is not None and not isinstance(stdout, str):
            raise ValueError("step tool call stdout must be a string")
        if stderr is not None and not isinstance(stderr, str):
            raise ValueError("step tool call stderr must be a string")
        return ToolCallRecord(
            tool_name=tool_name,
            arguments=dict(arguments),
            call_id=call_id,
            phase=phase,
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    @staticmethod
    def _artifact_declarations_payload(
        declarations: Sequence[ArtifactDeclaration],
    ) -> list[dict[str, object]]:
        return [{"name": declaration.name, "path": declaration.path} for declaration in declarations]

    @staticmethod
    def _validate_artifact_declarations(
        declarations: Sequence[ArtifactDeclaration | Mapping[str, object]] | object | None,
        *,
        sandbox_policy: SandboxPolicy | None,
    ) -> tuple[ArtifactDeclaration, ...]:
        if declarations is None:
            return ()
        if isinstance(declarations, (str, bytes)) or not isinstance(declarations, Sequence):
            raise ValueError("step artifact declarations must be a sequence")
        if not declarations:
            return ()
        if sandbox_policy is None:
            raise ValueError("artifact declarations require a persisted sandbox policy")
        if not sandbox_policy.mounts:
            raise ValueError(
                "artifact declarations require at least one persisted sandbox mount"
            )
        boundaries = tuple(
            posixpath.normpath(container_path) for _, container_path in sandbox_policy.mounts
        )
        normalized: list[ArtifactDeclaration] = []
        seen_names: set[str] = set()
        for item in declarations:
            if isinstance(item, ArtifactDeclaration):
                name, path = item.name, item.path
            elif isinstance(item, Mapping):
                allowed = {"name", "path"}
                if set(item) - allowed:
                    raise ValueError("step artifact declaration has unknown fields")
                name, path = item.get("name"), item.get("path")
            else:
                raise ValueError("step artifact declaration must be an object")
            if (
                not isinstance(name, str)
                or not name
                or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None
            ):
                raise ValueError(
                    "step artifact name must be a non-empty identifier-safe string"
                )
            if name in seen_names:
                raise ValueError(f"step artifact name must be unique: {name}")
            seen_names.add(name)
            if not isinstance(path, str) or not path.strip() or not posixpath.isabs(path):
                raise ValueError("step artifact path must be a non-empty absolute path")
            normalized_path = posixpath.normpath(path)
            if not any(
                normalized_path == boundary
                or normalized_path.startswith(boundary.rstrip("/") + "/")
                for boundary in boundaries
            ):
                raise ValueError(
                    "step artifact path does not resolve within a persisted sandbox "
                    f"mount: {path}"
                )
            normalized.append(ArtifactDeclaration(name=name, path=normalized_path))
        return tuple(normalized)

    @staticmethod
    def _validate_response_artifact_name(
        name: object | None, *, has_message: bool
    ) -> str | None:
        if name is None:
            return None
        if not has_message:
            raise ValueError("response artifact declarations require a provider message")
        if (
            not isinstance(name, str)
            or not name
            or re.fullmatch(r"[A-Za-z0-9_-]+", name) is None
        ):
            raise ValueError(
                "response artifact name must be a non-empty identifier-safe string"
            )
        return name

    @staticmethod
    def _resolve_artifact_host_path(
        policy: SandboxPolicy, declared_path: str
    ) -> Path | None:
        """Map a declared container path to its host-side path through persisted mounts."""

        normalized_declared = posixpath.normpath(declared_path)
        for host_path, container_path in policy.mounts:
            normalized_container = posixpath.normpath(container_path)
            if normalized_declared == normalized_container:
                return Path(host_path)
            prefix = normalized_container.rstrip("/") + "/"
            if normalized_declared.startswith(prefix):
                return Path(host_path) / normalized_declared[len(prefix):]
        return None

    def _capture_artifacts(self, step: RunStep) -> None:
        """Capture, mark absent, or reject each declared artifact after a successful command.

        Never changes the command step's own success outcome: absence and
        size-limit rejection are recorded as durable artifact evidence, not
        step failures.
        """

        if not step.artifact_declarations:
            return
        assert step.sandbox_policy is not None  # enforced by _validate_artifact_declarations
        for declaration in step.artifact_declarations:
            host_path = self._resolve_artifact_host_path(step.sandbox_policy, declaration.path)
            if host_path is None or not host_path.is_file():
                status = ArtifactStatus.ABSENT
                data = None
                size_bytes = None
                size_limit_bytes = None
            else:
                size = host_path.stat().st_size
                if size > self._artifact_size_limit_bytes:
                    status = ArtifactStatus.REJECTED
                    data = None
                    size_bytes = size
                    size_limit_bytes = self._artifact_size_limit_bytes
                else:
                    data = host_path.read_bytes()
                    status = ArtifactStatus.CAPTURED
                    size_bytes = len(data)
                    size_limit_bytes = None
            self._persist_artifact(
                step,
                name=declaration.name,
                source_path=declaration.path,
                execution_kind="command",
                status=status,
                data=data,
                size_bytes=size_bytes,
                size_limit_bytes=size_limit_bytes,
            )

    def _capture_response_artifact(self, step: RunStep, content: str) -> None:
        """Capture normalized provider response content through artifact storage."""

        assert step.response_artifact_name is not None
        data = content.encode("utf-8")
        if len(data) > self._artifact_size_limit_bytes:
            status = ArtifactStatus.REJECTED
            stored_data = None
            size_limit_bytes = self._artifact_size_limit_bytes
        else:
            status = ArtifactStatus.CAPTURED
            stored_data = data
            size_limit_bytes = None
        self._persist_artifact(
            step,
            name=step.response_artifact_name,
            source_path="response.content",
            execution_kind="provider",
            status=status,
            data=stored_data,
            size_bytes=len(data),
            size_limit_bytes=size_limit_bytes,
        )

    def _persist_artifact(
        self,
        step: RunStep,
        *,
        name: str,
        source_path: str,
        execution_kind: str,
        status: ArtifactStatus,
        data: bytes | None,
        size_bytes: int | None,
        size_limit_bytes: int | None,
    ) -> None:
        """Persist one artifact outcome through the shared local storage contract."""

        artifact_id = f"{step.step_id}-artifact-{name}"
        content_hash = None
        if status is ArtifactStatus.CAPTURED:
            assert data is not None
            content_hash = hashlib.sha256(data).hexdigest()
            self._artifact_storage_dir.mkdir(parents=True, exist_ok=True)
            (self._artifact_storage_dir / artifact_id).write_bytes(data)
        payload: dict[str, object] = {
            "run_id": step.run_id,
            "step_id": step.step_id,
            "name": name,
            "source_path": source_path,
        }
        if content_hash is not None:
            payload["content_hash"] = content_hash
        if size_bytes is not None:
            payload["size_bytes"] = size_bytes
        if size_limit_bytes is not None:
            payload["size_limit_bytes"] = size_limit_bytes
        self.store.insert(
            "artifact",
            artifact_id,
            status=status.value,
            payload=payload,
            history=(
                RunHistoryEntry(
                    step.run_id,
                    0,
                    f"artifact_{status.value}",
                    status.value,
                    step_id=step.step_id,
                    execution_kind=execution_kind,
                    artifact_name=name,
                ),
            ),
        )

    @staticmethod
    def _artifact(record: StateRecord) -> ArtifactRecord:
        run_id = record.payload.get("run_id")
        step_id = record.payload.get("step_id")
        name = record.payload.get("name")
        source_path = record.payload.get("source_path")
        content_hash = record.payload.get("content_hash")
        size_bytes = record.payload.get("size_bytes")
        size_limit_bytes = record.payload.get("size_limit_bytes")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(f"artifact record has invalid run id: {record.key}")
        if not isinstance(step_id, str) or not step_id:
            raise ValueError(f"artifact record has invalid step id: {record.key}")
        if not isinstance(name, str) or not name:
            raise ValueError(f"artifact record has invalid name: {record.key}")
        if not isinstance(source_path, str) or not source_path:
            raise ValueError(f"artifact record has invalid source path: {record.key}")
        if content_hash is not None and not isinstance(content_hash, str):
            raise ValueError(f"artifact record has invalid content hash: {record.key}")
        if size_bytes is not None and (
            not isinstance(size_bytes, int) or isinstance(size_bytes, bool)
        ):
            raise ValueError(f"artifact record has invalid size: {record.key}")
        if size_limit_bytes is not None and (
            not isinstance(size_limit_bytes, int) or isinstance(size_limit_bytes, bool)
        ):
            raise ValueError(f"artifact record has invalid size limit: {record.key}")
        try:
            status = ArtifactStatus(record.status)
        except ValueError as error:
            raise ValueError(f"artifact record has invalid status: {record.key}") from error
        return ArtifactRecord(
            artifact_id=record.key,
            run_id=run_id,
            step_id=step_id,
            name=name,
            source_path=source_path,
            status=status,
            content_hash=content_hash,
            size_bytes=size_bytes,
            size_limit_bytes=size_limit_bytes,
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
