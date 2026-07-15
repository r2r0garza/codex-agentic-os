from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from codex_agentic_os.chat import ChatResponse
from codex_agentic_os.runtime import (
    AgentRegistry,
    ProviderMessage,
    RunCoordinator,
    RunStatus,
    StepStatus,
)
from codex_agentic_os.sandboxes import SandboxResult
from codex_agentic_os.state import StateStore
from codex_agentic_os.worker import register_or_resume_agent, run_worker


class _Clock:
    """Deterministic injectable clock advancing only when told to."""

    def __init__(self, start: datetime) -> None:
        self.moment = start

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


class _CountingSleeper:
    """Records sleep calls and advances an associated clock, for cadence tests."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


def _bounded_should_continue(iterations: int):
    remaining = [iterations]

    def should_continue() -> bool:
        if remaining[0] <= 0:
            return False
        remaining[0] -= 1
        return True

    return should_continue


class _Executor:
    def execute(self, argv, *, timeout=None):
        return SandboxResult(tuple(argv), 0, "ok", "")


class _Adapter:
    def complete(self, request):
        return ChatResponse("synthesized", model="served-model")


def test_register_or_resume_agent_creates_new_identity(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    registry = AgentRegistry(store)

    agent = register_or_resume_agent(registry, "agent-1", label="Worker")

    assert agent.agent_id == "agent-1"
    assert agent.label == "Worker"
    assert agent.revision == 1


def test_register_or_resume_agent_heartbeats_existing_identity(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    registry = AgentRegistry(store)
    original = registry.register("agent-1", label="Worker")

    resumed = register_or_resume_agent(registry, "agent-1")

    assert resumed.agent_id == "agent-1"
    assert resumed.label == "Worker"
    assert resumed.revision == original.revision + 1


@pytest.mark.parametrize(
    ("heartbeat_interval", "poll_interval"),
    [(0, 5), (-1, 5), (5, 0), (5, -1)],
)
def test_run_worker_rejects_non_positive_intervals_without_mutation(
    tmp_path, heartbeat_interval, poll_interval
) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)

    with pytest.raises(ValueError, match="positive number of seconds"):
        run_worker(
            coordinator,
            registry,
            "agent-1",
            heartbeat_interval=heartbeat_interval,
            poll_interval=poll_interval,
            should_continue=_bounded_should_continue(1),
        )

    assert registry.list_agents() == ()


def test_run_worker_claims_assigned_run_and_executes_steps_in_order(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Deliver", agent_id="agent-1")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step(
        "run-1",
        "second",
        objective="Second",
        message=ProviderMessage("local", "Use the results"),
    )

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        adapter_resolver=lambda _: _Adapter(),
        should_continue=_bounded_should_continue(3),
    )

    assert summary.claimed_run_ids == ("run-1",)
    assert summary.executed_step_ids == ("first", "second")
    run = coordinator.get("run-1")
    assert run is not None
    assert run.status is RunStatus.SUCCEEDED
    first = coordinator.get_step("first")
    second = coordinator.get_step("second")
    assert first is not None and first.status is StepStatus.SUCCEEDED
    assert second is not None and second.status is StepStatus.SUCCEEDED


def test_run_worker_claims_next_unassigned_eligible_run(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Deliver")
    coordinator.add_step("run-1", "only", objective="Only", command=("true",))

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        should_continue=_bounded_should_continue(2),
    )

    assert summary.claimed_run_ids == ("run-1",)
    assert summary.executed_step_ids == ("only",)
    run = coordinator.get("run-1")
    assert run is not None
    assert run.status is RunStatus.SUCCEEDED
    assert run.agent_id == "agent-1"


def test_run_worker_prefers_previously_assigned_run_over_unassigned_run(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-unassigned", objective="Elsewhere")
    coordinator.add_step(
        "run-unassigned", "unassigned-only", objective="Only", command=("true",)
    )
    coordinator.create("run-assigned", objective="Deliver", agent_id="agent-1")
    coordinator.add_step(
        "run-assigned", "assigned-only", objective="Only", command=("true",)
    )

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        should_continue=_bounded_should_continue(2),
    )

    assert summary.claimed_run_ids == ("run-assigned",)


def test_run_worker_refreshes_heartbeat_on_configured_cadence(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = _Clock(start)
    sleeper = _CountingSleeper(clock)

    run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=10,
        poll_interval=5,
        clock=clock,
        sleeper=sleeper,
        should_continue=_bounded_should_continue(3),
    )

    agent = registry.get("agent-1")
    assert agent is not None
    assert agent.revision == 2
    assert sleeper.calls == [5, 5, 5]

    clock.advance(10)
    run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=10,
        poll_interval=5,
        clock=clock,
        sleeper=sleeper,
        should_continue=_bounded_should_continue(1),
    )
    agent = registry.get("agent-1")
    assert agent is not None
    assert agent.revision == 3


def test_run_worker_idles_without_busy_spinning_when_no_work_available(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = _Clock(start)
    sleeper = _CountingSleeper(clock)

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=2.5,
        clock=clock,
        sleeper=sleeper,
        should_continue=_bounded_should_continue(3),
    )

    assert summary.claimed_run_ids == ()
    assert summary.executed_step_ids == ()
    assert sleeper.calls == [2.5, 2.5, 2.5]


def test_run_worker_skips_approval_blocked_run_and_executes_another_eligible_run(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-blocked", objective="Needs approval", agent_id="agent-1")
    coordinator.add_step(
        "run-blocked",
        "gated",
        objective="Gated",
        command=("true",),
        approval_required=True,
    )
    coordinator.create("run-ready", objective="Deliver", agent_id="agent-1")
    coordinator.add_step("run-ready", "only", objective="Only", command=("true",))

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        should_continue=_bounded_should_continue(4),
    )

    assert "run-ready" in summary.claimed_run_ids
    assert summary.executed_step_ids == ("only",)
    ready_run = coordinator.get("run-ready")
    assert ready_run is not None and ready_run.status is RunStatus.SUCCEEDED
    blocked_run = coordinator.get("run-blocked")
    assert blocked_run is not None and blocked_run.status is RunStatus.QUEUED
    gated_step = coordinator.get_step("gated")
    assert gated_step is not None and gated_step.status is StepStatus.QUEUED


def test_run_worker_skips_unresolved_context_reference_step_without_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Deliver", agent_id="agent-1")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step(
        "run-1",
        "referencing",
        objective="Referencing",
        message=ProviderMessage("local", "Use the results"),
        context_step_ids=("first",),
    )
    coordinator.cancel_step("first")

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        adapter_resolver=lambda _: _Adapter(),
        should_continue=_bounded_should_continue(2),
    )

    assert summary.executed_step_ids == ()
    referencing = coordinator.get_step("referencing")
    assert referencing is not None and referencing.status is StepStatus.QUEUED
    first = coordinator.get_step("first")
    assert first is not None and first.status is StepStatus.CANCELLED
    run = coordinator.get("run-1")
    assert run is not None and run.status is RunStatus.QUEUED


def test_run_worker_idles_deterministically_when_only_eligible_run_is_blocked(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-blocked", objective="Needs approval", agent_id="agent-1")
    coordinator.add_step(
        "run-blocked",
        "gated",
        objective="Gated",
        command=("true",),
        approval_required=True,
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = _Clock(start)
    sleeper = _CountingSleeper(clock)

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=2,
        executor=_Executor(),
        clock=clock,
        sleeper=sleeper,
        should_continue=_bounded_should_continue(9),
    )

    assert summary.executed_step_ids == ()
    assert sleeper.calls == [2, 2, 2]
    blocked_run = coordinator.get("run-blocked")
    assert blocked_run is not None and blocked_run.status is RunStatus.QUEUED


def test_run_worker_resumes_blocked_run_on_a_later_poll_cycle(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Needs approval", agent_id="agent-1")
    step = coordinator.add_step(
        "run-1",
        "gated",
        objective="Gated",
        command=("true",),
        approval_required=True,
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = _Clock(start)
    sleeper = _CountingSleeper(clock)

    # First bounded run: only blocked work exists, so the worker idles.
    run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=2,
        executor=_Executor(),
        clock=clock,
        sleeper=sleeper,
        should_continue=_bounded_should_continue(3),
    )
    assert sleeper.calls == [2]
    blocked_run = coordinator.get("run-1")
    assert blocked_run is not None and blocked_run.status is RunStatus.QUEUED

    # Approval arrives out of band between worker invocations.
    coordinator.approve_step(step.step_id)

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=2,
        executor=_Executor(),
        clock=clock,
        sleeper=sleeper,
        should_continue=_bounded_should_continue(2),
    )

    assert summary.executed_step_ids == ("gated",)
    run = coordinator.get("run-1")
    assert run is not None and run.status is RunStatus.SUCCEEDED


def test_run_worker_command_step_uses_persisted_sandbox_policy_resolver(
    tmp_path,
) -> None:
    from codex_agentic_os.runtime import SandboxPolicy
    from codex_agentic_os.sandboxes import SandboxKind

    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Deliver", agent_id="agent-1")
    coordinator.add_step(
        "run-1",
        "only",
        objective="Only",
        command=("true",),
        sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
    )

    seen_policies = []

    def resolver(policy):
        seen_policies.append(policy)
        return _Executor()

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        sandbox_resolver=resolver,
        should_continue=_bounded_should_continue(2),
    )

    assert summary.executed_step_ids == ("only",)
    assert len(seen_policies) == 1
    assert seen_policies[0].kind is SandboxKind.DOCKER


def test_run_worker_command_step_without_persisted_policy_fails_without_ad_hoc_executor(
    tmp_path,
) -> None:
    """A worker never invents a fallback executor for a step with no persisted policy.

    ``run_worker`` (via the real CLI wiring) only ever supplies a
    ``sandbox_resolver``, never an ``executor`` override, so a command step
    declared without a persisted ``sandbox_policy`` must fail through
    ``execute_next_step``'s existing explicit error rather than dispatching
    through any resolver-independent path.
    """

    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Deliver", agent_id="agent-1")
    coordinator.add_step("run-1", "only", objective="Only", command=("true",))

    resolver_calls = []

    def resolver(policy):
        resolver_calls.append(policy)
        return _Executor()

    with pytest.raises(ValueError, match="next command step requires a sandbox: only"):
        run_worker(
            coordinator,
            registry,
            "agent-1",
            heartbeat_interval=60,
            poll_interval=1,
            sandbox_resolver=resolver,
            should_continue=_bounded_should_continue(2),
        )

    assert resolver_calls == []
    step = coordinator.get_step("only")
    assert step is not None and step.status is StepStatus.QUEUED


def test_run_worker_executes_a_declared_tool_call_from_queued_run(tmp_path) -> None:
    from codex_agentic_os.chat import ChatToolCall
    from codex_agentic_os.runtime import SandboxPolicy, ToolDeclaration
    from codex_agentic_os.sandboxes import SandboxKind

    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-1", objective="Summarize files", agent_id="agent-1")
    coordinator.add_step(
        "run-1",
        "only",
        objective="Summarize",
        message=ProviderMessage(provider="local", content="List files"),
        sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
        tools=[ToolDeclaration(name="list_files", command=("ls", "-la"))],
        tool_iteration_budget=1,
    )

    execute_calls = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            execute_calls.append(tuple(argv))
            return SandboxResult(tuple(argv), 0, "3 files\n", "")

    class Adapter:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    "", tool_call=ChatToolCall(name="list_files", arguments={})
                )
            return ChatResponse("Found 3 files.")

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        sandbox_resolver=lambda _policy: Executor(),
        adapter_resolver=lambda _message: Adapter(),
        should_continue=_bounded_should_continue(2),
    )

    assert summary.executed_step_ids == ("only",)
    assert execute_calls == [("ls", "-la")]
    step = coordinator.get_step("only")
    assert step.status is StepStatus.SUCCEEDED
    assert step.tool_call.exit_code == 0
    assert step.output["content"] == "Found 3 files."
    run = coordinator.get("run-1")
    assert run.status is RunStatus.SUCCEEDED


def test_run_worker_dispatches_delegation_step_then_moves_on_without_crashing(
    tmp_path,
) -> None:
    from codex_agentic_os.runtime import DelegationSpec

    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    coordinator.create("run-delegating", objective="Delegate", agent_id="agent-1")
    coordinator.add_step(
        "run-delegating",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.create("run-ready", objective="Deliver", agent_id="agent-1")
    coordinator.add_step("run-ready", "only", objective="Only", command=("true",))

    summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        should_continue=_bounded_should_continue(6),
    )

    assert "delegate" in summary.executed_step_ids
    assert "only" in summary.executed_step_ids
    delegate_step = coordinator.get_step("delegate")
    assert delegate_step is not None and delegate_step.status is StepStatus.RUNNING
    assert delegate_step.delegated_run_id == "delegate-child"
    child = coordinator.get("delegate-child")
    assert child is not None and child.status is RunStatus.QUEUED
    ready_run = coordinator.get("run-ready")
    assert ready_run is not None and ready_run.status is RunStatus.SUCCEEDED


def test_workers_complete_parent_after_target_agent_finishes_delegated_child(
    tmp_path,
) -> None:
    from codex_agentic_os.runtime import DelegationSpec

    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = RunCoordinator(store)
    registry = AgentRegistry(store)
    registry.register("agent-1")
    registry.register("agent-2")
    coordinator.create("run-parent", objective="Delegate", agent_id="agent-1")
    coordinator.add_step(
        "run-parent",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(
            child_objective="Review the change", target_agent_id="agent-2"
        ),
    )
    coordinator.execute_next_step("run-parent")
    coordinator.add_step(
        "delegate-child", "review", objective="Review", command=("true",)
    )

    child_summary = run_worker(
        coordinator,
        registry,
        "agent-2",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        should_continue=_bounded_should_continue(3),
    )
    parent_summary = run_worker(
        coordinator,
        registry,
        "agent-1",
        heartbeat_interval=60,
        poll_interval=1,
        executor=_Executor(),
        should_continue=_bounded_should_continue(3),
    )

    assert "review" in child_summary.executed_step_ids
    assert coordinator.get("delegate-child").status is RunStatus.SUCCEEDED
    assert "delegate" in parent_summary.executed_step_ids
    assert coordinator.get_step("delegate").status is StepStatus.SUCCEEDED
    assert coordinator.get("run-parent").status is RunStatus.SUCCEEDED
