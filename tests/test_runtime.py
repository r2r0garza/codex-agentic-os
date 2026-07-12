from concurrent.futures import ThreadPoolExecutor

import pytest

from codex_agentic_os.runtime import (
    AgentRun,
    RunCoordinator,
    RunStatus,
    RunStep,
    StepRecoveryReason,
    StepStatus,
)
from codex_agentic_os.sandboxes import SandboxResult
from codex_agentic_os.state import StateStore


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

    first = coordinator.add_step("run-1", "test", objective="Write tests")
    second = coordinator.add_step("run-1", "code", objective="Implement feature")
    coordinator.transition_step("test", StepStatus.RUNNING)
    completed = coordinator.transition_step(
        "test", StepStatus.SUCCEEDED, output={"tests": 3}
    )

    assert first == RunStep("test", "run-1", 1, "Write tests", StepStatus.QUEUED, 1)
    assert second.position == 2
    assert completed.revision == 3
    assert completed.output == {"tests": 3}
    assert RunCoordinator(StateStore(database)).list_steps("run-1") == (
        completed,
        second,
    )


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
        competing.add_step("run-1", "step-1", objective="Replacement")

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

    coordinator.add_step("run-1", "step-1", objective="Work")
    with pytest.raises(ValueError, match="step already exists"):
        coordinator.add_step("run-1", "step-1", objective="Duplicate")
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
    coordinator.add_step("run-1", "completed", objective="Already done")
    coordinator.add_step("run-1", "active", objective="In progress")
    coordinator.add_step("run-1", "queued", objective="Not started")
    coordinator.add_step("run-1", "failed", objective="Already failed")
    coordinator.add_step("run-1", "cancelled", objective="Already cancelled")
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
        RunStep("active", "run-1", 2, "In progress", StepStatus.CANCELLED, 3),
        RunStep("queued", "run-1", 3, "Not started", StepStatus.CANCELLED, 2),
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
    original_first = coordinator.add_step("run-1", "first", objective="First")
    original_second = coordinator.add_step("run-1", "second", objective="Second")
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
    first = coordinator.add_step("run-1", "first", objective="Already done")
    target = coordinator.add_step(
        "run-1",
        "target",
        objective="Skip this",
        command=("python", "-m", "pytest"),
        timeout=30,
    )
    last = coordinator.add_step("run-1", "last", objective="Still queued")
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

    appended = coordinator.add_step("run-1", "appended", objective="Added later")
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
    coordinator.add_step("run-1", "step-1", objective="Work")
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
    original_step = coordinator.add_step("run-1", "step-1", objective="Work")
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
    coordinator.add_step("run-1", "first", objective="First command")
    coordinator.add_step("run-1", "second", objective="Second command")

    first = coordinator.start_next_step("run-1")

    assert first == RunStep(
        "first", "run-1", 1, "First command", StepStatus.RUNNING, 2
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
    original_step = coordinator.add_step("run-1", "first", objective="First command")
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

    coordinator.add_step("run-1", "first", objective="First command")
    coordinator.add_step("run-1", "second", objective="Second command")
    coordinator.start_next_step("run-1")
    with pytest.raises(ValueError, match="already has a running step"):
        coordinator.start_next_step("run-1")

    coordinator.cancel("run-1")
    with pytest.raises(ValueError, match="terminal run"):
        coordinator.start_next_step("run-1")


def test_sandbox_results_complete_steps_and_run_without_backend_coupling(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step("run-1", "first", objective="First command")
    coordinator.add_step("run-1", "second", objective="Second command")
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
    coordinator.add_step("run-1", "command", objective="Run command")
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
    coordinator.add_step("run-1", "command", objective="Run command")
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
    coordinator.add_step("run-1", "command", objective="Run command")
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


def test_execute_next_step_rejects_non_command_without_mutation(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    queued = coordinator.create("run-1", objective="Coordinate work")
    step = coordinator.add_step("run-1", "manual", objective="Review output")

    with pytest.raises(ValueError, match="does not have a command"):
        coordinator.execute_next_step("run-1", object())

    assert coordinator.get("run-1") == queued
    assert coordinator.get_step("manual") == step


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
    first = coordinator.add_step("remove", "first", objective="First")
    second = coordinator.add_step("remove", "second", objective="Second")
    coordinator.create("keep", objective="Unrelated work")
    kept_step = coordinator.add_step("keep", "kept", objective="Keep")
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
    step = coordinator.add_step("active", "step", objective="Pending")
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
    coordinator.add_step("run-1", "first", objective="First")
    coordinator.add_step("run-1", "second", objective="Second")
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
