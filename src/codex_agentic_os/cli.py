"""Command-line entrypoint for the OS foundation and repository index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .index import build_clean_index, build_incremental_index, check_index, explain_symbol
from .providers import DEFAULT_PROVIDER_SPECS
from .runtime import RuntimeSpec
from .sandboxes import default_sandboxes


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
    explain = index_commands.add_parser("explain", help="describe one indexed symbol")
    explain.add_argument("qualified_name")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run a CLI command, defaulting to the foundation capability summary."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        print(json.dumps(_foundation_payload(), indent=2, sort_keys=True))
        return

    repository = Path.cwd()
    try:
        if arguments.index_command == "build":
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
        else:
            print(json.dumps(explain_symbol(repository, arguments.qualified_name), indent=2, sort_keys=True))
    except ValueError as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
