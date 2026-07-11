import pytest

from codex_agentic_os.runtime import AgentRun, RunCoordinator, RunStatus
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
