from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from codex_agentic_os.chat import ChatResponse
from codex_agentic_os.runtime import (
    Agent,
    AgentRegistry,
    AgentRun,
    ProviderMessage,
    RunCoordinator as _RunCoordinator,
    RunStatus,
    RunStep,
    StepRecoveryReason,
    StepStatus,
)
from codex_agentic_os.sandboxes import SandboxResult
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
            return ChatResponse("Durable answer", model="served-model", raw={"id": "r1"})

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
    }
    assert run.status is RunStatus.SUCCEEDED
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
    assert [entry.transition for entry in history] == ["created", "transitioned"]
    assert history[-1].status == "succeeded"
    assert history[-1].execution_kind == "provider_message"


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

    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
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
    assert step.revision == 3
    assert run.revision == 3
    reloaded = RunCoordinator(StateStore(database))
    assert reloaded.get_step("command") == step
    assert reloaded.get("run-1") == run


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
