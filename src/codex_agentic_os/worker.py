"""Foreground worker loop: durable identity heartbeat and claim-execute iteration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .providers import DEFAULT_PROVIDER_ROUTING_POLICY, ProviderRoutingPolicy
from .runtime import (
    Agent,
    AgentRegistry,
    AgentRun,
    ApprovalRequiredError,
    ChatAdapterResolver,
    ContextReferencesUnresolvedError,
    RunCoordinator,
    RunStatus,
    SandboxExecutor,
    SandboxPolicyResolver,
)

_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class WorkerRunSummary:
    """Deterministic record of one bounded worker loop invocation."""

    agent_id: str
    claimed_run_ids: tuple[str, ...]
    executed_step_ids: tuple[str, ...]


def register_or_resume_agent(
    registry: AgentRegistry, agent_id: str, *, label: str | None = None
) -> Agent:
    """Register a new durable agent identity, or heartbeat an already-registered one."""

    try:
        return registry.register(agent_id, label=label)
    except ValueError as error:
        if str(error) != f"agent already exists: {agent_id}":
            raise
        return registry.heartbeat(agent_id)


def _claim_eligible_run(
    coordinator: RunCoordinator,
    agent_id: str,
    *,
    exclude: frozenset[str] = frozenset(),
) -> AgentRun | None:
    """Return a non-terminal run already assigned to ``agent_id``, else claim the next eligible run.

    ``exclude`` skips runs already known to be blocked on an approval or
    eligibility gate this poll cycle, so the worker can move on to other
    assigned or claimable work instead of retrying the same blocked run.
    """

    assigned = next(
        (
            run
            for run in coordinator.list_runs()
            if run.agent_id == agent_id
            and run.status not in _TERMINAL_RUN_STATUSES
            and run.run_id not in exclude
        ),
        None,
    )
    if assigned is not None:
        return assigned
    return coordinator.claim_next(agent_id)


def run_worker(
    coordinator: RunCoordinator,
    registry: AgentRegistry,
    agent_id: str,
    *,
    heartbeat_interval: float,
    poll_interval: float,
    executor: SandboxExecutor | None = None,
    sandbox_resolver: SandboxPolicyResolver | None = None,
    adapter_resolver: ChatAdapterResolver | None = None,
    routing_policy: ProviderRoutingPolicy = DEFAULT_PROVIDER_ROUTING_POLICY,
    label: str | None = None,
    sleeper: Callable[[float], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    should_continue: Callable[[], bool] = lambda: True,
) -> WorkerRunSummary:
    """Heartbeat one durable agent identity and repeatedly claim and execute run steps.

    Iterates until ``should_continue`` returns ``False``, an injection point
    that lets callers observe a deterministic, bounded number of iterations
    instead of depending on wall-clock signals or process interruption.
    """

    if sleeper is None:
        sleeper = time.sleep
    if (
        not isinstance(heartbeat_interval, (int, float))
        or isinstance(heartbeat_interval, bool)
        or heartbeat_interval <= 0
    ):
        raise ValueError("heartbeat interval must be a positive number of seconds")
    if (
        not isinstance(poll_interval, (int, float))
        or isinstance(poll_interval, bool)
        or poll_interval <= 0
    ):
        raise ValueError("poll interval must be a positive number of seconds")

    register_or_resume_agent(registry, agent_id, label=label)
    last_heartbeat = clock()
    claimed_run_ids: list[str] = []
    executed_step_ids: list[str] = []
    blocked_run_ids: set[str] = set()

    def heartbeat_if_due() -> None:
        nonlocal last_heartbeat
        now = clock()
        if (now - last_heartbeat).total_seconds() >= heartbeat_interval:
            registry.heartbeat(agent_id)
            last_heartbeat = now

    while should_continue():
        heartbeat_if_due()
        run = _claim_eligible_run(
            coordinator, agent_id, exclude=frozenset(blocked_run_ids)
        )
        if run is None:
            sleeper(poll_interval)
            blocked_run_ids.clear()
            continue
        if run.run_id not in claimed_run_ids:
            claimed_run_ids.append(run.run_id)
        progressed = False
        blocked = False
        while should_continue():
            heartbeat_if_due()
            try:
                result = coordinator.execute_next_step(
                    run.run_id,
                    executor,
                    sandbox_resolver=sandbox_resolver,
                    adapter_resolver=adapter_resolver,
                    routing_policy=routing_policy,
                )
            except (ApprovalRequiredError, ContextReferencesUnresolvedError):
                blocked = True
                break
            if result is None:
                break
            step, updated_run = result
            executed_step_ids.append(step.step_id)
            progressed = True
            if updated_run.status in _TERMINAL_RUN_STATUSES:
                break
        if blocked:
            blocked_run_ids.add(run.run_id)
            continue
        if not progressed:
            sleeper(poll_interval)
            blocked_run_ids.clear()

    return WorkerRunSummary(
        agent_id=agent_id,
        claimed_run_ids=tuple(claimed_run_ids),
        executed_step_ids=tuple(executed_step_ids),
    )
