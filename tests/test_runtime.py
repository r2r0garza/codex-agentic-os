import pytest

from codex_agentic_os.sandboxes import SandboxResult

from codex_agentic_os.runtime import (
    AgentRun,
    RunCoordinator,
    RunStatus,
    RunStep,
    StepRecoveryReason,
    StepStatus,
)
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


def test_start_next_step_dispatches_in_position_order(tmp_path) -> None:
    coordinator = RunCoordinator(StateStore(tmp_path / "state.sqlite3"))
    coordinator.create("run-1", objective="Build feature")
    coordinator.add_step("run-1", "first", objective="First command")
    coordinator.add_step("run-1", "second", objective="Second command")

    first = coordinator.start_next_step("run-1")

    assert first == RunStep(
        "first", "run-1", 1, "First command", StepStatus.RUNNING, 2
    )
    assert coordinator.get("run-1").status is RunStatus.RUNNING

    coordinator.transition_step("first", StepStatus.SUCCEEDED)
    second = coordinator.start_next_step("run-1")

    assert second.step_id == "second"
    assert second.status is StepStatus.RUNNING


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
    assert succeeded.status is RunStatus.SUCCEEDED
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
    assert step.output == {
        "command": ["docker", "run", "false"],
        "exit_code": 17,
        "stdout": "",
        "stderr": "failed\n",
    }
    assert run.status is RunStatus.FAILED
    assert run.output == {"failed_step_id": "command", "exit_code": 17}


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
