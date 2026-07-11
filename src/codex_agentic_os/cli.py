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
from .runtime import RunCoordinator, RuntimeSpec
from .sandboxes import default_sandboxes
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
    inspect = run_commands.add_parser("inspect", help="show a run and its ordered steps")
    cancel = run_commands.add_parser("cancel", help="cancel a run and its active steps")
    for command in (inspect, cancel):
        command.add_argument("run_id")
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
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
            if not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.run_command == "inspect"
            coordinator = RunCoordinator(
                StateStore(arguments.state_db, read_only=read_only)
            )
            if arguments.run_command == "cancel":
                coordinator.cancel(arguments.run_id)
            print(json.dumps(_run_payload(coordinator, arguments.run_id), indent=2, sort_keys=True))
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
