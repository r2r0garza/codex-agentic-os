"""Command-line entrypoint for the OS foundation and repository index."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .index import (
    build_clean_index,
    build_incremental_index,
    check_index,
    explain_symbol,
    unstaged_index_paths,
)
from .providers import DEFAULT_PROVIDER_SPECS
from .runtime import RunCoordinator, RunStatus, RuntimeSpec, StepRecoveryReason
from .sandboxes import ContainerSandbox, SandboxKind, SandboxSpec, default_sandboxes
from .state import StateStore


def _foundation_payload() -> dict[str, object]:
    """Return the currently planned foundation capabilities."""

    return {
        "runtime": RuntimeSpec().to_dict(),
        "providers": [spec.to_dict() for spec in DEFAULT_PROVIDER_SPECS],
        "sandboxes": [spec.to_dict() for spec in default_sandboxes()],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-agentic-os")
    commands = parser.add_subparsers(dest="command")
    index = commands.add_parser("index", help="build and inspect the repository index")
    index_commands = index.add_subparsers(dest="index_command", required=True)

    build = index_commands.add_parser("build", help="build deterministic index artifacts")
    build.add_argument("--incremental", action="store_true", help="reuse unchanged records")
    index_commands.add_parser("check", help="verify artifacts against a clean rebuild")
    index_commands.add_parser("pre-commit", help="refresh and verify staged index artifacts")
    explain = index_commands.add_parser("explain", help="describe one indexed symbol")
    explain.add_argument("qualified_name")

    run = commands.add_parser("run", help="inspect and control durable runs")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    create = run_commands.add_parser("create", help="create a queued durable run")
    add_step = run_commands.add_parser(
        "add-step", help="append a queued command step to a durable run"
    )
    list_runs = run_commands.add_parser("list", help="list durable runs")
    inspect = run_commands.add_parser("inspect", help="show a run and its ordered steps")
    cancel = run_commands.add_parser("cancel", help="cancel a run and its active steps")
    execute_next = run_commands.add_parser(
        "execute-next", help="execute the next queued command step in a container"
    )
    recover = run_commands.add_parser(
        "recover", help="fail an interrupted or timed-out running step"
    )
    create.add_argument("run_id")
    create.add_argument("--objective", required=True, help="objective for the queued run")
    create.add_argument("--agent-id", help="optional agent assigned to the run")
    add_step.add_argument("run_id")
    add_step.add_argument("step_id")
    add_step.add_argument("--objective", required=True, help="objective for the queued step")
    add_step.add_argument("--timeout", type=float, help="positive command timeout in seconds")
    add_step.add_argument("step_command", nargs="+", help="command and arguments to execute")
    list_runs.add_argument(
        "--status",
        action="append",
        choices=[status.value for status in RunStatus],
        help="include runs with this lifecycle status; repeat to include multiple statuses",
    )
    list_runs.add_argument(
        "--agent-id",
        help="include runs assigned to this exact agent identifier",
    )
    for command in (create, add_step, list_runs):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
    for command in (inspect, cancel, execute_next, recover):
        identifier = "step_id" if command is recover else "run_id"
        command.add_argument(identifier)
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
    recover.add_argument(
        "reason", choices=[reason.value for reason in StepRecoveryReason]
    )
    recover.add_argument("--detail", help="operator context for the recovery")
    execute_next.add_argument(
        "--sandbox", required=True, choices=[kind.value for kind in SandboxKind]
    )
    execute_next.add_argument("--image", help="container image override")
    return parser


def _run_payload(coordinator: RunCoordinator, run_id: str) -> dict[str, object]:
    """Return a JSON-compatible, ordered view of one durable run."""

    run = coordinator.get(run_id)
    if run is None:
        raise ValueError(f"run does not exist: {run_id}")
    run_data = asdict(run)
    run_data["status"] = run.status.value
    steps = []
    for step in coordinator.list_steps(run_id):
        step_data = asdict(step)
        step_data["status"] = step.status.value
        steps.append(step_data)
    return {"run": run_data, "steps": steps}


def _run_list_payload(
    coordinator: RunCoordinator,
    statuses: Sequence[RunStatus] | None = None,
    agent_id: str | None = None,
) -> list[dict[str, object]]:
    """Return JSON-compatible run summaries in stable identifier order."""

    included_statuses = None if statuses is None else set(statuses)
    summaries = []
    for run in coordinator.list_runs():
        if included_statuses is not None and run.status not in included_statuses:
            continue
        if agent_id is not None and run.agent_id != agent_id:
            continue
        summary = asdict(run)
        summary["status"] = run.status.value
        summaries.append(summary)
    return summaries


def main(argv: Sequence[str] | None = None) -> None:
    """Run a CLI command, defaulting to the foundation capability summary."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        print(json.dumps(_foundation_payload(), indent=2, sort_keys=True))
        return

    repository = Path.cwd()
    try:
        if arguments.command == "run":
            if arguments.run_command != "create" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.run_command in {"inspect", "list"}
            coordinator = RunCoordinator(
                StateStore(arguments.state_db, read_only=read_only)
            )
            if arguments.run_command == "create":
                coordinator.create(
                    arguments.run_id,
                    objective=arguments.objective,
                    agent_id=arguments.agent_id,
                )
                run_id = arguments.run_id
            elif arguments.run_command == "add-step":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                coordinator.add_step(
                    arguments.run_id,
                    arguments.step_id,
                    objective=arguments.objective,
                    command=arguments.step_command,
                    timeout=arguments.timeout,
                )
                run_id = arguments.run_id
            elif arguments.run_command == "list":
                if arguments.agent_id is not None and not arguments.agent_id.strip():
                    raise ValueError("agent id must not be empty")
                statuses = (
                    None
                    if arguments.status is None
                    else [RunStatus(status) for status in arguments.status]
                )
                print(
                    json.dumps(
                        _run_list_payload(coordinator, statuses, arguments.agent_id),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            if arguments.run_command == "cancel":
                coordinator.cancel(arguments.run_id)
                run_id = arguments.run_id
            elif arguments.run_command == "execute-next":
                kind = SandboxKind(arguments.sandbox)
                if arguments.image is not None and not arguments.image.strip():
                    raise ValueError("sandbox image must not be empty")
                spec = (
                    SandboxSpec(kind=kind, image=arguments.image)
                    if arguments.image is not None
                    else SandboxSpec(kind=kind)
                )
                result = coordinator.execute_next_step(
                    arguments.run_id,
                    ContainerSandbox(spec),
                )
                run_id = arguments.run_id
                if result is None:
                    payload = _run_payload(coordinator, run_id)
                    payload["execution"] = {"attempted": False}
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return
            elif arguments.run_command == "recover":
                step = coordinator.get_step(arguments.step_id)
                if step is None:
                    raise ValueError(f"step does not exist: {arguments.step_id}")
                coordinator.recover_running_step(
                    arguments.step_id,
                    StepRecoveryReason(arguments.reason),
                    detail=arguments.detail,
                )
                run_id = step.run_id
            else:
                run_id = arguments.run_id
            print(json.dumps(_run_payload(coordinator, run_id), indent=2, sort_keys=True))
        elif arguments.index_command == "build":
            builder = build_incremental_index if arguments.incremental else build_clean_index
            manifest = builder(repository)
            counts = manifest["artifact_counts"]
            mode = "incremental" if arguments.incremental else "clean"
            print(
                f"Built {mode} index: {counts['tracked_files']} files, "
                f"{counts['symbols']} symbols, {counts['dependencies']} relationships."
            )
        elif arguments.index_command == "check":
            differences = check_index(repository)
            if differences:
                parser.exit(1, f"Index is stale: {', '.join(differences)}\n")
            print("Index is current.")
        elif arguments.index_command == "pre-commit":
            build_incremental_index(repository)
            unstaged = unstaged_index_paths(repository)
            if unstaged:
                parser.exit(
                    1,
                    "Repository index was refreshed; stage these files and retry: "
                    f"{', '.join(unstaged)}\n",
                )
            print("Repository index is staged and current.")
        else:
            print(json.dumps(explain_symbol(repository, arguments.qualified_name), indent=2, sort_keys=True))
    except ValueError as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
