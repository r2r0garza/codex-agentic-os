from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib

import pytest

from codex_agentic_os.chat import ChatMessage, ChatResponse, ChatUsage
from codex_agentic_os.providers import ProviderKind, ProviderRoutingPolicy, ProviderSpec
from codex_agentic_os.runtime import (
    Agent,
    AgentRegistry,
    AgentRun,
    ApprovalRequiredError,
    ApprovalStatus,
    ArtifactDeclaration,
    ArtifactStatus,
    ClaimStaleness,
    ContextReferencesUnresolvedError,
    DelegationPendingError,
    DelegationSpec,
    PLAN_PROPOSAL_SYSTEM_PROMPT,
    PlanDraft,
    PlanProposalError,
    PlanStepProposal,
    ProviderMessage,
    RunCoordinator as _RunCoordinator,
    RunStatus,
    RunStep,
    SandboxPolicy,
    StepFailureKind,
    StepRecoveryReason,
    StepStatus,
)
from codex_agentic_os.sandboxes import SandboxKind, SandboxResult
from codex_agentic_os.state import StateStore


def RunCoordinator(store: StateStore) -> _RunCoordinator:
    """Build a coordinator with the registered identities used by legacy fixtures."""

    registry = AgentRegistry(store)
    for agent_id in ("agent-0", "agent-1", "agent-2", "agent-7", "agent-10"):
        if store.get("agent", agent_id) is None:
            registry.register(agent_id)
    return _RunCoordinator(store)


def test_run_lifecycle_is_durable_and_revisioned(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))

    queued = coordinator.create("run-1", objective="Index the repository", agent_id="agent-1")
    running = coordinator.transition("run-1", RunStatus.RUNNING)
    succeeded = coordinator.transition(
        "run-1", RunStatus.SUCCEEDED, output={"artifacts": 4}
    )

    assert queued == AgentRun(
        "run-1", "Index the repository", RunStatus.QUEUED, 1, "agent-1"
    )
    assert running.revision == 2
    assert RunCoordinator(StateStore(database)).get("run-1") == succeeded
    assert succeeded.output == {"artifacts": 4}
    assert succeeded.revision == 3


def test_runs_are_listed_in_stable_identifier_order(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    second = coordinator.create("run-b", objective="Second")
    first = coordinator.create("run-a", objective="First", agent_id="agent-1")

    assert coordinator.list_runs() == (first, second)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (RunStatus.QUEUED, RunStatus.SUCCEEDED),
        (RunStatus.QUEUED, RunStatus.FAILED),
        (RunStatus.SUCCEEDED, RunStatus.RUNNING),
        (RunStatus.FAILED, RunStatus.RUNNING),
        (RunStatus.CANCELLED, RunStatus.RUNNING),
    ],
)
def test_invalid_run_transitions_are_rejected(tmp_path, start, target) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / f"{start}.sqlite3"))
    coordinator.create("run-1", objective="Test transitions")
    if start is RunStatus.SUCCEEDED or start is RunStatus.FAILED:
        coordinator.transition("run-1", RunStatus.RUNNING)
        coordinator.transition("run-1", start)
    elif start is RunStatus.CANCELLED:
        coordinator.transition("run-1", start)

    with pytest.raises(ValueError, match="invalid run transition"):
        coordinator.transition("run-1", target)


def test_run_creation_and_transition_validation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(ValueError, match="objective"):
        coordinator.create("run-1", objective=" ")

    coordinator.create("run-1", objective="Valid")
    with pytest.raises(ValueError, match="already exists"):
        coordinator.create("run-1", objective="Duplicate")
    with pytest.raises(KeyError, match="does not exist"):
        coordinator.transition("missing", RunStatus.RUNNING)
    with pytest.raises(ValueError, match="output is only valid"):
        coordinator.transition("run-1", RunStatus.RUNNING, output={"early": True})


def test_run_agent_references_require_registration_without_mutation(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = _RunCoordinator(store)

    with pytest.raises(ValueError, match="agent is not registered: missing"):
        coordinator.create("rejected", objective="Work", agent_id="missing")
    assert coordinator.get("rejected") is None

    unassigned = coordinator.create("run-1", objective="Work")
    with pytest.raises(ValueError, match="agent is not registered: missing"):
        coordinator.claim("run-1", "missing")
    with pytest.raises(ValueError, match="agent is not registered: missing"):
        coordinator.claim_next("missing")
    assert coordinator.get("run-1") == unassigned

    AgentRegistry(store).register("agent-1")
    created = coordinator.create("run-2", objective="Assigned", agent_id="agent-1")
    claimed = coordinator.claim("run-1", "agent-1")
    assert created.agent_id == "agent-1"
    assert claimed.agent_id == "agent-1"


def test_run_creation_is_atomic_across_coordinators(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    first = RunCoordinator(StateStore(database))
    competing = RunCoordinator(StateStore(database))

    original = first.create("run-1", objective="Original", agent_id="agent-1")
    with pytest.raises(ValueError, match="run already exists: run-1"):
        competing.create("run-1", objective="Replacement")

    assert original.revision == 1
    assert first.get("run-1") == original


def test_claim_queued_run_persists_agent_and_revision(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    queued = coordinator.create("run-1", objective="Build feature")

    claimed = coordinator.claim("run-1", "agent-1")

    assert claimed == AgentRun(
        run_id="run-1",
        objective="Build feature",
        status=RunStatus.QUEUED,
        agent_id="agent-1",
        output=None,
        revision=queued.revision + 1,
    )
    assert RunCoordinator(StateStore(database)).get("run-1") == claimed


def test_competing_claims_preserve_first_agent(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    first = RunCoordinator(StateStore(database))
    competing = RunCoordinator(StateStore(database))
    first.create("run-1", objective="Build feature")

    claimed = first.claim("run-1", "agent-1")
    with pytest.raises(ValueError, match="run cannot be claimed"):
        competing.claim("run-1", "agent-2")

    assert competing.get("run-1") == claimed


def test_claim_rejects_invalid_runs_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    assigned = coordinator.create(
        "assigned", objective="Assigned", agent_id="agent-1"
    )
    running = coordinator.create("running", objective="Running")
    running = coordinator.transition("running", RunStatus.RUNNING)
    terminal = coordinator.create("terminal", objective="Terminal")
    terminal = coordinator.transition("terminal", RunStatus.RUNNING)
    terminal = coordinator.transition("terminal", RunStatus.SUCCEEDED)

    for run in (assigned, running, terminal):
        with pytest.raises(ValueError, match="run cannot be claimed"):
            coordinator.claim(run.run_id, "agent-2")
        assert coordinator.get(run.run_id) == run

    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.claim("missing", "agent-2")
    with pytest.raises(ValueError, match="agent id must not be empty"):
        coordinator.claim("assigned", " ")
    assert coordinator.get("assigned") == assigned


def test_evaluate_claim_staleness_boundary_uses_injected_clock_and_heartbeat(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    registered_at = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    AgentRegistry(store, clock=lambda: registered_at).register("agent-1")
    _RunCoordinator(store).create("run-1", objective="Build feature")
    _RunCoordinator(store).claim("run-1", "agent-1")

    at_threshold = _RunCoordinator(
        store, clock=lambda: datetime(2026, 7, 12, 12, 5, 0, tzinfo=timezone.utc)
    )
    fresh = at_threshold.evaluate_claim_staleness("run-1", threshold_seconds=300)
    assert fresh == ClaimStaleness(
        run_id="run-1",
        agent_id="agent-1",
        last_seen="2026-07-12T12:00:00+00:00",
        threshold_seconds=300,
        evaluated_at="2026-07-12T12:05:00+00:00",
        stale=False,
    )

    past_threshold = _RunCoordinator(
        store, clock=lambda: datetime(2026, 7, 12, 12, 5, 1, tzinfo=timezone.utc)
    )
    stale = past_threshold.evaluate_claim_staleness("run-1", threshold_seconds=300)
    assert stale.stale is True
    assert stale.evaluated_at == "2026-07-12T12:05:01+00:00"


def test_evaluate_claim_staleness_is_durable_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    AgentRegistry(
        store, clock=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    ).register("agent-1")
    _RunCoordinator(store).create("run-1", objective="Build feature")
    _RunCoordinator(store).claim("run-1", "agent-1")

    reloaded = _RunCoordinator(
        StateStore(database, read_only=True),
        clock=lambda: datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc),
    )
    evaluation = reloaded.evaluate_claim_staleness("run-1", threshold_seconds=60)

    assert evaluation.last_seen == "2026-07-12T12:00:00+00:00"
    assert evaluation.stale is True


@pytest.mark.parametrize("threshold", [0, -1, -0.5])
def test_evaluate_claim_staleness_rejects_non_positive_threshold_without_mutation(
    tmp_path, threshold
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    AgentRegistry(store).register("agent-1")
    coordinator = _RunCoordinator(store)
    coordinator.create("run-1", objective="Build feature")
    claimed = coordinator.claim("run-1", "agent-1")

    with pytest.raises(ValueError, match="threshold must be a positive"):
        coordinator.evaluate_claim_staleness("run-1", threshold_seconds=threshold)

    assert coordinator.get("run-1") == claimed


def test_evaluate_claim_staleness_rejects_missing_run(tmp_path) -> None:
    coordinator = _RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="run does not exist: missing"):
        coordinator.evaluate_claim_staleness("missing", threshold_seconds=60)


def test_evaluate_claim_staleness_rejects_unclaimed_run_without_mutation(
    tmp_path,
) -> None:
    coordinator = _RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    queued = coordinator.create("run-1", objective="Build feature")

    with pytest.raises(ValueError, match="run is not claimed: run-1"):
        coordinator.evaluate_claim_staleness("run-1", threshold_seconds=60)

    assert coordinator.get("run-1") == queued


def test_evaluate_claim_staleness_rejects_unregistered_owner_without_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    created = store.insert(
        "run",
        "run-1",
        status=RunStatus.QUEUED,
        payload={"objective": "Build feature", "agent_id": "ghost-agent"},
    )
    coordinator = _RunCoordinator(store)

    with pytest.raises(ValueError, match="agent is not registered: ghost-agent"):
        coordinator.evaluate_claim_staleness("run-1", threshold_seconds=60)

    assert coordinator.get("run-1").revision == created.revision


def test_evaluate_claim_staleness_rejects_legacy_agent_without_heartbeat(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("agent", "legacy-agent", status="registered", payload={})
    coordinator = _RunCoordinator(store)
    created = coordinator.create(
        "run-1", objective="Build feature", agent_id="legacy-agent"
    )

    with pytest.raises(
        ValueError, match="agent has no recorded heartbeat: legacy-agent"
    ):
        coordinator.evaluate_claim_staleness("run-1", threshold_seconds=60)

    assert coordinator.get("run-1") == created


def test_evaluate_claim_staleness_rejects_naive_last_seen_without_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert(
        "agent",
        "naive-agent",
        status="registered",
        payload={"last_seen": "2026-07-12T12:00:00"},
    )
    coordinator = _RunCoordinator(store)
    created = coordinator.create(
        "run-1", objective="Build feature", agent_id="naive-agent"
    )

    with pytest.raises(ValueError, match="ambiguous last_seen"):
        coordinator.evaluate_claim_staleness("run-1", threshold_seconds=60)

    assert coordinator.get("run-1") == created


def test_evaluate_claim_staleness_rejects_malformed_last_seen_without_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert(
        "agent",
        "broken-agent",
        status="registered",
        payload={"last_seen": "not-a-timestamp"},
    )
    coordinator = _RunCoordinator(store)
    created = coordinator.create(
        "run-1", objective="Build feature", agent_id="broken-agent"
    )

    with pytest.raises(ValueError, match="invalid last_seen"):
        coordinator.evaluate_claim_staleness("run-1", threshold_seconds=60)

    assert coordinator.get("run-1") == created


def test_reassign_stale_claim_is_durable_and_compare_and_swap_safe(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    heartbeat = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    registry = AgentRegistry(store, clock=lambda: heartbeat)
    for agent_id in ("old", "replacement-1", "replacement-2"):
        registry.register(agent_id)
    setup = _RunCoordinator(store)
    run = setup.create("run-1", objective="Build", agent_id="old")
    coordinators = tuple(
        _RunCoordinator(
            StateStore(database),
            clock=lambda: datetime(2026, 7, 12, 12, 5, 1, tzinfo=timezone.utc),
        )
        for _ in range(2)
    )

    def attempt(coordinator, replacement):
        try:
            return coordinator.reassign_stale_claim(
                "run-1", replacement, expected_agent_id="old",
                expected_revision=run.revision, threshold_seconds=300,
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, coordinators, ("replacement-1", "replacement-2")))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert _RunCoordinator(StateStore(database)).get("run-1") == winners[0]
    assert len([entry for entry in setup.list_history("run-1") if entry.transition == "claim_reassigned"]) == 1


def test_reassign_stale_claim_rejects_heartbeat_refresh_and_unregistered_replacement(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    registry = AgentRegistry(
        store, clock=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    )
    registry.register("old")
    coordinator = _RunCoordinator(
        store, clock=lambda: datetime(2026, 7, 12, 12, 10, tzinfo=timezone.utc)
    )
    run = coordinator.create("run-1", objective="Build", agent_id="old")
    registry = AgentRegistry(
        store, clock=lambda: datetime(2026, 7, 12, 12, 9, tzinfo=timezone.utc)
    )
    registry.heartbeat("old")

    for replacement in ("missing", "old"):
        with pytest.raises(ValueError):
            coordinator.reassign_stale_claim(
                "run-1", replacement, expected_agent_id="old",
                expected_revision=run.revision, threshold_seconds=300,
            )

    assert coordinator.get("run-1").agent_id == "old"
    assert all(entry.transition != "claim_reassigned" for entry in coordinator.list_history("run-1"))


def test_release_claim_clears_exact_queued_assignment(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    claimed = coordinator.create(
        "run-1", objective="Build feature", agent_id="agent-1"
    )
    unrelated = coordinator.create("run-2", objective="Other work", agent_id="agent-2")

    released = coordinator.release_claim("run-1", "agent-1")

    assert released == AgentRun(
        run_id="run-1",
        objective="Build feature",
        status=RunStatus.QUEUED,
        agent_id=None,
        output=None,
        revision=claimed.revision + 1,
    )
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == released
    assert reloaded.get("run-2") == unrelated


def test_release_claim_rejects_invalid_runs_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    unassigned = coordinator.create("unassigned", objective="Unassigned")
    mismatched = coordinator.create(
        "mismatched", objective="Mismatched", agent_id="agent-1"
    )
    running = coordinator.create("running", objective="Running", agent_id="agent-1")
    running = coordinator.transition("running", RunStatus.RUNNING)

    for run, owner in (
        (unassigned, "agent-1"),
        (mismatched, "agent-2"),
        (running, "agent-1"),
    ):
        with pytest.raises(ValueError, match="run claim cannot be released"):
            coordinator.release_claim(run.run_id, owner)
        assert coordinator.get(run.run_id) == run

    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.release_claim("missing", "agent-1")
    with pytest.raises(ValueError, match="agent id must not be empty"):
        coordinator.release_claim("mismatched", " ")
    assert coordinator.get("mismatched") == mismatched


def test_competing_release_cannot_clear_changed_claim(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    first = RunCoordinator(StateStore(database))
    competing = RunCoordinator(StateStore(database))
    claimed = first.create("run-1", objective="Build feature", agent_id="agent-1")

    released = first.release_claim("run-1", "agent-1")
    reclaimed = first.claim("run-1", "agent-2")
    with pytest.raises(ValueError, match="run claim cannot be released"):
        competing.release_claim("run-1", "agent-1")

    assert released.revision == claimed.revision + 1
    assert reclaimed.revision == released.revision + 1
    assert competing.get("run-1") == reclaimed


def test_competing_transitions_cannot_both_succeed_or_overwrite_each_other(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    creator = RunCoordinator(StateStore(database))
    creator.create("run-1", objective="Build feature")
    coordinators = (
        RunCoordinator(StateStore(database)),
        RunCoordinator(StateStore(database)),
    )

    def attempt(coordinator: RunCoordinator) -> AgentRun | ValueError:
        try:
            return coordinator.transition("run-1", RunStatus.RUNNING)
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt, coordinator) for coordinator in coordinators
        ]
        results = [future.result() for future in futures]

    successes = [result for result in results if isinstance(result, AgentRun)]
    failures = [result for result in results if isinstance(result, ValueError)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0].status is RunStatus.RUNNING
    assert successes[0].revision == 2
    assert creator.get("run-1") == successes[0]


def test_claim_next_selects_first_eligible_run_in_identifier_order(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    assigned = coordinator.create("run-a", objective="Assigned", agent_id="agent-0")
    running = coordinator.create("run-b", objective="Running")
    coordinator.transition("run-b", RunStatus.RUNNING)
    terminal = coordinator.create("run-c", objective="Terminal")
    coordinator.transition("run-c", RunStatus.RUNNING)
    coordinator.transition("run-c", RunStatus.SUCCEEDED)
    later = coordinator.create("run-z", objective="Later")
    first = coordinator.create("run-d", objective="First")

    claimed = coordinator.claim_next("agent-1")

    assert claimed == AgentRun(
        "run-d", "First", RunStatus.QUEUED, first.revision + 1, "agent-1"
    )
    assert coordinator.get("run-a") == assigned
    assert coordinator.get("run-z") == later


def test_competing_claim_next_calls_claim_distinct_runs(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    first = RunCoordinator(StateStore(database))
    competing = RunCoordinator(StateStore(database))
    first.create("run-a", objective="First")
    first.create("run-b", objective="Second")

    first_claim = first.claim_next("agent-1")
    second_claim = competing.claim_next("agent-2")

    assert first_claim is not None and first_claim.run_id == "run-a"
    assert second_claim is not None and second_claim.run_id == "run-b"
    assert competing.get("run-a") == first_claim


def test_claim_next_empty_queue_and_invalid_agent_do_not_mutate(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    assigned = coordinator.create("run-a", objective="Assigned", agent_id="agent-0")

    assert coordinator.claim_next("agent-1") is None
    with pytest.raises(ValueError, match="agent id must not be empty"):
        coordinator.claim_next(" ")

    assert coordinator.get("run-a") == assigned


def test_ordered_steps_are_durable_and_revisioned(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Build feature")

    first = coordinator.add_step(
        "run-1", "test", objective="Write tests", command=("true",)
    )
    second = coordinator.add_step(
        "run-1", "code", objective="Implement feature", command=("true",)
    )
    coordinator.transition_step("test", StepStatus.RUNNING)
    completed = coordinator.transition_step(
        "test", StepStatus.SUCCEEDED, output={"tests": 3}
    )

    assert first == RunStep(
        "test", "run-1", 1, "Write tests", StepStatus.QUEUED, 1, command=("true",)
    )
    assert second.position == 2
    assert completed.revision == 3
    assert completed.output == {"tests": 3}
    assert RunCoordinator(StateStore(database)).list_steps("run-1") == (
        completed,
        second,
    )


def test_competing_step_transitions_cannot_both_succeed_or_mutate_family(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    creator = RunCoordinator(StateStore(database))
    run = creator.create("run-1", objective="Build feature")
    original = creator.add_step(
        "run-1", "step-1", objective="First", command=("true",)
    )
    sibling = creator.add_step(
        "run-1", "step-2", objective="Second", command=("true",)
    )
    coordinators = (
        RunCoordinator(StateStore(database)),
        RunCoordinator(StateStore(database)),
    )

    def attempt(coordinator: RunCoordinator) -> RunStep | ValueError:
        try:
            return coordinator.transition_step("step-1", StepStatus.RUNNING)
        except ValueError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, coordinators))

    successes = [result for result in results if isinstance(result, RunStep)]
    failures = [result for result in results if isinstance(result, ValueError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert successes[0].revision == original.revision + 1
    assert creator.get("run-1") == run
    assert creator.get_step("step-2") == sibling


def test_step_append_is_atomic_across_coordinators(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    creator = RunCoordinator(StateStore(database))
    creator.create("run-1", objective="Build feature")
    coordinators = (
        RunCoordinator(StateStore(database)),
        RunCoordinator(StateStore(database)),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                coordinator.add_step,
                "run-1",
                f"step-{index}",
                objective=f"Work {index}",
                command=("true",),
            )
            for index, coordinator in enumerate(coordinators, start=1)
        ]
        created = tuple(future.result() for future in futures)

    assert sorted(step.position for step in created) == [1, 2]
    assert [step.position for step in creator.list_steps("run-1")] == [1, 2]


def test_duplicate_step_append_preserves_original_across_coordinators(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    first = RunCoordinator(StateStore(database))
    competing = RunCoordinator(StateStore(database))
    first.create("run-1", objective="Build feature")
    original = first.add_step(
        "run-1", "step-1", objective="Original", command=("python", "-V"), timeout=5
    )

    with pytest.raises(ValueError, match="step already exists: step-1"):
        competing.add_step(
            "run-1", "step-1", objective="Replacement", command=("true",)
        )

    assert first.get_step("step-1") == original
    assert original.revision == 1


def test_step_command_and_timeout_are_durable(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute commands")

    created = coordinator.add_step(
        "run-1",
        "command",
        objective="Print a greeting",
        command=("python", "-c", "print('hello')"),
        timeout=12.5,
    )
    coordinator.start_next_step("run-1")

    assert created.command == ("python", "-c", "print('hello')")
    assert created.timeout == 12.5
    reloaded = RunCoordinator(StateStore(database)).get_step("command")
    assert reloaded is not None
    assert reloaded.command == created.command
    assert reloaded.timeout == created.timeout


def test_command_step_sandbox_policy_round_trips_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute commands")
    policy = SandboxPolicy(
        kind=SandboxKind.DOCKER,
        image="python:3.12-slim",
        mounts=(("/host/data", "/data"),),
        working_dir="/data",
        env_passthrough=("API_TOKEN", "HOME"),
        network_enabled=True,
    )

    created = coordinator.add_step(
        "run-1",
        "command",
        objective="Print a greeting",
        command=("python", "-c", "print('hello')"),
        sandbox_policy=policy,
    )

    assert created.sandbox_policy == policy
    reloaded = RunCoordinator(StateStore(database)).get_step("command")
    assert reloaded is not None
    assert reloaded.sandbox_policy == policy


def test_command_step_without_sandbox_policy_has_none(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute commands")

    created = coordinator.add_step(
        "run-1", "command", objective="Print", command=("printf", "hi")
    )

    assert created.sandbox_policy is None


def test_sandbox_policy_is_rejected_for_provider_message_steps_without_mutation(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    run = coordinator.create("run-1", objective="Ask a model")

    with pytest.raises(ValueError, match="only valid for command steps"):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Summarize",
            message=ProviderMessage(provider="local", content="Hello"),
            sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
        )

    assert coordinator.get("run-1") == run
    assert coordinator.list_steps("run-1") == ()


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"kind": "not-a-kind"}, "kind is invalid"),
        ({"kind": "docker", "image": " "}, "image must be a non-empty string"),
        (
            {"kind": "docker", "mounts": [["/host"]]},
            "mounts require non-empty host and container paths",
        ),
        (
            {"kind": "docker", "working_dir": "relative/path"},
            "working directory must be a non-empty absolute path",
        ),
        (
            {"kind": "docker", "working_dir": " "},
            "working directory must be a non-empty absolute path",
        ),
        (
            {"kind": "docker", "env_passthrough": ["NOT VALID"]},
            "env passthrough names must be valid identifiers",
        ),
        (
            {"kind": "docker", "env_passthrough": ["DUP", "DUP"]},
            "env passthrough names must be unique",
        ),
        (
            {"kind": "docker", "network_enabled": "yes"},
            "network option must be a boolean",
        ),
    ],
)
def test_sandbox_policy_validation_rejects_invalid_input_without_mutation(
    tmp_path, policy, message
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute commands")

    with pytest.raises(ValueError, match=message):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Invalid policy",
            command=("printf", "hi"),
            sandbox_policy=policy,
        )

    assert coordinator.list_steps("run-1") == ()


def test_provider_message_step_round_trips_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ask a model")
    message = ProviderMessage(
        provider="openrouter",
        content="Summarize the change",
        model="example/model",
        system="Be concise",
        temperature=0.25,
        max_tokens=321,
    )

    created = coordinator.add_step(
        "run-1", "model", objective="Summarize", message=message
    )

    assert created.message == message
    assert RunCoordinator(StateStore(database)).get_step("model") == created


def test_provider_message_step_with_required_capability_round_trips_across_restart(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ask any capable model")
    message = ProviderMessage(
        provider=None, content="Summarize the change", required_capability="general"
    )

    created = coordinator.add_step(
        "run-1", "model", objective="Summarize", message=message
    )

    assert created.message == message
    assert created.message.provider is None
    assert created.message.required_capability == "general"
    assert RunCoordinator(StateStore(database)).get_step("model") == created


def test_add_step_rejects_provider_message_with_both_provider_and_capability(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ask a model")

    with pytest.raises(ValueError, match="exactly one of provider or required_capability"):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Summarize",
            message=ProviderMessage(
                provider="ollama", content="hi", required_capability="general"
            ),
        )

    assert coordinator.list_steps("run-1") == ()


def test_add_step_rejects_provider_message_with_neither_provider_nor_capability(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ask a model")

    with pytest.raises(ValueError, match="exactly one of provider or required_capability"):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Summarize",
            message=ProviderMessage(provider=None, content="hi"),
        )

    assert coordinator.list_steps("run-1") == ()


def test_add_step_rejects_unknown_required_capability_without_mutation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ask a model")

    with pytest.raises(ValueError, match="not declared by any configured provider"):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Summarize",
            message=ProviderMessage(
                provider=None, content="hi", required_capability="telekinesis"
            ),
        )

    assert coordinator.list_steps("run-1") == ()


def test_fixed_provider_message_step_still_omits_required_capability(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ask a model")

    created = coordinator.add_step(
        "run-1",
        "model",
        objective="Summarize",
        message=ProviderMessage(provider="ollama", content="hi"),
    )

    assert created.message.provider == "ollama"
    assert created.message.required_capability is None


def test_provider_context_step_ids_round_trip_in_declared_order(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step("run-1", "second", objective="Second", command=("true",))

    created = coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage(provider="local", content="Synthesize the results"),
        context_step_ids=("second", "first"),
    )

    assert created.context_step_ids == ("second", "first")
    assert RunCoordinator(StateStore(database)).get_step("model") == created


@pytest.mark.parametrize(
    ("context_step_ids", "command", "message", "error"),
    [
        (("missing",), None, ProviderMessage("local", "Use it"), "does not exist"),
        (("other",), None, ProviderMessage("local", "Use it"), "another run"),
        (("first",), ("true",), None, "require a provider message"),
        (("first", "first"), None, ProviderMessage("local", "Use it"), "unique"),
    ],
)
def test_provider_context_step_ids_reject_invalid_references_without_mutation(
    tmp_path, context_step_ids, command, message, error
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.create("run-2", objective="Other work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step("run-2", "other", objective="Other", command=("true",))
    before = coordinator.list_steps("run-1")

    with pytest.raises(ValueError, match=error):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Synthesize",
            command=command,
            message=message,
            context_step_ids=context_step_ids,
        )

    assert coordinator.list_steps("run-1") == before


def test_provider_context_step_ids_survive_lifecycle_payload_rewrites(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.transition_step("first", StepStatus.RUNNING)
    coordinator.transition_step("first", StepStatus.SUCCEEDED, output={"secret": "value"})
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage("local", "Use the result"),
        context_step_ids=("first",),
    )

    running = coordinator.transition_step("model", StepStatus.RUNNING)
    failed = coordinator.transition_step(
        "model", StepStatus.FAILED, output={"error": "no adapter", "error_type": "ValueError"}
    )

    assert running.context_step_ids == ("first",)
    assert failed.context_step_ids == ("first",)


def test_provider_step_dispatches_when_context_references_all_succeeded(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step("run-1", "second", objective="Second", command=("true",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage("local", "Use the results"),
        context_step_ids=("second", "first"),
    )

    class Executor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(tuple(argv), 0, "ok", "")

    class Adapter:
        def complete(self, request):
            return ChatResponse("Synthesized", model="served-model")

    executor = Executor()
    coordinator.execute_next_step("run-1", executor)
    coordinator.execute_next_step("run-1", executor)
    step, run = coordinator.execute_next_step(
        "run-1", adapter_resolver=lambda _: Adapter()
    )

    assert step.step_id == "model"
    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED

    started = next(
        entry
        for entry in coordinator.list_history("run-1")
        if entry.transition == "step_started" and entry.step_id == "model"
    )
    assert started.context_step_ids == ("second", "first")

    reloaded = RunCoordinator(StateStore(database)).list_history("run-1")
    reloaded_started = next(
        entry
        for entry in reloaded
        if entry.transition == "step_started" and entry.step_id == "model"
    )
    assert reloaded_started.context_step_ids == ("second", "first")


def test_provider_step_with_cancelled_context_reference_remains_queued_ineligible(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage("local", "Use the result"),
        context_step_ids=("first",),
    )
    coordinator.cancel_step("first")

    run_before = coordinator.get("run-1")
    step_before = coordinator.get_step("model")

    with pytest.raises(
        ContextReferencesUnresolvedError,
        match="unresolved context references: model",
    ):
        coordinator.start_next_step("run-1")

    assert coordinator.get("run-1") == run_before
    assert coordinator.get_step("model") == step_before
    assert coordinator.get_step("first").status is StepStatus.CANCELLED


def test_provider_step_with_failed_context_reference_remains_queued_ineligible(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("false",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage("local", "Use the result"),
        context_step_ids=("first",),
    )

    class FailingExecutor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(tuple(argv), 1, "", "boom")

    coordinator.execute_next_step("run-1", FailingExecutor())
    failed_first = coordinator.get_step("first")
    failed_run = coordinator.get("run-1")
    assert failed_first.status is StepStatus.FAILED
    assert failed_run.status is RunStatus.FAILED

    coordinator.retry_step(
        "first",
        "first-retry",
        expected_step_revision=failed_first.revision,
        expected_run_revision=failed_run.revision,
    )

    run_before = coordinator.get("run-1")
    step_before = coordinator.get_step("model")

    with pytest.raises(
        ContextReferencesUnresolvedError,
        match="unresolved context references: model",
    ):
        coordinator.start_next_step("run-1")

    assert coordinator.get("run-1") == run_before
    assert coordinator.get_step("model") == step_before
    assert coordinator.get_step("first").status is StepStatus.FAILED


def test_provider_step_context_eligibility_is_resolved_fresh_at_dispatch_time(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage("local", "Use the result"),
        context_step_ids=("first",),
    )

    coordinator.transition_step("first", StepStatus.RUNNING)
    coordinator.transition_step("first", StepStatus.SUCCEEDED, output={"exit_code": 0})

    started_model = coordinator.start_next_step("run-1")

    assert started_model.step_id == "model"
    assert started_model.status is StepStatus.RUNNING


def test_provider_step_context_gate_composes_with_pending_approval(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage("local", "Use the result"),
        context_step_ids=("first",),
        approval_required=True,
    )
    coordinator.cancel_step("first")

    with pytest.raises(ApprovalRequiredError, match="model"):
        coordinator.start_next_step("run-1")


def test_approval_required_step_round_trips_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Perform sensitive work")

    created = coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )

    assert created.approval_required is True
    assert created.approval_status is ApprovalStatus.PENDING
    assert RunCoordinator(StateStore(database)).get_step("sensitive") == created


def test_pending_approval_refuses_start_without_mutation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    run = coordinator.create("run-1", objective="Perform sensitive work")
    step = coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )

    with pytest.raises(
        ApprovalRequiredError, match="requires approval before dispatch: sensitive"
    ):
        coordinator.start_next_step("run-1")

    assert coordinator.get("run-1") == run
    assert coordinator.get_step("sensitive") == step


def test_pending_approval_refuses_execute_before_sandbox_dispatch(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    run = coordinator.create("run-1", objective="Perform sensitive work")
    step = coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )
    calls = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            calls.append((tuple(argv), timeout))
            return SandboxResult(tuple(argv), 0, "", "")

    executor = Executor()

    with pytest.raises(ApprovalRequiredError, match="sensitive"):
        coordinator.execute_next_step("run-1", executor)

    assert calls == []
    assert coordinator.get("run-1") == run
    assert coordinator.get_step("sensitive") == step


def test_step_without_approval_requirement_dispatches_as_before(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Perform ordinary work")
    created = coordinator.add_step(
        "run-1", "ordinary", objective="Run command", command=("true",)
    )

    running = coordinator.start_next_step("run-1")

    assert created.approval_required is False
    assert created.approval_status is None
    assert running is not None
    assert running.status is StepStatus.RUNNING
    assert running.approval_required is False
    assert running.approval_status is None


def test_approve_step_unblocks_dispatch(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Perform sensitive work")
    coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )

    approved = coordinator.approve_step("sensitive", agent_id="agent-1")

    assert approved.approval_status is ApprovalStatus.APPROVED
    assert approved.status is StepStatus.QUEUED
    assert RunCoordinator(StateStore(database)).get_step("sensitive") == approved

    running = coordinator.start_next_step("run-1")

    assert running is not None
    assert running.status is StepStatus.RUNNING
    assert running.approval_status is ApprovalStatus.APPROVED


def test_reject_step_produces_terminal_outcome_without_execution(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Perform sensitive work")
    coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )
    calls = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            calls.append(tuple(argv))
            return SandboxResult(tuple(argv), 0, "", "")

    step, run = coordinator.reject_step("sensitive", agent_id="agent-1")

    assert calls == []
    assert step.status is StepStatus.FAILED
    assert step.approval_status is ApprovalStatus.REJECTED
    assert step.output is not None and step.output["error_type"] == "ApprovalRejectedError"
    assert run.status is RunStatus.FAILED
    assert run.output is not None and run.output["failed_step_id"] == "sensitive"

    with pytest.raises(ValueError, match="terminal run"):
        coordinator.execute_next_step("run-1", Executor())
    assert calls == []


def test_reject_step_on_first_queued_step_fails_a_never_started_run(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Perform sensitive work")
    coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )

    step, run = coordinator.reject_step("sensitive")

    assert run.status is RunStatus.FAILED
    assert step.status is StepStatus.FAILED


def test_decision_against_already_decided_step_changes_nothing(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Perform sensitive work")
    coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )

    approved = coordinator.approve_step("sensitive")

    with pytest.raises(ValueError, match="not pending approval: sensitive"):
        coordinator.approve_step("sensitive")
    with pytest.raises(ValueError, match="not pending approval: sensitive"):
        coordinator.reject_step("sensitive")

    assert coordinator.get_step("sensitive") == approved
    assert coordinator.get("run-1").status is RunStatus.QUEUED


def test_competing_approval_decisions_apply_exactly_once(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Perform sensitive work")
    coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )

    def decide():
        instance = RunCoordinator(StateStore(database))
        try:
            return instance.approve_step("sensitive")
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: decide(), range(2)))

    assert sum(not isinstance(result, str) for result in results) == 1
    final = RunCoordinator(StateStore(database)).get_step("sensitive")
    assert final.approval_status is ApprovalStatus.APPROVED


def test_approval_decisions_append_atomic_history_with_agent_attribution(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Perform sensitive work")
    coordinator.add_step(
        "run-1",
        "sensitive",
        objective="Change external state",
        command=("true",),
        approval_required=True,
    )
    coordinator.approve_step("sensitive", agent_id="agent-1")

    history = RunCoordinator(StateStore(database)).list_history("run-1")
    assert [(entry.transition, entry.status, entry.agent_id) for entry in history] == [
        ("created", "queued", None),
        ("step_approved", "queued", "agent-1"),
    ]

    coordinator.add_step(
        "run-1",
        "sensitive-2",
        objective="Change external state again",
        command=("true",),
        approval_required=True,
    )
    coordinator.reject_step("sensitive-2", agent_id="agent-2")

    history = RunCoordinator(StateStore(database)).list_history("run-1")
    assert [(entry.transition, entry.status, entry.agent_id) for entry in history[-2:]] == [
        ("step_rejected", "failed", "agent-2"),
        ("run_failed", "failed", "agent-2"),
    ]


def test_step_requires_exactly_one_execution_input_without_mutation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    run = coordinator.create("run-1", objective="Validate inputs")

    with pytest.raises(ValueError, match="exactly one"):
        coordinator.add_step("run-1", "empty", objective="Empty")
    with pytest.raises(ValueError, match="exactly one"):
        coordinator.add_step(
            "run-1",
            "ambiguous",
            objective="Ambiguous",
            command=("true",),
            message=ProviderMessage(provider="local", content="Hello"),
        )

    assert coordinator.get("run-1") == run
    assert coordinator.list_steps("run-1") == ()


@pytest.mark.parametrize(
    ("command", "timeout", "message"),
    [
        ((), None, "command must not be empty"),
        (("python", ""), None, "arguments must be non-empty strings"),
        (("python",), 0, "timeout must be positive"),
        (None, 1, "timeout requires a command"),
    ],
)
def test_step_command_validation(tmp_path, command, timeout, message) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute commands")

    with pytest.raises(ValueError, match=message):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Invalid command",
            command=command,
            timeout=timeout,
        )
    assert coordinator.list_steps("run-1") == ()


def test_step_validation_and_terminal_run_rules(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")

    with pytest.raises(ValueError, match="step objective"):
        coordinator.add_step("run-1", "step-1", objective=" ")
    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.add_step("missing", "step-1", objective="Work")

    coordinator.add_step("run-1", "step-1", objective="Work", command=("true",))
    with pytest.raises(ValueError, match="step already exists"):
        coordinator.add_step(
            "run-1", "step-1", objective="Duplicate", command=("true",)
        )
    with pytest.raises(ValueError, match="invalid step transition"):
        coordinator.transition_step("step-1", StepStatus.SUCCEEDED)
    with pytest.raises(ValueError, match="output is only valid"):
        coordinator.transition_step("step-1", StepStatus.RUNNING, output={"early": True})

    coordinator.transition("run-1", RunStatus.CANCELLED)
    with pytest.raises(ValueError, match="terminal run"):
        coordinator.add_step("run-1", "step-2", objective="Too late")


@pytest.mark.parametrize("run_status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_cancelling_run_cancels_active_steps_and_preserves_completed_steps(
    tmp_path, run_status
) -> None:
    database = tmp_path / f"{run_status}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "completed", objective="Already done", command=("true",)
    )
    coordinator.add_step(
        "run-1", "active", objective="In progress", command=("true",)
    )
    coordinator.add_step(
        "run-1", "queued", objective="Not started", command=("true",)
    )
    coordinator.add_step(
        "run-1", "failed", objective="Already failed", command=("true",)
    )
    coordinator.add_step(
        "run-1", "cancelled", objective="Already cancelled", command=("true",)
    )
    coordinator.transition_step("completed", StepStatus.RUNNING)
    completed = coordinator.transition_step(
        "completed", StepStatus.SUCCEEDED, output={"artifact": "result.json"}
    )
    coordinator.transition_step("active", StepStatus.RUNNING)
    coordinator.transition_step("failed", StepStatus.RUNNING)
    failed = coordinator.transition_step(
        "failed", StepStatus.FAILED, output={"error": "known"}
    )
    cancelled_step = coordinator.transition_step("cancelled", StepStatus.CANCELLED)
    if run_status is RunStatus.RUNNING:
        coordinator.transition("run-1", RunStatus.RUNNING)

    cancelled = coordinator.cancel("run-1")

    assert cancelled.status is RunStatus.CANCELLED
    assert RunCoordinator(StateStore(database)).list_steps("run-1") == (
        completed,
        RunStep(
            "active", "run-1", 2, "In progress", StepStatus.CANCELLED, 3,
            command=("true",),
        ),
        RunStep(
            "queued", "run-1", 3, "Not started", StepStatus.CANCELLED, 2,
            command=("true",),
        ),
        failed,
        cancelled_step,
    )


def test_cancel_rejects_missing_and_terminal_runs(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.cancel("missing")

    coordinator.create("run-1", objective="Build feature")
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.transition("run-1", RunStatus.SUCCEEDED)
    with pytest.raises(ValueError, match="invalid run transition"):
        coordinator.cancel("run-1")


def test_cancel_rolls_back_every_record_when_a_batch_write_fails(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    original_run = coordinator.create("run-1", objective="Build feature")
    original_first = coordinator.add_step(
        "run-1", "first", objective="First", command=("true",)
    )
    original_second = coordinator.add_step(
        "run-1", "second", objective="Second", command=("true",)
    )
    original_write = store._put_on_connection
    writes = 0

    def fail_after_first_write(connection, kind, key, status, encoded):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("injected persistence failure")
        return original_write(connection, kind, key, status, encoded)

    monkeypatch.setattr(store, "_put_on_connection", fail_after_first_write)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        coordinator.cancel("run-1")

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == (original_first, original_second)


@pytest.mark.parametrize("parent_status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_cancel_step_changes_only_one_queued_step(tmp_path, parent_status) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / f"{parent_status}.sqlite3"))
    original_run = coordinator.create(
        "run-1", objective="Build feature", agent_id="agent-1"
    )
    first = coordinator.add_step(
        "run-1", "first", objective="Already done", command=("true",)
    )
    target = coordinator.add_step(
        "run-1",
        "target",
        objective="Skip this",
        command=("python", "-m", "pytest"),
        timeout=30,
    )
    last = coordinator.add_step(
        "run-1", "last", objective="Still queued", command=("true",)
    )
    coordinator.transition_step("first", StepStatus.RUNNING)
    first = coordinator.transition_step(
        "first", StepStatus.SUCCEEDED, output={"result": "ok"}
    )
    if parent_status is RunStatus.RUNNING:
        original_run = coordinator.transition("run-1", RunStatus.RUNNING)

    cancelled = coordinator.cancel_step("target")

    assert cancelled == RunStep(
        "target",
        "run-1",
        2,
        "Skip this",
        StepStatus.CANCELLED,
        target.revision + 1,
        command=("python", "-m", "pytest"),
        timeout=30,
    )
    assert coordinator.get("run-1") == original_run
    assert coordinator.list_steps("run-1") == (first, cancelled, last)

    appended = coordinator.add_step(
        "run-1", "appended", objective="Added later", command=("true",)
    )
    assert appended.position == 4


@pytest.mark.parametrize(
    "step_status",
    [StepStatus.RUNNING, StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED],
)
def test_cancel_step_rejects_non_queued_steps_without_mutation(
    tmp_path, step_status
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / f"{step_status}.sqlite3"))
    original_run = coordinator.create("run-1", objective="Build feature")
    coordinator.add_step("run-1", "step-1", objective="Work", command=("true",))
    if step_status is StepStatus.RUNNING:
        original_step = coordinator.transition_step("step-1", StepStatus.RUNNING)
    elif step_status is StepStatus.CANCELLED:
        original_step = coordinator.transition_step("step-1", StepStatus.CANCELLED)
    else:
        coordinator.transition_step("step-1", StepStatus.RUNNING)
        original_step = coordinator.transition_step("step-1", step_status)

    with pytest.raises(ValueError, match="step must be queued"):
        coordinator.cancel_step("step-1")

    assert coordinator.get("run-1") == original_run
    assert coordinator.get_step("step-1") == original_step


@pytest.mark.parametrize(
    "parent_status", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED]
)
def test_cancel_step_rejects_terminal_parent_without_mutation(
    tmp_path, parent_status
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / f"{parent_status}.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    original_step = coordinator.add_step(
        "run-1", "step-1", objective="Work", command=("true",)
    )
    coordinator.transition("run-1", RunStatus.RUNNING)
    original_run = coordinator.transition("run-1", parent_status)

    with pytest.raises(ValueError, match="run must be active"):
        coordinator.cancel_step("step-1")

    assert coordinator.get("run-1") == original_run
    assert coordinator.get_step("step-1") == original_step


def test_cancel_step_rejects_missing_or_orphaned_step_without_mutation(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = RunCoordinator(store)

    with pytest.raises(KeyError, match="step does not exist"):
        coordinator.cancel_step("missing")

    orphan = store.put(
        "step",
        "orphan",
        status=StepStatus.QUEUED,
        payload={"run_id": "missing-run", "position": 1, "objective": "Orphan"},
    )
    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.cancel_step("orphan")

    assert store.get("step", "orphan") == orphan


def test_cancel_step_rejects_malformed_step_without_mutation(tmp_path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    coordinator = RunCoordinator(store)
    malformed = store.put(
        "step",
        "malformed",
        status=StepStatus.QUEUED,
        payload={"run_id": "", "position": 1, "objective": "Malformed"},
    )

    with pytest.raises(ValueError, match="invalid run id"):
        coordinator.cancel_step("malformed")

    assert store.get("step", "malformed") == malformed


def test_start_next_step_dispatches_in_position_order(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "first", objective="First command", command=("true",)
    )
    coordinator.add_step(
        "run-1", "second", objective="Second command", command=("true",)
    )

    first = coordinator.start_next_step("run-1")

    assert first == RunStep(
        "first", "run-1", 1, "First command", StepStatus.RUNNING, 2,
        command=("true",),
    )
    assert coordinator.get("run-1") == AgentRun(
        "run-1", "Build feature", RunStatus.RUNNING, 2
    )

    coordinator.transition_step("first", StepStatus.SUCCEEDED)
    second = coordinator.start_next_step("run-1")

    assert second.step_id == "second"
    assert second.status is StepStatus.RUNNING
    assert second.revision == 2
    assert coordinator.get("run-1").revision == 2


def test_start_next_step_rolls_back_first_dispatch_when_batch_write_fails(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    original_run = coordinator.create("run-1", objective="Build feature")
    original_step = coordinator.add_step(
        "run-1", "first", objective="First command", command=("true",)
    )
    original_write = store._put_on_connection
    writes = 0

    def fail_after_first_write(connection, kind, key, status, encoded):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("injected persistence failure")
        return original_write(connection, kind, key, status, encoded)

    monkeypatch.setattr(store, "_put_on_connection", fail_after_first_write)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        coordinator.start_next_step("run-1")

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.get_step("first") == original_step


def test_start_next_step_validates_run_and_single_active_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.start_next_step("missing")

    coordinator.create("run-1", objective="Build feature")
    assert coordinator.start_next_step("run-1") is None

    coordinator.add_step(
        "run-1", "first", objective="First command", command=("true",)
    )
    coordinator.add_step(
        "run-1", "second", objective="Second command", command=("true",)
    )
    coordinator.start_next_step("run-1")
    with pytest.raises(ValueError, match="already has a running step"):
        coordinator.start_next_step("run-1")

    coordinator.cancel("run-1")
    with pytest.raises(ValueError, match="terminal run"):
        coordinator.start_next_step("run-1")


def test_sandbox_policy_survives_dispatch_and_completion(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.PODMAN, env_passthrough=("HOME",))
    coordinator.add_step(
        "run-1", "first", objective="First command", command=("true",),
        sandbox_policy=policy,
    )

    running = coordinator.start_next_step("run-1")
    assert running is not None
    assert running.sandbox_policy == policy

    completed, _ = coordinator.complete_step_from_result(
        "first", SandboxResult(("podman", "run", "true"), 0, "ok\n", "")
    )
    assert completed.sandbox_policy == policy
    reloaded = RunCoordinator(StateStore(coordinator.store.path)).get_step("first")
    assert reloaded is not None
    assert reloaded.sandbox_policy == policy


def test_persisted_policy_result_redacts_resolved_environment_values(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "first", objective="First command", command=("true",),
        sandbox_policy=SandboxPolicy(
            kind=SandboxKind.DOCKER, env_passthrough=("API_TOKEN",)
        ),
    )
    coordinator.start_next_step("run-1")

    completed, _ = coordinator.complete_step_from_result(
        "first",
        SandboxResult(
            (
                "docker", "run", "--env", "API_TOKEN=runtime-only-secret",
                "python:3.12-slim", "true",
            ),
            0,
            "ok\n",
            "",
        ),
    )

    assert completed.output is not None
    assert completed.output["command"] == [
        "docker", "run", "--env", "API_TOKEN", "python:3.12-slim", "true",
    ]
    assert b"runtime-only-secret" not in database.read_bytes()


def test_sandbox_results_complete_steps_and_run_without_backend_coupling(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "first", objective="First command", command=("true",)
    )
    coordinator.add_step(
        "run-1", "second", objective="Second command", command=("true",)
    )
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.transition_step("first", StepStatus.RUNNING)

    first, running = coordinator.complete_step_from_result(
        "first", SandboxResult(("docker", "run", "true"), 0, "ok\n", "")
    )

    assert first.status is StepStatus.SUCCEEDED
    assert first.output == {
        "command": ["docker", "run", "true"],
        "exit_code": 0,
        "stdout": "ok\n",
        "stderr": "",
    }
    assert running.status is RunStatus.RUNNING

    coordinator.transition_step("second", StepStatus.RUNNING)
    second, succeeded = coordinator.complete_step_from_result(
        "second", SandboxResult(("podman", "run", "true"), 0, "", "")
    )

    assert second.status is StepStatus.SUCCEEDED
    assert second.revision == 3
    assert succeeded.status is RunStatus.SUCCEEDED
    assert succeeded.revision == 3
    assert succeeded.output == {"completed_steps": 2}


def test_failed_sandbox_result_fails_step_and_run(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("true",)
    )
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.transition_step("command", StepStatus.RUNNING)

    step, run = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "false"), 17, "", "failed\n")
    )

    assert step.status is StepStatus.FAILED
    assert step.revision == 3
    assert step.output == {
        "command": ["docker", "run", "false"],
        "exit_code": 17,
        "stdout": "",
        "stderr": "failed\n",
    }
    assert run.status is RunStatus.FAILED
    assert run.revision == 3
    assert run.output == {"failed_step_id": "command", "exit_code": 17}
    assert step.failure_kind is StepFailureKind.DEFINITE
    assert step.retry_eligible is True


@pytest.mark.parametrize("returncode", [0, 17])
def test_terminal_result_rolls_back_step_and_run_when_batch_write_fails(
    tmp_path, monkeypatch, returncode
) -> None:
    database = tmp_path / f"result-{returncode}.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("true",)
    )
    original_step = coordinator.start_next_step("run-1")
    original_run = coordinator.get("run-1")
    original_write = store._put_on_connection
    writes = 0

    def fail_after_first_write(connection, kind, key, status, encoded):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("injected persistence failure")
        return original_write(connection, kind, key, status, encoded)

    monkeypatch.setattr(store, "_put_on_connection", fail_after_first_write)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        coordinator.complete_step_from_result(
            "command",
            SandboxResult(("docker", "run", "command"), returncode, "", ""),
        )

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_step("command") == original_step
    assert reloaded.get("run-1") == original_run


def test_execution_result_requires_running_run_and_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("true",)
    )
    result = SandboxResult(("docker", "run", "true"), 0, "", "")

    with pytest.raises(ValueError, match="run must be running"):
        coordinator.complete_step_from_result("command", result)

    coordinator.transition("run-1", RunStatus.RUNNING)
    with pytest.raises(ValueError, match="step must be running"):
        coordinator.complete_step_from_result("command", result)


def test_execute_next_step_uses_injected_sandbox_and_completes_run(tmp_path) -> None:
    calls = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            calls.append((tuple(argv), timeout))
            return SandboxResult(("docker", "run", *argv), 0, "hello\n", "")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute command")
    coordinator.add_step(
        "run-1",
        "command",
        objective="Print greeting",
        command=("python", "-c", "print('hello')"),
        timeout=7.5,
    )

    result = coordinator.execute_next_step("run-1", Executor())

    assert result is not None
    step, run = result
    assert calls == [(('python', '-c', "print('hello')"), 7.5)]
    assert step.status is StepStatus.SUCCEEDED
    assert step.output["stdout"] == "hello\n"
    assert run.status is RunStatus.SUCCEEDED


def test_execute_next_step_resolves_persisted_sandbox_policy_before_dispatch(
    tmp_path,
) -> None:
    policy = SandboxPolicy(
        kind=SandboxKind.PODMAN,
        image="custom:1",
        mounts=(("/host", "/workspace"),),
        working_dir="/workspace",
        env_passthrough=("TOKEN",),
        network_enabled=True,
    )
    resolved = []
    calls = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            calls.append((tuple(argv), timeout))
            return SandboxResult(("podman", "run", *argv), 0, "ok\n", "")

    def resolve(received_policy):
        resolved.append(received_policy)
        return Executor()

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute persisted policy")
    coordinator.add_step(
        "run-1", "command", objective="Run", command=("true",), timeout=3,
        sandbox_policy=policy,
    )

    step, run = coordinator.execute_next_step(
        "run-1", sandbox_resolver=resolve
    )

    assert resolved == [policy]
    assert calls == [(('true',), 3)]
    assert step.status is StepStatus.SUCCEEDED
    assert step.sandbox_policy == policy
    assert run.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize("supply_executor", [False, True])
def test_execute_next_step_rejects_bypassing_persisted_policy_without_mutation(
    tmp_path, supply_executor
) -> None:
    class Executor:
        def execute(self, argv, *, timeout=None):
            raise AssertionError("must not execute")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    run = coordinator.create("run-1", objective="Execute persisted policy")
    step = coordinator.add_step(
        "run-1", "command", objective="Run", command=("true",),
        sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
    )

    with pytest.raises(ValueError, match="persisted sandbox policy"):
        coordinator.execute_next_step(
            "run-1", Executor() if supply_executor else None
        )

    assert coordinator.get("run-1") == run
    assert coordinator.get_step("command") == step


def test_execute_next_step_records_nonzero_result(tmp_path) -> None:
    class Executor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(("podman", "run", *argv), 9, "", "bad\n")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute command")
    coordinator.add_step(
        "run-1", "command", objective="Fail", command=("false",)
    )

    step, run = coordinator.execute_next_step("run-1", Executor())

    assert step.status is StepStatus.FAILED
    assert run.status is RunStatus.FAILED
    assert run.output == {"failed_step_id": "command", "exit_code": 9}


def test_execute_next_step_sends_provider_message_and_persists_response(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    resolved = []
    requests = []

    class Adapter:
        def complete(self, request):
            requests.append(request)
            return ChatResponse(
                "Durable answer",
                model="served-model",
                raw={"id": "r1"},
                usage=ChatUsage(
                    available=True,
                    input_tokens=17,
                    output_tokens=5,
                    raw={"prompt_tokens": 17, "completion_tokens": 5},
                ),
            )

    def resolve(message):
        resolved.append(message)
        return Adapter()

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Coordinate work")
    message = ProviderMessage(
        provider="local",
        content="Review output",
        model="requested-model",
        system="Be concise",
        temperature=0.25,
        max_tokens=120,
    )
    coordinator.add_step(
        "run-1",
        "manual",
        objective="Review output",
        message=message,
    )

    step, run = coordinator.execute_next_step("run-1", adapter_resolver=resolve)

    assert resolved == [message]
    assert requests[0].messages[0].role == "system"
    assert requests[0].messages[0].content == "Be concise"
    assert requests[0].messages[1].content == "Review output"
    assert requests[0].temperature == 0.25
    assert requests[0].max_tokens == 120
    assert step.status is StepStatus.SUCCEEDED
    assert step.output == {
        "content": "Durable answer",
        "model": "served-model",
        "raw": {"id": "r1"},
        "usage": {
            "available": True,
            "input_tokens": 17,
            "output_tokens": 5,
            "raw": {"prompt_tokens": 17, "completion_tokens": 5},
            "unavailable_reason": None,
        },
    }
    assert run.status is RunStatus.SUCCEEDED
    assert RunCoordinator(StateStore(database)).get_step("manual") == step


def test_execute_next_step_routes_capability_by_policy_and_reconstructs_provenance(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    resolved_messages = []

    class Adapter:
        def complete(self, request):
            return ChatResponse("Durable answer", model="served-model")

    class Executor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(tuple(argv), 0, "ready", "")

    specs = (
        ProviderSpec(
            kind=ProviderKind.OPENAI, model="openai-model", capabilities=("reasoning",)
        ),
        ProviderSpec(
            kind=ProviderKind.ANTHROPIC,
            model="anthropic-model",
            capabilities=("reasoning",),
        ),
    )
    policy = ProviderRoutingPolicy((ProviderKind.ANTHROPIC, ProviderKind.OPENAI))
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Route work")
    coordinator.add_step(
        "run-1", "prepare", objective="Prepare", command=("true",)
    )
    coordinator.add_step(
        "run-1",
        "routed",
        objective="Reason",
        message=ProviderMessage(
            provider=None,
            content="private request body",
            required_capability="reasoning",
        ),
    )
    coordinator.execute_next_step("run-1", Executor())

    step, run = coordinator.execute_next_step(
        "run-1",
        adapter_resolver=lambda message: resolved_messages.append(message) or Adapter(),
        routing_policy=policy,
        provider_specs=specs,
    )

    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED
    assert resolved_messages == [
        ProviderMessage(
            provider="anthropic", content="private request body", model="anthropic-model"
        )
    ]
    started = next(
        entry
        for entry in RunCoordinator(StateStore(database)).list_history("run-1")
        if entry.transition == "step_started" and entry.step_id == "routed"
    )
    assert started.required_capability == "reasoning"
    assert started.resolved_provider == "anthropic"
    assert started.resolved_model == "anthropic-model"
    assert started.routing_reason == (
        "policy position 1 selected anthropic as the first configured provider "
        "declaring capability 'reasoning'"
    )
    assert "private request body" not in repr(started)


def test_execute_next_step_policy_order_changes_capability_route(tmp_path) -> None:
    resolved_providers = []

    class Adapter:
        def complete(self, request):
            return ChatResponse("ok")

    specs = (
        ProviderSpec(
            kind=ProviderKind.OPENAI, model="openai-model", capabilities=("general",)
        ),
        ProviderSpec(
            kind=ProviderKind.ANTHROPIC,
            model="anthropic-model",
            capabilities=("general",),
        ),
    )
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    for run_id, policy in (
        ("openai-first", ProviderRoutingPolicy((ProviderKind.OPENAI, ProviderKind.ANTHROPIC))),
        ("anthropic-first", ProviderRoutingPolicy((ProviderKind.ANTHROPIC, ProviderKind.OPENAI))),
    ):
        coordinator.create(run_id, objective="Route work")
        coordinator.add_step(
            run_id,
            f"{run_id}-step",
            objective="Route",
            message=ProviderMessage(
                provider=None, content="route", required_capability="general"
            ),
        )
        coordinator.execute_next_step(
            run_id,
            adapter_resolver=lambda message: resolved_providers.append(message.provider)
            or Adapter(),
            routing_policy=policy,
            provider_specs=specs,
        )

    assert resolved_providers == ["openai", "anthropic"]


def test_execute_next_step_fixed_provider_bypasses_capability_routing(tmp_path) -> None:
    resolved = []

    class Adapter:
        def complete(self, request):
            return ChatResponse("fixed")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Fixed dispatch")
    message = ProviderMessage(provider="ollama", content="fixed request")
    coordinator.add_step("run-1", "fixed", objective="Fixed", message=message)

    coordinator.execute_next_step(
        "run-1",
        adapter_resolver=lambda routed: resolved.append(routed) or Adapter(),
        routing_policy=ProviderRoutingPolicy(()),
        provider_specs=(),
    )

    assert resolved == [message]
    started = next(
        entry
        for entry in coordinator.list_history("run-1")
        if entry.transition == "step_started"
    )
    assert started.required_capability is None
    assert started.resolved_provider is None
    assert started.resolved_model is None
    assert started.routing_reason is None


def test_execute_next_step_fails_definitively_when_no_provider_satisfies_capability(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    specs = (
        ProviderSpec(
            kind=ProviderKind.OPENAI, model="openai-model", capabilities=("general",)
        ),
    )
    policy = ProviderRoutingPolicy((ProviderKind.OPENAI,))
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Route work")
    coordinator.add_step(
        "run-1",
        "routed",
        objective="Reason",
        message=ProviderMessage(
            provider=None,
            content="private request body",
            required_capability="reasoning",
        ),
    )

    step, run = coordinator.execute_next_step(
        "run-1",
        adapter_resolver=lambda message: pytest.fail("adapter must not be invoked"),
        routing_policy=policy,
        provider_specs=specs,
    )

    assert step.status is StepStatus.FAILED
    assert step.output == {
        "error": "no configured provider satisfies required capability: reasoning",
        "error_type": "ValueError",
    }
    assert step.failure_kind is StepFailureKind.DEFINITE
    assert step.retry_eligible is True
    assert run.status is RunStatus.FAILED

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1").status is RunStatus.FAILED
    assert reloaded.get_step("routed").status is StepStatus.FAILED
    assert reloaded.get_step("routed").output == step.output

    history = reloaded.list_history("run-1")
    started = next(
        entry for entry in history if entry.transition == "step_started"
    )
    assert started.required_capability == "reasoning"
    assert started.resolved_provider is None
    assert started.resolved_model is None
    assert started.routing_reason is None
    failed = next(entry for entry in history if entry.transition == "step_failed")
    assert failed.step_id == "routed"
    assert "private request body" not in repr(failed)


def test_execute_next_step_fixed_provider_regression_alongside_capability_failure(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    specs = (
        ProviderSpec(
            kind=ProviderKind.OPENAI, model="openai-model", capabilities=("general",)
        ),
    )
    policy = ProviderRoutingPolicy((ProviderKind.OPENAI,))
    coordinator = RunCoordinator(StateStore(database))

    coordinator.create("routed-run", objective="Route work")
    coordinator.add_step(
        "routed-run",
        "routed",
        objective="Reason",
        message=ProviderMessage(
            provider=None, content="route", required_capability="reasoning"
        ),
    )
    failed_step, failed_run = coordinator.execute_next_step(
        "routed-run",
        adapter_resolver=lambda message: pytest.fail("adapter must not be invoked"),
        routing_policy=policy,
        provider_specs=specs,
    )
    assert failed_step.status is StepStatus.FAILED
    assert failed_run.status is RunStatus.FAILED

    coordinator.create("fixed-run", objective="Fixed dispatch")
    fixed_message = ProviderMessage(provider="ollama", content="fixed request")
    coordinator.add_step("fixed-run", "fixed", objective="Fixed", message=fixed_message)

    resolved = []

    class Adapter:
        def complete(self, request):
            return ChatResponse("fixed")

    fixed_step, fixed_run = coordinator.execute_next_step(
        "fixed-run",
        adapter_resolver=lambda routed: resolved.append(routed) or Adapter(),
        routing_policy=policy,
        provider_specs=specs,
    )
    assert resolved == [fixed_message]
    assert fixed_step.status is StepStatus.SUCCEEDED
    assert fixed_run.status is RunStatus.SUCCEEDED


def test_execute_next_step_sends_resolved_context_as_ordered_prior_turns(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    requests = []

    class CommandExecutor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(tuple(argv), 0, "first-stdout", "")

    class SummaryAdapter:
        def complete(self, request):
            return ChatResponse("Second output", model="served-model")

    class SynthesisAdapter:
        def complete(self, request):
            requests.append(request)
            return ChatResponse("Synthesized", model="served-model")

    def resolve(message):
        return SummaryAdapter() if message.content == "Summarize" else SynthesisAdapter()

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Compose durable work")
    coordinator.add_step("run-1", "first", objective="Gather input", command=("true",))
    coordinator.add_step(
        "run-1",
        "second",
        objective="Summarize input",
        message=ProviderMessage(provider="local", content="Summarize"),
    )
    coordinator.add_step(
        "run-1",
        "model",
        objective="Synthesize",
        message=ProviderMessage(
            provider="local", content="Synthesize the results", system="Be concise"
        ),
        context_step_ids=("second", "first"),
    )

    coordinator.execute_next_step("run-1", CommandExecutor(), adapter_resolver=resolve)
    coordinator.execute_next_step("run-1", CommandExecutor(), adapter_resolver=resolve)
    step, run = coordinator.execute_next_step(
        "run-1", CommandExecutor(), adapter_resolver=resolve
    )

    assert len(requests) == 1
    assert requests[0].messages == (
        ChatMessage("system", "Be concise"),
        ChatMessage("user", "Summarize input"),
        ChatMessage("assistant", "Second output"),
        ChatMessage("user", "Gather input"),
        ChatMessage("assistant", "exit_code=0\nstdout:\nfirst-stdout\nstderr:\n"),
        ChatMessage("user", "Synthesize the results"),
    )
    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED


def test_execute_next_step_persists_explicit_unavailable_usage(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse(
                "Durable answer",
                usage=ChatUsage(
                    available=False,
                    unavailable_reason="provider response did not include a usage block",
                ),
            )

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Coordinate work")
    coordinator.add_step(
        "run-1",
        "manual",
        objective="Review output",
        message=ProviderMessage(provider="local", content="Review output"),
    )

    step, run = coordinator.execute_next_step(
        "run-1", adapter_resolver=lambda _: Adapter()
    )

    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED
    assert step.output["usage"] == {
        "available": False,
        "input_tokens": None,
        "output_tokens": None,
        "raw": None,
        "unavailable_reason": "provider response did not include a usage block",
    }
    assert RunCoordinator(StateStore(database)).get_step("manual") == step


def test_execute_next_step_records_provider_message_execution_kind_in_history(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse("Durable answer", model="served-model")

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Coordinate work")
    coordinator.add_step(
        "run-1",
        "manual",
        objective="Review output",
        message=ProviderMessage(provider="local", content="Review output"),
    )

    coordinator.execute_next_step("run-1", adapter_resolver=lambda _: Adapter())

    history = RunCoordinator(StateStore(database)).list_history("run-1")
    assert [entry.transition for entry in history] == [
        "created",
        "run_started",
        "step_started",
        "step_succeeded",
        "run_succeeded",
    ]
    assert history[-1].status == "succeeded"
    assert history[-1].execution_kind == "provider"
    assert history[-2].step_id == "manual"


def test_list_history_reflects_create_claim_and_transition_and_rejects_missing_run(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Build feature")
    coordinator.claim("run-1", "agent-1")
    coordinator.transition("run-1", RunStatus.RUNNING)

    history = RunCoordinator(StateStore(database)).list_history("run-1")

    assert [(entry.transition, entry.status, entry.agent_id) for entry in history] == [
        ("created", "queued", None),
        ("claimed", "queued", "agent-1"),
        ("transitioned", "running", "agent-1"),
    ]
    with pytest.raises(KeyError, match="run does not exist: missing"):
        coordinator.list_history("missing")


def test_competing_model_execution_sends_step_once(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Coordinate work")
    coordinator.add_step(
        "run-1",
        "model",
        objective="Review output",
        message=ProviderMessage(provider="local", content="Review output"),
    )
    coordinator.transition("run-1", RunStatus.RUNNING)
    calls = []

    class Adapter:
        def complete(self, request):
            calls.append(request)
            return ChatResponse("answer")

    def execute():
        instance = RunCoordinator(StateStore(database))
        try:
            return instance.execute_next_step(
                "run-1", adapter_resolver=lambda _: Adapter()
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: execute(), range(2)))

    assert len(calls) == 1
    assert sum(not isinstance(result, str) for result in results) == 1
    assert RunCoordinator(StateStore(database)).get_step("model").status is StepStatus.SUCCEEDED


def test_execute_next_step_leaves_running_state_when_executor_raises(tmp_path) -> None:
    class Executor:
        def execute(self, argv, *, timeout=None):
            raise TimeoutError("sandbox timed out")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute command")
    coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10"), timeout=1
    )

    with pytest.raises(TimeoutError, match="sandbox timed out"):
        coordinator.execute_next_step("run-1", Executor())

    assert coordinator.get("run-1").status is RunStatus.RUNNING
    assert coordinator.get_step("command").status is StepStatus.RUNNING


def test_execute_next_step_fails_step_on_adapter_transport_error(tmp_path) -> None:
    class Adapter:
        def complete(self, request):
            raise RuntimeError("chat request failed: connection refused")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Coordinate work")
    coordinator.add_step(
        "run-1",
        "model",
        objective="Review output",
        message=ProviderMessage(provider="local", content="Review output"),
    )

    step, run = coordinator.execute_next_step("run-1", adapter_resolver=lambda _: Adapter())

    assert step.status is StepStatus.FAILED
    assert step.output == {
        "error": "chat request failed: connection refused",
        "error_type": "RuntimeError",
    }
    assert run.status is RunStatus.FAILED
    assert run.output == {
        "failed_step_id": "model",
        "error": "chat request failed: connection refused",
    }
    assert step.failure_kind is StepFailureKind.DEFINITE
    assert step.retry_eligible is True


def test_execute_next_step_fails_step_on_adapter_resolution_error(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Coordinate work")
    coordinator.add_step(
        "run-1",
        "model",
        objective="Review output",
        message=ProviderMessage(provider="anthropic", content="Review output"),
    )

    def resolve(message):
        raise ValueError("missing credentials for provider: anthropic")

    step, run = coordinator.execute_next_step("run-1", adapter_resolver=resolve)

    assert step.status is StepStatus.FAILED
    assert step.output == {
        "error": "missing credentials for provider: anthropic",
        "error_type": "ValueError",
    }
    assert run.status is RunStatus.FAILED
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1").status is RunStatus.FAILED
    assert reloaded.get_step("model").status is StepStatus.FAILED


def test_execute_next_step_runs_mixed_command_and_model_steps_in_order(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    command_calls = []
    model_calls = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            command_calls.append(tuple(argv))
            return SandboxResult(("docker", "run", *argv), 0, "ok\n", "")

    class Adapter:
        def complete(self, request):
            model_calls.append(request)
            return ChatResponse("Reviewed", model="served-model")

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Mixed durable work")
    coordinator.add_step("run-1", "step-1", objective="Checkpoint", command=("true",))
    coordinator.add_step(
        "run-1",
        "step-2",
        objective="Review",
        message=ProviderMessage(provider="local", content="Review output"),
    )
    coordinator.add_step("run-1", "step-3", objective="Finish", command=("true",))

    executor = Executor()
    resolve = lambda _: Adapter()

    first = coordinator.execute_next_step("run-1", executor, adapter_resolver=resolve)
    second = coordinator.execute_next_step("run-1", executor, adapter_resolver=resolve)
    third = coordinator.execute_next_step("run-1", executor, adapter_resolver=resolve)

    assert first[0].step_id == "step-1" and first[0].status is StepStatus.SUCCEEDED
    assert second[0].step_id == "step-2" and second[0].status is StepStatus.SUCCEEDED
    assert third[0].step_id == "step-3" and third[0].status is StepStatus.SUCCEEDED
    assert command_calls == [("true",), ("true",)]
    assert len(model_calls) == 1
    assert third[1].status is RunStatus.SUCCEEDED
    history = RunCoordinator(StateStore(database)).list_history("run-1")
    assert [entry.transition for entry in history] == [
        "created",
        "run_started",
        "step_started",
        "step_succeeded",
        "step_started",
        "step_succeeded",
        "step_started",
        "step_succeeded",
        "run_succeeded",
    ]
    assert [
        (entry.step_id, entry.execution_kind)
        for entry in history
        if entry.step_id is not None
    ] == [
        ("step-1", "command"),
        ("step-1", "command"),
        ("step-2", "provider"),
        ("step-2", "provider"),
        ("step-3", "command"),
        ("step-3", "command"),
    ]
    assert all("Review output" not in repr(entry) for entry in history)


@pytest.mark.parametrize(
    "reason", [StepRecoveryReason.INTERRUPTED, StepRecoveryReason.TIMED_OUT]
)
def test_recover_running_step_fails_step_and_run_durably(tmp_path, reason) -> None:
    database = tmp_path / f"{reason}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Execute command")
    coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10"), timeout=1
    )
    coordinator.start_next_step("run-1")

    step, run = coordinator.recover_running_step(
        "command", reason, detail="worker process exited before recording a result"
    )

    assert step.status is StepStatus.FAILED
    assert step.output == {
        "recovery_reason": reason.value,
        "recovery_detail": "worker process exited before recording a result",
    }
    assert run.status is RunStatus.FAILED
    assert run.output == {
        "failed_step_id": "command",
        "recovery_reason": reason.value,
    }
    assert step.failure_kind is StepFailureKind.UNCERTAIN
    assert step.retry_eligible is False
    assert step.revision == 3
    assert run.revision == 3
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_step("command") == step
    assert reloaded.get("run-1") == run


@pytest.mark.parametrize("status", [StepStatus.QUEUED, StepStatus.RUNNING, StepStatus.SUCCEEDED])
def test_non_failed_steps_have_no_failure_classification(tmp_path, status) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / f"{status.value}.sqlite3"))
    coordinator.create("run-1", objective="Inspect classification")
    step = coordinator.add_step(
        "run-1", "command", objective="Run command", command=("true",)
    )
    if status is StepStatus.RUNNING:
        step = coordinator.start_next_step("run-1")
    elif status is StepStatus.SUCCEEDED:
        coordinator.start_next_step("run-1")
        step, _ = coordinator.complete_step_from_result(
            "command", SandboxResult(("docker", "run", "true"), 0, "", "")
        )

    assert step is not None
    assert step.failure_kind is None
    assert step.retry_eligible is None


def test_retry_step_creates_new_queued_attempt_and_reopens_run(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Build feature", agent_id="agent-1")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("false",), timeout=30
    )
    coordinator.start_next_step("run-1")
    failed_step, failed_run = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "false"), 17, "", "boom")
    )

    new_step, run = coordinator.retry_step(
        "command", "command-retry",
        expected_step_revision=failed_step.revision,
        expected_run_revision=failed_run.revision,
    )

    assert new_step.status is StepStatus.QUEUED
    assert new_step.command == ("false",)
    assert new_step.timeout == 30
    assert new_step.objective == "Run command"
    assert new_step.position == 2
    assert run.status is RunStatus.QUEUED
    assert run.revision == failed_run.revision + 1
    assert run.output is None

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_step("command") == failed_step
    assert reloaded.get_step("command") == coordinator.get_step("command")
    history = reloaded.list_history("run-1")
    assert history[-1].transition == "step_retried"
    assert history[-1].step_id == "command-retry"
    assert history[-1].retried_step_id == "command"

    running_step = reloaded.start_next_step("run-1")
    assert running_step.step_id == "command-retry"
    assert running_step.status is StepStatus.RUNNING


def test_successful_retry_completes_run_despite_superseded_failed_attempt(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Retry command")
    coordinator.add_step("run-1", "command", objective="Run", command=("false",))
    coordinator.start_next_step("run-1")
    failed_step, failed_run = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "false"), 1, "", "boom")
    )
    coordinator.retry_step(
        "command", "command-retry",
        expected_step_revision=failed_step.revision,
        expected_run_revision=failed_run.revision,
    )
    coordinator.start_next_step("run-1")

    retried_step, completed_run = coordinator.complete_step_from_result(
        "command-retry", SandboxResult(("docker", "false"), 0, "ok", "")
    )

    assert retried_step.status is StepStatus.SUCCEEDED
    assert completed_run.status is RunStatus.SUCCEEDED
    assert coordinator.get_step("command") == failed_step
    assert coordinator.list_history("run-1")[-1].transition == "run_succeeded"


def test_successful_provider_retry_completes_run_after_superseded_failure(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Retry provider")
    coordinator.add_step(
        "run-1",
        "model",
        objective="Ask model",
        message=ProviderMessage(provider="ollama", content="Answer"),
    )
    coordinator.start_next_step("run-1")
    failed_step, failed_run = coordinator.fail_step_from_error(
        "model", RuntimeError("provider unavailable")
    )
    coordinator.retry_step(
        "model", "model-retry",
        expected_step_revision=failed_step.revision,
        expected_run_revision=failed_run.revision,
    )
    coordinator.start_next_step("run-1")

    retried_step, completed_run = coordinator.complete_step_from_chat_response(
        "model-retry", ChatResponse("answer", model="local")
    )

    assert retried_step.status is StepStatus.SUCCEEDED
    assert completed_run.status is RunStatus.SUCCEEDED
    assert coordinator.get_step("model") == failed_step
    assert coordinator.list_history("run-1")[-1].transition == "run_succeeded"


def test_retry_step_rejects_uncertain_recovered_step_without_mutation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Execute command")
    coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10"), timeout=1
    )
    coordinator.start_next_step("run-1")
    step, run = coordinator.recover_running_step(
        "command", StepRecoveryReason.TIMED_OUT
    )

    with pytest.raises(ValueError, match="not retry-eligible"):
        coordinator.retry_step(
            "command", "command-retry",
            expected_step_revision=step.revision,
            expected_run_revision=run.revision,
        )

    assert coordinator.get_step("command") == step
    assert coordinator.get("run-1") == run
    assert coordinator.get_step("command-retry") is None


@pytest.mark.parametrize("status", [StepStatus.QUEUED, StepStatus.RUNNING, StepStatus.SUCCEEDED])
def test_retry_step_rejects_non_failed_step_without_mutation(tmp_path, status) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / f"{status.value}.sqlite3"))
    coordinator.create("run-1", objective="Inspect classification")
    coordinator.add_step(
        "run-1", "command", objective="Run command", command=("true",)
    )
    if status is StepStatus.RUNNING:
        step = coordinator.start_next_step("run-1")
    elif status is StepStatus.SUCCEEDED:
        coordinator.start_next_step("run-1")
        step, _ = coordinator.complete_step_from_result(
            "command", SandboxResult(("docker", "run", "true"), 0, "", "")
        )
    else:
        step = coordinator.get_step("command")

    with pytest.raises(ValueError, match="not retry-eligible"):
        coordinator.retry_step(
            "command", "command-retry",
            expected_step_revision=step.revision,
            expected_run_revision=coordinator.get("run-1").revision,
        )

    assert coordinator.get_step("command") == step


def test_retry_step_is_compare_and_swap_safe(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    setup = RunCoordinator(StateStore(database))
    setup.create("run-1", objective="Build feature", agent_id="agent-1")
    setup.add_step("run-1", "command", objective="Run command", command=("false",))
    setup.start_next_step("run-1")
    failed_step, failed_run = setup.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "false"), 17, "", "boom")
    )
    coordinators = tuple(_RunCoordinator(StateStore(database)) for _ in range(2))

    def attempt(coordinator, new_step_id):
        try:
            return coordinator.retry_step(
                "command", new_step_id,
                expected_step_revision=failed_step.revision,
                expected_run_revision=failed_run.revision,
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(attempt, coordinators, ("retry-a", "retry-b"))
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == winners[0][1]
    assert reloaded.get_step("command") == failed_step
    assert len(
        [entry for entry in reloaded.list_history("run-1") if entry.transition == "step_retried"]
    ) == 1


def test_recover_running_step_validates_state_and_reason_before_mutation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="step does not exist"):
        coordinator.recover_running_step("missing", StepRecoveryReason.INTERRUPTED)

    coordinator.create("run-1", objective="Execute command")
    coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10")
    )
    with pytest.raises(ValueError, match="run must be running"):
        coordinator.recover_running_step("command", StepRecoveryReason.INTERRUPTED)

    coordinator.start_next_step("run-1")
    with pytest.raises(ValueError, match="StepRecoveryReason"):
        coordinator.recover_running_step("command", "interrupted")
    with pytest.raises(ValueError, match="detail must not be empty"):
        coordinator.recover_running_step(
            "command", StepRecoveryReason.INTERRUPTED, detail=" "
        )

    assert coordinator.get("run-1").status is RunStatus.RUNNING
    assert coordinator.get_step("command").status is StepStatus.RUNNING


def test_recover_running_step_rolls_back_step_and_run_when_batch_write_fails(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    coordinator.create("run-1", objective="Execute command", agent_id="agent-1")
    coordinator.add_step(
        "run-1", "command", objective="Wait", command=("sleep", "10"), timeout=1
    )
    original_step = coordinator.start_next_step("run-1")
    original_run = coordinator.get("run-1")
    original_write = store._put_on_connection
    writes = 0

    def fail_after_first_write(connection, kind, key, status, encoded):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("injected persistence failure")
        return original_write(connection, kind, key, status, encoded)

    monkeypatch.setattr(store, "_put_on_connection", fail_after_first_write)

    with pytest.raises(RuntimeError, match="injected persistence failure"):
        coordinator.recover_running_step(
            "command",
            StepRecoveryReason.INTERRUPTED,
            detail="worker process exited before recording a result",
        )

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_step("command") == original_step
    assert reloaded.get("run-1") == original_run


@pytest.mark.parametrize(
    "status", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED]
)
def test_prune_removes_terminal_run_and_steps_only(tmp_path, status) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("remove", objective="Completed work")
    first = coordinator.add_step(
        "remove", "first", objective="First", command=("true",)
    )
    second = coordinator.add_step(
        "remove", "second", objective="Second", command=("true",)
    )
    coordinator.create("keep", objective="Unrelated work")
    kept_step = coordinator.add_step(
        "keep", "kept", objective="Keep", command=("true",)
    )
    if status is RunStatus.CANCELLED:
        terminal = coordinator.cancel("remove")
    else:
        coordinator.transition("remove", RunStatus.RUNNING)
        terminal = coordinator.transition("remove", status)

    removed_run, removed_steps = coordinator.prune("remove")

    assert removed_run == terminal
    assert [step.step_id for step in removed_steps] == [first.step_id, second.step_id]
    assert coordinator.get("remove") is None
    assert coordinator.get_step("first") is None
    assert coordinator.get_step("second") is None
    assert coordinator.get("keep") is not None
    assert coordinator.get_step("kept") == kept_step


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_prune_rejects_active_and_missing_runs_without_mutation(tmp_path, status) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    original = coordinator.create("active", objective="Active work")
    step = coordinator.add_step(
        "active", "step", objective="Pending", command=("true",)
    )
    if status is RunStatus.RUNNING:
        original = coordinator.transition("active", status)

    with pytest.raises(ValueError, match="run is not terminal"):
        coordinator.prune("active")
    with pytest.raises(KeyError, match="run does not exist"):
        coordinator.prune("missing")

    assert coordinator.get("active") == original
    assert coordinator.get_step("step") == step


def test_prune_rolls_back_when_deletion_fails(tmp_path, monkeypatch) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    coordinator.create("run-1", objective="Completed work")
    coordinator.add_step("run-1", "first", objective="First", command=("true",))
    coordinator.add_step("run-1", "second", objective="Second", command=("true",))
    coordinator.cancel("run-1")
    original_run = coordinator.get("run-1")
    original_steps = coordinator.list_steps("run-1")
    original_delete = store._delete_on_connection
    deletions = 0

    def fail_after_first_delete(connection, kind, key):
        nonlocal deletions
        deletions += 1
        if deletions == 2:
            raise RuntimeError("injected deletion failure")
        original_delete(connection, kind, key)

    monkeypatch.setattr(store, "_delete_on_connection", fail_after_first_delete)

    with pytest.raises(RuntimeError, match="injected deletion failure"):
        coordinator.prune("run-1")

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("run-1") == original_run
    assert reloaded.list_steps("run-1") == original_steps


def test_agent_registration_is_durable_and_revisioned(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))

    registered = registry.register("agent-1", label="Build worker")

    assert registered.agent_id == "agent-1"
    assert registered.label == "Build worker"
    assert registered.revision == 1
    assert registered.last_seen is not None
    assert AgentRegistry(StateStore(database)).list_agents() == (registered,)


def test_agent_registration_without_label(tmp_path) -> None:
    registry = AgentRegistry(StateStore(tmp_path / "state.sqlite3"))

    registered = registry.register("agent-1")

    assert registered.agent_id == "agent-1"
    assert registered.label is None
    assert registered.revision == 1
    assert registered.last_seen is not None


def test_agent_heartbeat_updates_last_seen_with_injected_clock(tmp_path) -> None:
    moments = iter(
        (
            datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 12, 12, 5, tzinfo=timezone.utc),
        )
    )
    registry = AgentRegistry(
        StateStore(tmp_path / "state.sqlite3"), clock=lambda: next(moments)
    )
    registered = registry.register("agent-1", label="Worker")

    refreshed = registry.heartbeat("agent-1")

    assert registered.last_seen == "2026-07-12T12:00:00+00:00"
    assert refreshed == Agent(
        "agent-1", "Worker", 2, "2026-07-12T12:05:00+00:00"
    )


def test_agent_heartbeat_rejects_unregistered_id_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    original = registry.register("agent-1")

    with pytest.raises(ValueError, match="agent does not exist: missing"):
        registry.heartbeat("missing")

    assert AgentRegistry(StateStore(database)).list_agents() == (original,)


def test_agent_get_returns_existing_record_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    original = registry.register("agent-1", label="Worker")

    assert AgentRegistry(StateStore(database, read_only=True)).get("agent-1") == original
    assert AgentRegistry(StateStore(database)).get("agent-1") == original


def test_agent_get_returns_none_for_missing_record_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    original = registry.register("agent-1")

    assert AgentRegistry(StateStore(database, read_only=True)).get("missing") is None
    assert AgentRegistry(StateStore(database)).list_agents() == (original,)


def test_agent_get_reads_legacy_record_without_last_seen(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    store.insert("agent", "legacy", status="registered", payload={"label": "Legacy"})

    assert AgentRegistry(StateStore(database, read_only=True)).get("legacy") == Agent(
        "legacy", "Legacy", 1, None
    )


@pytest.mark.parametrize("agent_id", ["", " "])
def test_agent_get_rejects_empty_identity_without_mutation(tmp_path, agent_id) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    original = registry.register("agent-1")

    with pytest.raises(ValueError, match="agent id must not be empty"):
        registry.get(agent_id)

    assert registry.list_agents() == (original,)


def test_agents_are_listed_in_stable_identifier_order(tmp_path) -> None:
    registry = AgentRegistry(StateStore(tmp_path / "state.sqlite3"))
    second = registry.register("agent-b")
    first = registry.register("agent-a", label="First")

    assert registry.list_agents() == (first, second)


def test_agent_list_is_empty_when_no_agents_are_registered(tmp_path) -> None:
    registry = AgentRegistry(StateStore(tmp_path / "state.sqlite3"))

    assert registry.list_agents() == ()


@pytest.mark.parametrize(
    ("agent_id", "label", "message"),
    [
        (" ", None, "agent id must not be empty"),
        ("", None, "agent id must not be empty"),
        ("agent-1", " ", "agent label must not be empty"),
    ],
)
def test_agent_registration_rejects_empty_identity_without_mutation(
    tmp_path, agent_id, label, message
) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))

    with pytest.raises(ValueError, match=message):
        registry.register(agent_id, label=label)

    assert registry.list_agents() == ()


def test_agent_registration_rejects_duplicate_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    registry = AgentRegistry(StateStore(database))
    original = registry.register("agent-1", label="First")

    with pytest.raises(ValueError, match="agent already exists: agent-1"):
        registry.register("agent-1", label="Replacement")

    assert AgentRegistry(StateStore(database)).list_agents() == (original,)


def test_agent_registration_is_atomic_across_registries(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    first = AgentRegistry(StateStore(database))
    competing = AgentRegistry(StateStore(database))

    original = first.register("agent-1", label="Original")
    with pytest.raises(ValueError, match="agent already exists: agent-1"):
        competing.register("agent-1", label="Replacement")

    assert AgentRegistry(StateStore(database)).list_agents() == (original,)


PLAN_PROPOSAL_CONTENT = (
    '{"steps": ['
    '{"objective": "Write the fix", "execution_kind": "command", '
    '"command": ["pytest"], "sandbox_policy": {"kind": "docker"}}, '
    '{"objective": "Summarize the change", "execution_kind": "provider", '
    '"message": {"provider": "ollama", "content": "Summarize the diff"}}'
    "]}"
)


def _propose_test_plan(coordinator, plan_id="plan-1"):
    class Adapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    return coordinator.propose_plan(
        "run-1", plan_id, adapter_resolver=lambda message: Adapter(), provider="ollama"
    )


def test_propose_plan_persists_draft_with_ordered_steps_and_evidence(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    requests = []

    class Adapter:
        def complete(self, request):
            requests.append(request)
            return ChatResponse(
                content=PLAN_PROPOSAL_CONTENT,
                model="served-model",
                raw={"id": "r1"},
            )

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    draft = coordinator.propose_plan(
        "run-1",
        "plan-1",
        adapter_resolver=lambda message: Adapter(),
        provider="ollama",
        model="requested-model",
        temperature=0.2,
        max_tokens=200,
    )

    assert draft.plan_id == "plan-1"
    assert draft.run_id == "run-1"
    assert draft.status == "draft"
    assert draft.revision == 1
    assert draft.steps == (
        PlanStepProposal(
            step_id="plan-1-step-1",
            objective="Write the fix",
            execution_kind="command",
            command=("pytest",),
            sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
        ),
        PlanStepProposal(
            step_id="plan-1-step-2",
            objective="Summarize the change",
            execution_kind="provider",
            message=ProviderMessage(provider="ollama", content="Summarize the diff"),
        ),
    )
    assert draft.evidence["provider"] == "ollama"
    assert draft.evidence["requested_model"] == "requested-model"
    assert draft.evidence["response_model"] == "served-model"
    assert draft.evidence["raw"] == {"id": "r1"}
    assert draft.error is None

    assert requests[0].messages[0].role == "system"
    assert requests[0].messages[0].content == PLAN_PROPOSAL_SYSTEM_PROMPT
    assert requests[0].messages[1] == ChatMessage("user", "Ship the feature")
    assert requests[0].temperature == 0.2
    assert requests[0].max_tokens == 200

    # No steps are queued by a successful plan proposal.
    assert coordinator.list_steps("run-1") == ()
    assert coordinator.get("run-1").status is RunStatus.QUEUED

    reloaded = StateStore(database).get("plan", "plan-1")
    assert reloaded.status == "draft"
    assert reloaded.payload["steps"] == [
        {
            "step_id": "plan-1-step-1",
            "objective": "Write the fix",
            "execution_kind": "command",
            "command": ["pytest"],
            "sandbox_policy": {
                "kind": "docker",
                "image": "python:3.12-slim",
                "mounts": [],
                "working_dir": None,
                "env_passthrough": [],
                "network_enabled": False,
            },
        },
        {
            "step_id": "plan-1-step-2",
            "objective": "Summarize the change",
            "execution_kind": "provider",
            "message": {"provider": "ollama", "content": "Summarize the diff"},
        },
    ]


def test_propose_plan_defaults_objective_to_run_objective_and_supports_override(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    sent = []

    class Adapter:
        def complete(self, request):
            sent.append(request.messages[1].content)
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Run objective")

    coordinator.propose_plan(
        "run-1", "plan-default", adapter_resolver=lambda message: Adapter(), provider="ollama"
    )
    coordinator.propose_plan(
        "run-1",
        "plan-override",
        adapter_resolver=lambda message: Adapter(),
        provider="ollama",
        objective="Overridden planning objective",
    )

    assert sent == ["Run objective", "Overridden planning objective"]


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("not json at all", "plan proposal is not valid JSON"),
        ("[]", "plan proposal must be a JSON object"),
        ("{}", "plan proposal must include a non-empty 'steps' list"),
        ('{"steps": []}', "plan proposal must include a non-empty 'steps' list"),
        ('{"steps": ["not-an-object"]}', "plan proposal step 0 must be a JSON object"),
        (
            '{"steps": [{"execution_kind": "command"}]}',
            "plan proposal step 0 objective must be a non-empty string",
        ),
        (
            '{"steps": [{"objective": "  ", "execution_kind": "command"}]}',
            "plan proposal step 0 objective must be a non-empty string",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "branch"}]}',
            "plan proposal step 0 execution_kind must be 'command' or 'provider'",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "command", '
            '"sandbox_policy": {"kind": "docker"}}]}',
            "plan proposal step 0 command execution requires 'command'",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "command", '
            '"command": ["pytest"]}]}',
            "plan proposal step 0 command execution requires 'sandbox_policy'",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "command", '
            '"command": ["pytest"], "sandbox_policy": {"kind": "docker"}, '
            '"message": {"provider": "ollama", "content": "hi"}}]}',
            "plan proposal step 0 command execution must not include 'message'",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "command", '
            '"command": [], "sandbox_policy": {"kind": "docker"}}]}',
            "plan proposal step 0 step command must not be empty",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "command", '
            '"command": ["pytest"], "sandbox_policy": {"kind": "unknown"}}]}',
            "plan proposal step 0 step sandbox policy kind is invalid",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "provider"}]}',
            "plan proposal step 0 provider execution requires 'message'",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "provider", '
            '"message": {"provider": "ollama", "content": "hi"}, '
            '"command": ["pytest"]}]}',
            "plan proposal step 0 provider execution must not include",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "provider", '
            '"message": {"provider": "", "content": "hi"}}]}',
            "plan proposal step 0 step provider must be a non-empty string",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "provider", '
            '"message": {"provider": "ollama", "required_capability": "general", '
            '"content": "hi"}}]}',
            "plan proposal step 0 step provider message requires exactly one of "
            "provider or required_capability",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "provider", '
            '"message": {"content": "hi"}}]}',
            "plan proposal step 0 step provider message requires exactly one of "
            "provider or required_capability",
        ),
        (
            '{"steps": [{"objective": "Do it", "execution_kind": "provider", '
            '"message": {"required_capability": "telekinesis", "content": "hi"}}]}',
            "plan proposal step 0 step required capability is not declared by any "
            "configured provider: telekinesis",
        ),
    ],
)
def test_propose_plan_rejects_malformed_proposals_and_preserves_raw_evidence(
    tmp_path, content, match
) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse(content=content, model="served-model", raw={"id": "bad"})

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    with pytest.raises(PlanProposalError, match=match):
        coordinator.propose_plan(
            "run-1", "plan-bad", adapter_resolver=lambda message: Adapter(), provider="ollama"
        )

    # No steps are queued when the proposal is malformed.
    assert coordinator.list_steps("run-1") == ()
    assert coordinator.get("run-1").status is RunStatus.QUEUED

    record = StateStore(database).get("plan", "plan-bad")
    assert record is not None
    assert record.status == "invalid"
    assert record.payload["evidence"]["content"] == content
    assert record.payload["evidence"]["raw"] == {"id": "bad"}
    assert match in record.payload["error"]
    assert "steps" not in record.payload


def test_propose_plan_rejects_duplicate_plan_id_without_dispatching(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    dispatch_count = 0

    class Adapter:
        def complete(self, request):
            nonlocal dispatch_count
            dispatch_count += 1
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    coordinator.propose_plan(
        "run-1", "plan-1", adapter_resolver=lambda message: Adapter(), provider="ollama"
    )

    with pytest.raises(ValueError, match="plan already exists: plan-1"):
        coordinator.propose_plan(
            "run-1", "plan-1", adapter_resolver=lambda message: Adapter(), provider="ollama"
        )

    assert dispatch_count == 1


def test_propose_plan_rejects_missing_run(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="run does not exist: missing"):
        coordinator.propose_plan(
            "missing",
            "plan-1",
            adapter_resolver=lambda message: (_ for _ in ()).throw(AssertionError("no dispatch")),
            provider="ollama",
        )


def test_propose_plan_rejects_terminal_run_without_dispatching(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    coordinator.transition("run-1", RunStatus.RUNNING)
    coordinator.transition("run-1", RunStatus.CANCELLED)

    with pytest.raises(ValueError, match="cannot propose a plan for terminal run: run-1"):
        coordinator.propose_plan(
            "run-1",
            "plan-1",
            adapter_resolver=lambda message: (_ for _ in ()).throw(AssertionError("no dispatch")),
            provider="ollama",
        )


def test_get_plan_returns_a_reviewable_draft_in_stable_order(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse(
                content=PLAN_PROPOSAL_CONTENT,
                model="served-model",
                raw={"id": "r1"},
            )

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    coordinator.propose_plan(
        "run-1", "plan-1", adapter_resolver=lambda message: Adapter(), provider="ollama"
    )

    draft = coordinator.get_plan("plan-1")

    assert draft == PlanDraft(
        plan_id="plan-1",
        run_id="run-1",
        status="draft",
        revision=1,
        steps=(
            PlanStepProposal(
                step_id="plan-1-step-1",
                objective="Write the fix",
                execution_kind="command",
                command=("pytest",),
                sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
            ),
            PlanStepProposal(
                step_id="plan-1-step-2",
                objective="Summarize the change",
                execution_kind="provider",
                message=ProviderMessage(provider="ollama", content="Summarize the diff"),
            ),
        ),
        evidence={
            "provider": "ollama",
            "requested_model": None,
            "response_model": "served-model",
            "content": draft.evidence["content"],
            "raw": {"id": "r1"},
        },
        error=None,
    )

    # Read-only: no run or step state was created or changed by inspection.
    assert coordinator.list_steps("run-1") == ()
    assert coordinator.get("run-1").status is RunStatus.QUEUED


def test_get_plan_returns_an_invalid_draft_with_recorded_error(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse(content="not json at all", model="served-model")

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    with pytest.raises(PlanProposalError):
        coordinator.propose_plan(
            "run-1", "plan-1", adapter_resolver=lambda message: Adapter(), provider="ollama"
        )

    draft = coordinator.get_plan("plan-1")

    assert draft.plan_id == "plan-1"
    assert draft.status == "invalid"
    assert draft.steps == ()
    assert draft.error == "plan proposal is not valid JSON: Expecting value: line 1 column 1 (char 0)"
    assert draft.evidence["content"] == "not json at all"


def test_get_plan_returns_none_for_an_absent_plan(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ship the feature")

    assert coordinator.get_plan("missing") is None


def test_propose_plan_materializes_deterministic_collision_free_step_ids(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse(
                content=(
                    '{"steps": ['
                    '{"objective": "First", "execution_kind": "command", '
                    '"command": ["pytest"], "sandbox_policy": {"kind": "docker"}}, '
                    '{"objective": "Second", "execution_kind": "provider", '
                    '"message": {"provider": "ollama", "content": "hi"}}, '
                    '{"objective": "Third", "execution_kind": "command", '
                    '"command": ["ls"], "sandbox_policy": {"kind": "podman"}}'
                    "]}"
                )
            )

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    draft = coordinator.propose_plan(
        "run-1", "plan-xyz", adapter_resolver=lambda message: Adapter(), provider="ollama"
    )

    step_ids = [step.step_id for step in draft.steps]
    assert step_ids == ["plan-xyz-step-1", "plan-xyz-step-2", "plan-xyz-step-3"]
    # Materialized ids are unique by construction (namespaced under the unique plan id).
    assert len(set(step_ids)) == len(step_ids)


def test_get_plan_reconstructs_executable_payload_after_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"

    class Adapter:
        def complete(self, request):
            return ChatResponse(content=PLAN_PROPOSAL_CONTENT)

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    coordinator.propose_plan(
        "run-1", "plan-1", adapter_resolver=lambda message: Adapter(), provider="ollama"
    )

    reloaded = _RunCoordinator(StateStore(database)).get_plan("plan-1")

    assert reloaded.steps == (
        PlanStepProposal(
            step_id="plan-1-step-1",
            objective="Write the fix",
            execution_kind="command",
            command=("pytest",),
            sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER),
        ),
        PlanStepProposal(
            step_id="plan-1-step-2",
            objective="Summarize the change",
            execution_kind="provider",
            message=ProviderMessage(provider="ollama", content="Summarize the diff"),
        ),
    )


def test_accept_plan_atomically_materializes_ordered_steps_with_provenance(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    coordinator.add_step("run-1", "manual", objective="Existing", command=("true",))
    _propose_test_plan(coordinator)

    accepted, steps = coordinator.accept_plan(
        "plan-1", expected_revision=1, agent_id="agent-1"
    )

    assert accepted.status == "accepted"
    assert accepted.revision == 2
    assert accepted.decision_agent_id == "agent-1"
    assert [step.step_id for step in steps] == ["plan-1-step-1", "plan-1-step-2"]
    assert [step.position for step in steps] == [2, 3]
    assert all(step.status is StepStatus.QUEUED for step in steps)
    assert steps[0].command == ("pytest",)
    assert steps[0].sandbox_policy == SandboxPolicy(kind=SandboxKind.DOCKER)
    assert steps[1].message == ProviderMessage(
        provider="ollama", content="Summarize the diff"
    )

    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_plan("plan-1") == accepted
    decision = reloaded.list_history("run-1")[-1]
    assert decision.transition == "plan_accepted"
    assert decision.status == "accepted"
    assert decision.plan_id == "plan-1"
    assert decision.agent_id == "agent-1"


def test_plan_proposal_with_required_capability_parses_and_materializes(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    content = (
        '{"steps": ['
        '{"objective": "Summarize the change", "execution_kind": "provider", '
        '"message": {"required_capability": "general", "content": "Summarize the diff"}}'
        "]}"
    )

    class Adapter:
        def complete(self, request):
            return ChatResponse(content=content)

    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")

    draft = coordinator.propose_plan(
        "run-1", "plan-capability", adapter_resolver=lambda message: Adapter(), provider="ollama"
    )

    assert draft.steps == (
        PlanStepProposal(
            step_id="plan-capability-step-1",
            objective="Summarize the change",
            execution_kind="provider",
            message=ProviderMessage(
                provider=None, content="Summarize the diff", required_capability="general"
            ),
        ),
    )

    accepted, steps = coordinator.accept_plan("plan-capability", expected_revision=1)

    assert steps[0].message.provider is None
    assert steps[0].message.required_capability == "general"
    assert RunCoordinator(StateStore(database)).get_step(steps[0].step_id) == steps[0]


def test_reject_plan_records_provenance_and_materializes_no_steps(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    _propose_test_plan(coordinator)

    rejected = coordinator.reject_plan(
        "plan-1", expected_revision=1, agent_id="agent-2"
    )

    assert rejected.status == "rejected"
    assert rejected.revision == 2
    assert rejected.decision_agent_id == "agent-2"
    assert coordinator.list_steps("run-1") == ()
    decision = coordinator.list_history("run-1")[-1]
    assert decision.transition == "plan_rejected"
    assert decision.plan_id == "plan-1"
    assert decision.agent_id == "agent-2"


def test_plan_decision_rejects_stale_revision_without_mutation(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    original = _propose_test_plan(coordinator)
    before_history = coordinator.list_history("run-1")

    with pytest.raises(ValueError, match="plan decision conflict: plan-1"):
        coordinator.accept_plan("plan-1", expected_revision=2)

    assert coordinator.get_plan("plan-1") == original
    assert coordinator.list_steps("run-1") == ()
    assert coordinator.list_history("run-1") == before_history


def test_competing_plan_decisions_have_exactly_one_atomic_winner(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    _propose_test_plan(coordinator)

    def accept():
        return RunCoordinator(StateStore(database)).accept_plan(
            "plan-1", expected_revision=1
        )

    def reject():
        return RunCoordinator(StateStore(database)).reject_plan(
            "plan-1", expected_revision=1
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(accept), pool.submit(reject)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(("success", future.result()))
        except ValueError as error:
            outcomes.append(("conflict", str(error)))

    assert [kind for kind, _ in outcomes].count("success") == 1
    assert [kind for kind, _ in outcomes].count("conflict") == 1
    reloaded = RunCoordinator(StateStore(database))
    plan = reloaded.get_plan("plan-1")
    assert plan.revision == 2
    if plan.status == "accepted":
        assert [step.step_id for step in reloaded.list_steps("run-1")] == [
            "plan-1-step-1",
            "plan-1-step-2",
        ]
    else:
        assert plan.status == "rejected"
        assert reloaded.list_steps("run-1") == ()
    decisions = [
        entry
        for entry in reloaded.list_history("run-1")
        if entry.transition in {"plan_accepted", "plan_rejected"}
    ]
    assert len(decisions) == 1


def test_accept_plan_rejects_existing_step_identity_without_partial_mutation(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    original = _propose_test_plan(coordinator)
    manual = coordinator.add_step(
        "run-1", "plan-1-step-2", objective="Manual collision", command=("true",)
    )
    before_history = coordinator.list_history("run-1")

    with pytest.raises(ValueError, match="plan step already exists: plan-1-step-2"):
        coordinator.accept_plan("plan-1", expected_revision=1)

    assert coordinator.get_plan("plan-1") == original
    assert coordinator.list_steps("run-1") == (manual,)
    assert coordinator.get_step("plan-1-step-1") is None
    assert coordinator.list_history("run-1") == before_history


def test_accepted_plan_step_executes_through_existing_lifecycle(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ship the feature")
    _propose_test_plan(coordinator)
    coordinator.accept_plan("plan-1", expected_revision=1)

    class Executor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(tuple(argv), 0, "passed", "")

    step, run = coordinator.execute_next_step(
        "run-1", sandbox_resolver=lambda policy: Executor()
    )

    assert step.step_id == "plan-1-step-1"
    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.RUNNING
    assert any(
        entry.transition == "step_succeeded" and entry.step_id == step.step_id
        for entry in coordinator.list_history("run-1")
    )


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_plan_decision_rejects_missing_invalid_and_already_decided_drafts(
    tmp_path, decision
) -> None:
    database = tmp_path / f"{decision}.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ship the feature")
    method = coordinator.accept_plan if decision == "accept" else coordinator.reject_plan

    with pytest.raises(KeyError, match="plan does not exist: missing"):
        method("missing", expected_revision=1)

    StateStore(database).insert(
        "plan",
        "invalid",
        status="invalid",
        payload={"run_id": "run-1", "objective": "bad", "error": "malformed"},
    )
    with pytest.raises(ValueError, match="plan is not a reviewable draft: invalid"):
        method("invalid", expected_revision=1)

    _propose_test_plan(coordinator)
    coordinator.reject_plan("plan-1", expected_revision=1)
    with pytest.raises(ValueError, match="plan is not a reviewable draft: plan-1"):
        method("plan-1", expected_revision=2)


def test_add_step_persists_artifact_declarations_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=(("/host/data", "/workspace"),))

    created = coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )

    assert created.artifact_declarations == (
        ArtifactDeclaration(name="out", path="/workspace/out.txt"),
    )
    reloaded = RunCoordinator(StateStore(database)).get_step("command")
    assert reloaded is not None
    assert reloaded.artifact_declarations == created.artifact_declarations


def test_artifact_declarations_require_a_persisted_sandbox_policy(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    run = coordinator.create("run-1", objective="Build feature")

    with pytest.raises(ValueError, match="require a persisted sandbox policy"):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Produce a file",
            command=("true",),
            artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
        )

    assert coordinator.get("run-1") == run
    assert coordinator.list_steps("run-1") == ()


def test_artifact_declarations_require_at_least_one_mount(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")

    with pytest.raises(ValueError, match="at least one persisted sandbox mount"):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Produce a file",
            command=("true",),
            sandbox_policy=SandboxPolicy(kind=SandboxKind.DOCKER, working_dir="/workspace"),
            artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
        )

    assert coordinator.list_steps("run-1") == ()


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/workspace/../secrets", "/workspace-other/out.txt"],
)
def test_artifact_declaration_path_must_resolve_within_a_mount(tmp_path, path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=(("/host/data", "/workspace"),))

    with pytest.raises(
        ValueError, match="does not resolve within a persisted sandbox mount"
    ):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Produce a file",
            command=("true",),
            sandbox_policy=policy,
            artifacts=[ArtifactDeclaration(name="out", path=path)],
        )

    assert coordinator.list_steps("run-1") == ()


@pytest.mark.parametrize(
    ("declarations", "message"),
    [
        ([{"name": "", "path": "/workspace/out.txt"}], "non-empty identifier-safe string"),
        ([{"name": "bad name", "path": "/workspace/out.txt"}], "identifier-safe string"),
        (
            [
                {"name": "out", "path": "/workspace/a.txt"},
                {"name": "out", "path": "/workspace/b.txt"},
            ],
            "name must be unique: out",
        ),
        ([{"name": "out", "path": "relative/out.txt"}], "non-empty absolute path"),
        (
            [{"unexpected": "field", "name": "out", "path": "/workspace/out.txt"}],
            "unknown fields",
        ),
    ],
)
def test_artifact_declaration_validation_rejects_invalid_input_without_mutation(
    tmp_path, declarations, message
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=(("/host/data", "/workspace"),))

    with pytest.raises(ValueError, match=message):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Produce a file",
            command=("true",),
            sandbox_policy=policy,
            artifacts=declarations,
        )

    assert coordinator.list_steps("run-1") == ()


def test_artifact_declarations_are_rejected_for_provider_message_steps(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ask a model")

    with pytest.raises(ValueError, match="require a persisted sandbox policy"):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Summarize",
            message=ProviderMessage(provider="local", content="Hello"),
            artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
        )

    assert coordinator.list_steps("run-1") == ()


def test_successful_command_captures_declared_artifact_with_hash_and_size(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    content = b"hello artifact\n"
    (host_dir / "out.txt").write_bytes(content)

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")

    step, run = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED
    artifacts = coordinator.list_artifacts("run-1")
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.run_id == "run-1"
    assert artifact.step_id == "command"
    assert artifact.name == "out"
    assert artifact.source_path == "/workspace/out.txt"
    assert artifact.status is ArtifactStatus.CAPTURED
    assert artifact.content_hash == hashlib.sha256(content).hexdigest()
    assert artifact.size_bytes == len(content)
    assert artifact.size_limit_bytes is None
    stored = coordinator._artifact_storage_dir / artifact.artifact_id
    assert stored.read_bytes() == content


def test_missing_declared_artifact_is_recorded_absent_without_failing_step(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")

    step, _ = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    assert step.status is StepStatus.SUCCEEDED
    artifact = coordinator.list_artifacts("run-1")[0]
    assert artifact.status is ArtifactStatus.ABSENT
    assert artifact.content_hash is None
    assert artifact.size_bytes is None


def test_oversized_declared_artifact_is_rejected_without_streaming_content(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    content = b"0123456789"
    (host_dir / "out.txt").write_bytes(content)

    coordinator = _RunCoordinator(
        StateStore(tmp_path / "state.sqlite3"), artifact_size_limit_bytes=4
    )
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")

    step, _ = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    assert step.status is StepStatus.SUCCEEDED
    artifact = coordinator.list_artifacts("run-1")[0]
    assert artifact.status is ArtifactStatus.REJECTED
    assert artifact.content_hash is None
    assert artifact.size_bytes == len(content)
    assert artifact.size_limit_bytes == 4
    assert list(coordinator._artifact_storage_dir.glob("*")) == []


def test_failed_command_step_does_not_capture_artifacts(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    (host_dir / "out.txt").write_bytes(b"data")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("false",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")

    step, _ = coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "false"), 1, "", "boom")
    )

    assert step.status is StepStatus.FAILED
    assert coordinator.list_artifacts("run-1") == ()


def test_artifact_capture_records_redacted_history(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    (host_dir / "out.txt").write_bytes(b"data")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")
    coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    entries = [
        entry
        for entry in coordinator.list_history("run-1")
        if entry.transition == "artifact_captured"
    ]
    assert len(entries) == 1
    assert entries[0].step_id == "command"
    assert entries[0].artifact_name == "out"
    assert entries[0].status == "captured"


def test_artifact_declarations_and_sandbox_policy_survive_second_step_dispatch(
    tmp_path,
) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    (host_dir / "second.txt").write_bytes(b"second-content")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1", "first", objective="First", command=("true",), sandbox_policy=policy,
    )
    coordinator.add_step(
        "run-1",
        "second",
        objective="Second",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/second.txt")],
    )

    coordinator.start_next_step("run-1")
    coordinator.complete_step_from_result(
        "first", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    dispatched_second = coordinator.start_next_step("run-1")
    assert dispatched_second is not None
    assert dispatched_second.sandbox_policy == policy
    assert dispatched_second.artifact_declarations == (
        ArtifactDeclaration(name="out", path="/workspace/second.txt"),
    )

    step, _ = coordinator.complete_step_from_result(
        "second", SandboxResult(("docker", "run", "true"), 0, "", "")
    )
    assert step.status is StepStatus.SUCCEEDED
    artifacts = coordinator.list_artifacts("run-1", step_id="second")
    assert len(artifacts) == 1
    assert artifacts[0].status is ArtifactStatus.CAPTURED


def test_list_artifacts_requires_an_existing_run(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="run does not exist: missing"):
        coordinator.list_artifacts("missing")


def test_run_coordinator_rejects_non_positive_artifact_size_limit(tmp_path) -> None:
    with pytest.raises(ValueError, match="artifact size limit must be positive"):
        _RunCoordinator(StateStore(tmp_path / "state.sqlite3"), artifact_size_limit_bytes=0)


def test_provider_response_artifact_declaration_persists_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Ask a model")

    created = coordinator.add_step(
        "run-1",
        "model",
        objective="Summarize",
        message=ProviderMessage(provider="local", content="Hello"),
        response_artifact_name="answer",
    )

    assert created.response_artifact_name == "answer"
    assert RunCoordinator(StateStore(database)).get_step("model") == created


@pytest.mark.parametrize("name", ["", "bad name", "path/name"])
def test_provider_response_artifact_name_validation_is_pre_mutation(tmp_path, name) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    original = coordinator.create("run-1", objective="Ask a model")

    with pytest.raises(ValueError, match="non-empty identifier-safe string"):
        coordinator.add_step(
            "run-1",
            "model",
            objective="Summarize",
            message=ProviderMessage(provider="local", content="Hello"),
            response_artifact_name=name,
        )

    assert coordinator.get("run-1") == original
    assert coordinator.list_steps("run-1") == ()


def test_response_artifact_declaration_is_rejected_for_command_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Run a command")

    with pytest.raises(ValueError, match="require a provider message"):
        coordinator.add_step(
            "run-1",
            "command",
            objective="Execute",
            command=("true",),
            response_artifact_name="answer",
        )

    assert coordinator.list_steps("run-1") == ()


def test_provider_response_artifact_captures_content_with_routing_context_and_usage(
    tmp_path,
) -> None:
    database = tmp_path / "state.sqlite3"
    resolved_messages = []
    requests = []

    class Executor:
        def execute(self, argv, *, timeout=None):
            return SandboxResult(tuple(argv), 0, "context-output", "")

    class Adapter:
        def complete(self, request):
            requests.append(request)
            return ChatResponse(
                "Durable answer\n",
                model="served-model",
                raw={"provider_envelope": "preserved"},
                usage=ChatUsage(
                    available=True,
                    input_tokens=9,
                    output_tokens=3,
                    raw={"prompt_tokens": 9, "completion_tokens": 3},
                ),
            )

    specs = (
        ProviderSpec(
            kind=ProviderKind.ANTHROPIC,
            model="routed-model",
            capabilities=("reasoning",),
        ),
    )
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Compose work")
    coordinator.add_step("run-1", "context", objective="Gather", command=("true",))
    coordinator.add_step(
        "run-1",
        "model",
        objective="Reason",
        message=ProviderMessage(
            provider=None,
            content="private request",
            required_capability="reasoning",
        ),
        context_step_ids=("context",),
        response_artifact_name="answer",
    )
    coordinator.add_step("run-1", "tail", objective="Finish", command=("true",))
    coordinator.execute_next_step("run-1", Executor())

    step, run = coordinator.execute_next_step(
        "run-1",
        adapter_resolver=lambda message: resolved_messages.append(message) or Adapter(),
        routing_policy=ProviderRoutingPolicy((ProviderKind.ANTHROPIC,)),
        provider_specs=specs,
    )

    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.RUNNING
    assert step.response_artifact_name == "answer"
    assert RunCoordinator(StateStore(database)).get_step("model") == step
    assert step.output == {
        "content": "Durable answer\n",
        "model": "served-model",
        "raw": {"provider_envelope": "preserved"},
        "usage": {
            "available": True,
            "input_tokens": 9,
            "output_tokens": 3,
            "raw": {"prompt_tokens": 9, "completion_tokens": 3},
            "unavailable_reason": None,
        },
    }
    assert resolved_messages[0].provider == "anthropic"
    assert requests[0].messages[-2:] == (
        ChatMessage("assistant", "exit_code=0\nstdout:\ncontext-output\nstderr:\n"),
        ChatMessage("user", "private request"),
    )
    artifact = coordinator.list_artifacts("run-1", step_id="model")[0]
    content = b"Durable answer\n"
    assert artifact.name == "answer"
    assert artifact.source_path == "response.content"
    assert artifact.status is ArtifactStatus.CAPTURED
    assert artifact.content_hash == hashlib.sha256(content).hexdigest()
    assert artifact.size_bytes == len(content)
    assert (coordinator._artifact_storage_dir / artifact.artifact_id).read_bytes() == content
    captured = next(
        entry
        for entry in coordinator.list_history("run-1")
        if entry.transition == "artifact_captured" and entry.step_id == "model"
    )
    assert captured.execution_kind == "provider"
    assert captured.artifact_name == "answer"
    assert "private request" not in repr(captured)
    assert "provider_envelope" not in repr(captured)
    _, completed_run = coordinator.execute_next_step("run-1", Executor())
    assert completed_run.status is RunStatus.SUCCEEDED


def test_provider_response_artifact_size_rejection_does_not_fail_step(tmp_path) -> None:
    class Adapter:
        def complete(self, request):
            return ChatResponse("oversized")

    coordinator = _RunCoordinator(
        StateStore(tmp_path / "state.sqlite3"), artifact_size_limit_bytes=4
    )
    coordinator.create("run-1", objective="Ask a model")
    coordinator.add_step(
        "run-1",
        "model",
        objective="Answer",
        message=ProviderMessage(provider="local", content="Hello"),
        response_artifact_name="answer",
    )

    step, run = coordinator.execute_next_step(
        "run-1", adapter_resolver=lambda _: Adapter()
    )

    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED
    artifact = coordinator.list_artifacts("run-1")[0]
    assert artifact.status is ArtifactStatus.REJECTED
    assert artifact.size_bytes == len("oversized".encode("utf-8"))
    assert artifact.size_limit_bytes == 4
    assert list(coordinator._artifact_storage_dir.glob("*")) == []


def test_failed_provider_step_does_not_capture_response_artifact(tmp_path) -> None:
    class Adapter:
        def complete(self, request):
            raise RuntimeError("provider unavailable")

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Ask a model")
    coordinator.add_step(
        "run-1",
        "model",
        objective="Answer",
        message=ProviderMessage(provider="local", content="Hello"),
        response_artifact_name="answer",
    )

    step, _ = coordinator.execute_next_step(
        "run-1", adapter_resolver=lambda _: Adapter()
    )

    assert step.status is StepStatus.FAILED
    assert step.response_artifact_name == "answer"
    assert coordinator.list_artifacts("run-1") == ()


def test_read_artifact_content_returns_captured_bytes(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    content = b"hello artifact\n"
    (host_dir / "out.txt").write_bytes(content)

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")
    coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    artifact = coordinator.list_artifacts("run-1")[0]
    assert coordinator.read_artifact_content(artifact.artifact_id) == content


def test_read_artifact_content_rejects_unknown_artifact(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))

    with pytest.raises(KeyError, match="artifact does not exist: missing"):
        coordinator.read_artifact_content("missing")


def test_read_artifact_content_rejects_absent_artifact(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")
    coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    artifact = coordinator.list_artifacts("run-1")[0]
    assert artifact.status is ArtifactStatus.ABSENT
    with pytest.raises(ValueError, match="no exportable content"):
        coordinator.read_artifact_content(artifact.artifact_id)


def test_read_artifact_content_rejects_rejected_artifact(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    (host_dir / "out.txt").write_bytes(b"0123456789")

    coordinator = _RunCoordinator(
        StateStore(tmp_path / "state.sqlite3"), artifact_size_limit_bytes=4
    )
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")
    coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    artifact = coordinator.list_artifacts("run-1")[0]
    assert artifact.status is ArtifactStatus.REJECTED
    with pytest.raises(ValueError, match="no exportable content"):
        coordinator.read_artifact_content(artifact.artifact_id)


def test_read_artifact_content_rejects_missing_local_storage(tmp_path) -> None:
    host_dir = tmp_path / "host"
    host_dir.mkdir()
    content = b"hello artifact\n"
    (host_dir / "out.txt").write_bytes(content)

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    policy = SandboxPolicy(kind=SandboxKind.DOCKER, mounts=((str(host_dir), "/workspace"),))
    coordinator.add_step(
        "run-1",
        "command",
        objective="Produce a file",
        command=("true",),
        sandbox_policy=policy,
        artifacts=[ArtifactDeclaration(name="out", path="/workspace/out.txt")],
    )
    coordinator.start_next_step("run-1")
    coordinator.complete_step_from_result(
        "command", SandboxResult(("docker", "run", "true"), 0, "", "")
    )

    artifact = coordinator.list_artifacts("run-1")[0]
    (coordinator._artifact_storage_dir / artifact.artifact_id).unlink()
    with pytest.raises(ValueError, match="missing from local storage"):
        coordinator.read_artifact_content(artifact.artifact_id)


def test_add_step_rejects_delegation_combined_with_command_or_message(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")

    with pytest.raises(ValueError, match="exactly one of command, provider message"):
        coordinator.add_step(
            "run-1",
            "bad",
            objective="Ambiguous",
            command=("true",),
            delegation=DelegationSpec(child_objective="Do the child work"),
        )
    with pytest.raises(ValueError, match="exactly one of command, provider message"):
        coordinator.add_step(
            "run-1",
            "bad2",
            objective="Ambiguous",
            message=ProviderMessage("local", "hi"),
            delegation=DelegationSpec(child_objective="Do the child work"),
        )
    with pytest.raises(ValueError, match="exactly one of command, provider message"):
        coordinator.add_step("run-1", "bad3", objective="Nothing declared")
    assert coordinator.list_steps("run-1") == ()


def test_add_step_rejects_empty_delegation_child_objective(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")

    with pytest.raises(ValueError, match="child objective must be a non-empty string"):
        coordinator.add_step(
            "run-1", "bad", objective="Delegate", delegation=DelegationSpec(child_objective="  ")
        )
    assert coordinator.list_steps("run-1") == ()


def test_add_step_rejects_unregistered_delegation_target_agent(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")

    with pytest.raises(ValueError, match="agent is not registered"):
        coordinator.add_step(
            "run-1",
            "bad",
            objective="Delegate",
            delegation=DelegationSpec(
                child_objective="Do the child work", target_agent_id="ghost-agent"
            ),
        )
    assert coordinator.list_steps("run-1") == ()


def test_add_step_rejects_delegation_to_parent_run_agent_without_mutation(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work", agent_id="agent-1")

    with pytest.raises(ValueError, match="target agent matches the parent run agent"):
        coordinator.add_step(
            "run-1",
            "bad",
            objective="Delegate",
            delegation=DelegationSpec(
                child_objective="Do the child work", target_agent_id="agent-1"
            ),
        )

    assert coordinator.list_steps("run-1") == ()


def test_add_step_rejects_delegation_back_to_ancestor_agent_without_mutation(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work", agent_id="agent-1")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate to agent 2",
        delegation=DelegationSpec(
            child_objective="Review the work", target_agent_id="agent-2"
        ),
    )
    coordinator.execute_next_step("run-1")

    with pytest.raises(ValueError, match="target agent would create an ancestor cycle"):
        coordinator.add_step(
            "delegate-child",
            "bad-cycle",
            objective="Delegate back to agent 1",
            delegation=DelegationSpec(
                child_objective="Rework the parent", target_agent_id="agent-1"
            ),
        )

    assert coordinator.list_steps("delegate-child") == ()


def test_add_step_persists_delegation_declaration_across_restart(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Delegate part of the work")

    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change", target_agent_id="agent-1"),
    )

    reloaded = RunCoordinator(StateStore(database)).get_step("delegate")
    assert reloaded is not None
    assert reloaded.command is None
    assert reloaded.message is None
    assert reloaded.delegation == DelegationSpec(
        child_objective="Review the change", target_agent_id="agent-1"
    )
    assert reloaded.delegated_run_id is None
    assert reloaded.status is StepStatus.QUEUED


def test_execute_next_step_atomically_spawns_and_links_child_run(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work", agent_id="agent-1")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )

    step, run = coordinator.execute_next_step("run-1")

    assert step.status is StepStatus.RUNNING
    assert step.delegated_run_id == "delegate-child"
    assert run.status is RunStatus.RUNNING

    child = coordinator.get("delegate-child")
    assert child is not None
    assert child.objective == "Review the change"
    assert child.status is RunStatus.QUEUED
    assert child.parent_run_id == "run-1"
    assert child.parent_step_id == "delegate"
    assert child.agent_id is None

    history = coordinator.list_history("run-1")
    assert any(entry.transition == "step_delegated" for entry in history)
    child_history = coordinator.list_history("delegate-child")
    assert child_history[0].transition == "created"


def test_execute_next_step_assigns_delegation_target_agent_to_child_run(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change", target_agent_id="agent-1"),
    )

    coordinator.execute_next_step("run-1")

    child = coordinator.get("delegate-child")
    assert child is not None
    assert child.agent_id == "agent-1"


def test_execute_next_step_on_pending_delegation_step_raises_delegation_pending(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")

    with pytest.raises(DelegationPendingError, match="delegate -> delegate-child"):
        coordinator.execute_next_step("run-1")


@pytest.mark.parametrize("child_status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_delegation_reconciliation_waits_for_active_child_without_mutation(
    tmp_path, child_status
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    if child_status is RunStatus.RUNNING:
        coordinator.transition("delegate-child", RunStatus.RUNNING)
    before_step = coordinator.get_step("delegate")
    before_run = coordinator.get("run-1")

    with pytest.raises(
        DelegationPendingError,
        match=rf"delegate -> delegate-child \({child_status.value}\)",
    ):
        coordinator.execute_next_step("run-1")

    assert coordinator.get_step("delegate") == before_step
    assert coordinator.get("run-1") == before_run


def test_succeeded_child_completes_parent_delegation_with_durable_evidence(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work", agent_id="agent-1")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(
            child_objective="Review the change", target_agent_id="agent-2"
        ),
    )
    coordinator.execute_next_step("run-1")
    coordinator.transition("delegate-child", RunStatus.RUNNING)
    coordinator.transition(
        "delegate-child",
        RunStatus.SUCCEEDED,
        output={"summary": "review passed"},
    )

    step, run = coordinator.execute_next_step("run-1")

    assert step.status is StepStatus.SUCCEEDED
    assert step.output == {
        "child_run_id": "delegate-child",
        "child_status": "succeeded",
        "child_output": {"summary": "review passed"},
        "child_agent_id": "agent-2",
    }
    assert run.status is RunStatus.SUCCEEDED
    assert run.output == {"completed_steps": 1}
    assert [entry.transition for entry in coordinator.list_history("run-1")][-2:] == [
        "step_succeeded",
        "run_succeeded",
    ]
    assert all(
        entry.execution_kind == "delegation"
        for entry in coordinator.list_history("run-1")[-2:]
    )


def test_succeeded_child_advances_parent_to_its_next_queued_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate then deliver")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.add_step("run-1", "deliver", objective="Deliver", command=("true",))
    coordinator.execute_next_step("run-1")
    coordinator.transition("delegate-child", RunStatus.RUNNING)
    coordinator.transition("delegate-child", RunStatus.SUCCEEDED, output={"ok": True})

    step, run = coordinator.execute_next_step("run-1")

    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.RUNNING
    assert coordinator.get_step("deliver").status is StepStatus.QUEUED
    final_step, final_run = coordinator.execute_next_step("run-1", _StubExecutor())
    assert final_step.status is StepStatus.SUCCEEDED
    assert final_run.status is RunStatus.SUCCEEDED


@pytest.mark.parametrize("child_status", [RunStatus.FAILED, RunStatus.CANCELLED])
def test_unsuccessful_child_fails_parent_delegation_explicitly(
    tmp_path, child_status
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    if child_status is RunStatus.FAILED:
        coordinator.transition("delegate-child", RunStatus.RUNNING)
        coordinator.transition(
            "delegate-child", RunStatus.FAILED, output={"reason": "review failed"}
        )
        expected_child_output = {"reason": "review failed"}
    else:
        coordinator.cancel("delegate-child")
        expected_child_output = None

    step, run = coordinator.execute_next_step("run-1")

    assert step.status is StepStatus.FAILED
    assert step.output == {
        "child_run_id": "delegate-child",
        "child_status": child_status.value,
        "child_output": expected_child_output,
    }
    assert run.status is RunStatus.FAILED
    assert run.output == {
        "failed_step_id": "delegate",
        "child_run_id": "delegate-child",
        "child_status": child_status.value,
        "child_output": expected_child_output,
    }
    assert [entry.transition for entry in coordinator.list_history("run-1")][-2:] == [
        "step_failed",
        "run_failed",
    ]


def test_delegation_outcome_reconciliation_reports_compare_and_swap_conflict(
    tmp_path, monkeypatch
) -> None:
    database = tmp_path / "state.sqlite3"
    store = StateStore(database)
    coordinator = RunCoordinator(store)
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    coordinator.transition("delegate-child", RunStatus.RUNNING)
    coordinator.transition("delegate-child", RunStatus.SUCCEEDED, output={"ok": True})
    competing = RunCoordinator(StateStore(database))
    real_put_many = store.put_many

    def cancel_before_write(*args, **kwargs):
        competing.cancel("run-1")
        return real_put_many(*args, **kwargs)

    monkeypatch.setattr(store, "put_many", cancel_before_write)

    with pytest.raises(ValueError, match="delegation outcome conflict: delegate"):
        coordinator.execute_next_step("run-1")
    assert competing.get("run-1").status is RunStatus.CANCELLED
    assert competing.get_step("delegate").status is StepStatus.CANCELLED


def test_execute_next_step_respects_delegation_approval_gate(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
        approval_required=True,
    )

    with pytest.raises(ApprovalRequiredError):
        coordinator.execute_next_step("run-1")
    assert coordinator.get("delegate-child") is None

    coordinator.approve_step("delegate")
    step, _ = coordinator.execute_next_step("run-1")
    assert step.delegated_run_id == "delegate-child"


def test_start_next_step_rejects_delegation_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )

    with pytest.raises(ValueError, match="must be dispatched through execute_next_step"):
        coordinator.start_next_step("run-1")
    assert coordinator.get_step("delegate").status is StepStatus.QUEUED


def test_delegation_dispatch_is_compare_and_swap_safe(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    setup = RunCoordinator(StateStore(database))
    setup.create("run-1", objective="Delegate part of the work")
    setup.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinators = tuple(_RunCoordinator(StateStore(database)) for _ in range(4))

    def attempt(coordinator):
        try:
            return coordinator.execute_next_step("run-1")
        except (ValueError, DelegationPendingError):
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(attempt, coordinators))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get("delegate-child") is not None
    assert len(reloaded.list_runs()) == 2
    step_history = [
        entry for entry in reloaded.list_history("run-1") if entry.transition == "step_delegated"
    ]
    assert len(step_history) == 1


def test_delegated_child_run_is_claimable_and_executes_through_existing_paths(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work", agent_id="agent-1")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")

    claimed_child = coordinator.claim_next("agent-2")
    assert claimed_child is not None and claimed_child.run_id == "delegate-child"

    coordinator.add_step(
        "delegate-child", "review", objective="Do the review", command=("true",)
    )
    step, run = coordinator.execute_next_step(
        "delegate-child", _StubExecutor()
    )
    assert step.status is StepStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED
    # Parent linkage survives the child run's own lifecycle rewrites.
    reloaded_child = coordinator.get("delegate-child")
    assert reloaded_child.parent_run_id == "run-1"
    assert reloaded_child.parent_step_id == "delegate"
    # The parent step and run are unaffected by the child completing (out of
    # scope for this slice: terminal-outcome propagation is a later sprint).
    parent_step = coordinator.get_step("delegate")
    assert parent_step.status is StepStatus.RUNNING
    parent_run = coordinator.get("run-1")
    assert parent_run.status is RunStatus.RUNNING


def test_cancel_run_preserves_delegation_fields_on_active_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")

    coordinator.cancel("run-1")

    step = coordinator.get_step("delegate")
    assert step.status is StepStatus.CANCELLED
    assert step.delegation == DelegationSpec(child_objective="Review the change")
    assert step.delegated_run_id == "delegate-child"
    # Cancelling the parent is the one automatic child-cancellation policy
    # this runtime applies: an active delegated child must never be left
    # running unattended with no parent step left to reconcile its outcome.
    child = coordinator.get("delegate-child")
    assert child.status is RunStatus.CANCELLED


def test_cancel_run_cascades_to_active_delegated_child_and_its_own_step(
    tmp_path,
) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    coordinator.add_step(
        "delegate-child", "review", objective="Do the review", command=("true",)
    )
    coordinator.start_next_step("delegate-child")

    run = coordinator.cancel("run-1")

    assert run.status is RunStatus.CANCELLED
    child = coordinator.get("delegate-child")
    assert child.status is RunStatus.CANCELLED
    child_step = coordinator.get_step("review")
    assert child_step.status is StepStatus.CANCELLED
    child_history = [entry.transition for entry in coordinator.list_history("delegate-child")]
    assert child_history[-2:] == ["step_cancelled", "run_cancelled"]


def test_cancel_run_leaves_terminal_delegated_child_untouched(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    coordinator.transition("delegate-child", RunStatus.RUNNING)
    coordinator.transition(
        "delegate-child", RunStatus.SUCCEEDED, output={"summary": "review passed"}
    )

    coordinator.cancel("run-1")

    step = coordinator.get_step("delegate")
    assert step.status is StepStatus.CANCELLED
    child = coordinator.get("delegate-child")
    assert child.status is RunStatus.SUCCEEDED
    assert child.output == {"summary": "review passed"}


def test_cancel_run_rejects_mismatched_delegation_linkage(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    store = StateStore(database)
    child_record = store.get("run", "delegate-child")
    tampered = {**child_record.payload, "parent_step_id": "not-delegate"}
    store.put("run", "delegate-child", status=child_record.status, payload=tampered)

    with pytest.raises(ValueError, match="delegated child run linkage does not match parent"):
        coordinator.cancel("run-1")


def test_recover_running_step_rejects_delegation_step(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")

    with pytest.raises(ValueError, match="cannot recover a delegation step directly"):
        coordinator.recover_running_step("delegate", StepRecoveryReason.INTERRUPTED)

    step = coordinator.get_step("delegate")
    assert step.status is StepStatus.RUNNING
    assert step.delegated_run_id == "delegate-child"
    child = coordinator.get("delegate-child")
    assert child.status is RunStatus.QUEUED


def test_reconcile_delegation_step_rejects_mismatched_child_linkage(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    coordinator = RunCoordinator(StateStore(database))
    coordinator.create("run-1", objective="Delegate part of the work")
    coordinator.add_step(
        "run-1",
        "delegate",
        objective="Delegate the review",
        delegation=DelegationSpec(child_objective="Review the change"),
    )
    coordinator.execute_next_step("run-1")
    coordinator.transition("delegate-child", RunStatus.RUNNING)
    coordinator.transition("delegate-child", RunStatus.SUCCEEDED, output={"ok": True})
    store = StateStore(database)
    child_record = store.get("run", "delegate-child")
    tampered = {**child_record.payload, "parent_run_id": "not-run-1"}
    store.put("run", "delegate-child", status=child_record.status, payload=tampered)

    with pytest.raises(ValueError, match="delegated child run linkage does not match parent"):
        coordinator.execute_next_step("run-1")


class _StubExecutor:
    def execute(self, argv, *, timeout=None):
        from codex_agentic_os.sandboxes import SandboxResult

        return SandboxResult(tuple(argv), 0, "ok", "")
