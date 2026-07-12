"""Command-line entrypoint for the OS foundation and repository index."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .chat import ChatMessage, ChatRequest, adapter_for
from .index import (
    build_clean_index,
    build_incremental_index,
    check_index,
    explain_symbol,
    unstaged_index_paths,
)
from .providers import DEFAULT_PROVIDER_SPECS, ProviderKind, ProviderSpec
from .runtime import (
    Agent,
    AgentRegistry,
    RunCoordinator,
    RunStatus,
    RunStep,
    RuntimeSpec,
    StepRecoveryReason,
    StepStatus,
)
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
    claim = run_commands.add_parser("claim", help="claim a queued durable run")
    release = run_commands.add_parser("release", help="release a queued durable run claim")
    claim_next = run_commands.add_parser(
        "claim-next", help="atomically claim the next eligible queued run"
    )
    add_step = run_commands.add_parser(
        "add-step",
        help="append a queued command step to a durable run",
        usage=(
            "%(prog)s [-h] --objective OBJECTIVE [--timeout TIMEOUT] "
            "[--state-db STATE_DB] run_id step_id [command ...]"
        ),
        epilog=(
            "The trailing command (optionally introduced with '--') is parsed "
            "manually rather than as an argparse positional; omit it for a "
            "coordination-only step."
        ),
    )
    list_runs = run_commands.add_parser("list", help="list durable runs")
    inspect = run_commands.add_parser("inspect", help="show a run and its ordered steps")
    transition = run_commands.add_parser(
        "transition", help="advance a run through an explicit lifecycle transition"
    )
    transition_step = run_commands.add_parser(
        "transition-step", help="advance a step through an explicit lifecycle transition"
    )
    inspect_step = run_commands.add_parser("inspect-step", help="show one durable step")
    cancel = run_commands.add_parser("cancel", help="cancel a run and its active steps")
    cancel_step = run_commands.add_parser(
        "cancel-step", help="cancel one queued durable step"
    )
    prune = run_commands.add_parser(
        "prune", help="permanently remove one terminal run and its steps"
    )
    execute_next = run_commands.add_parser(
        "execute-next", help="execute the next queued command step in a container"
    )
    recover = run_commands.add_parser(
        "recover", help="fail an interrupted or timed-out running step"
    )
    create.add_argument("run_id")
    create.add_argument("--objective", required=True, help="objective for the queued run")
    create.add_argument("--agent-id", help="optional agent assigned to the run")
    claim.add_argument("run_id")
    claim.add_argument("--agent-id", required=True, help="agent claiming the queued run")
    release.add_argument("run_id")
    release.add_argument("--agent-id", required=True, help="agent releasing the queued run")
    claim_next.add_argument(
        "--agent-id", required=True, help="agent claiming the next eligible run"
    )
    add_step.add_argument("run_id")
    add_step.add_argument("step_id")
    add_step.add_argument("--objective", required=True, help="objective for the queued step")
    add_step.add_argument("--timeout", type=float, help="positive command timeout in seconds")
    list_runs.add_argument(
        "--status",
        action="append",
        choices=[status.value for status in RunStatus],
        help="include runs with this lifecycle status; repeat to include multiple statuses",
    )
    transition.add_argument("run_id")
    transition.add_argument("status", choices=[status.value for status in RunStatus])
    transition.add_argument(
        "--output", help="JSON object persisted for a succeeded or failed run"
    )
    transition.add_argument(
        "--state-db",
        type=Path,
        default=Path(".codex-agentic-os/state.sqlite3"),
        help="path to the runtime state database",
    )
    transition_step.add_argument("step_id")
    transition_step.add_argument(
        "status", choices=[status.value for status in StepStatus]
    )
    transition_step.add_argument(
        "--output", help="JSON object persisted for a succeeded or failed step"
    )
    transition_step.add_argument(
        "--state-db",
        type=Path,
        default=Path(".codex-agentic-os/state.sqlite3"),
        help="path to the runtime state database",
    )
    list_runs.add_argument(
        "--agent-id",
        help="include runs assigned to this exact agent identifier",
    )
    list_runs.add_argument(
        "--unassigned",
        action="store_true",
        help="include only runs without an assigned agent",
    )
    for command in (create, claim, release, claim_next, add_step, list_runs):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
    for command in (
        inspect,
        inspect_step,
        cancel,
        cancel_step,
        prune,
        execute_next,
        recover,
    ):
        identifier = "step_id" if command in (inspect_step, cancel_step, recover) else "run_id"
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
    execute_next.add_argument(
        "--mount",
        action="append",
        default=[],
        metavar="HOST:CONTAINER",
        help="bind mount a host path in the container; repeat for multiple mounts",
    )
    execute_next.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="pass an environment variable into the container; repeat for multiple",
    )

    agent = commands.add_parser("agent", help="manage durable agent identities")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_register = agent_commands.add_parser(
        "register", help="register a durable agent identity"
    )
    agent_register.add_argument("agent_id")
    agent_register.add_argument("--label", help="optional human-readable label")
    agent_list = agent_commands.add_parser(
        "list", help="list registered agent identities"
    )
    agent_heartbeat = agent_commands.add_parser(
        "heartbeat", help="refresh a registered agent's liveness timestamp"
    )
    agent_heartbeat.add_argument("agent_id")
    for command in (agent_register, agent_list, agent_heartbeat):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )

    provider = commands.add_parser("provider", help="inspect configured model providers")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    provider_commands.add_parser("list", help="list default provider specs")

    chat = commands.add_parser("chat", help="send ad hoc requests through provider adapters")
    chat_commands = chat.add_subparsers(dest="chat_command", required=True)
    chat_send = chat_commands.add_parser(
        "send", help="send a single message through a configured provider adapter"
    )
    chat_send.add_argument("message")
    chat_send.add_argument(
        "--provider", required=True, choices=[kind.value for kind in ProviderKind]
    )
    chat_send.add_argument("--model", help="override the provider's default model")
    chat_send.add_argument("--base-url", help="override the provider's default base URL")
    chat_send.add_argument(
        "--api-key-env",
        help="override the provider's default credential environment variable",
    )
    chat_send.add_argument("--temperature", type=float, help="sampling temperature")
    chat_send.add_argument("--max-tokens", type=int, help="maximum response tokens")
    chat_send.add_argument(
        "--system", help="optional system instruction sent ahead of the message"
    )
    return parser


def _parse_mounts(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Parse strict HOST:CONTAINER bind mount arguments."""

    mounts = []
    for value in values:
        parts = value.split(":")
        if len(parts) != 2 or not all(parts):
            raise ValueError("mount must be HOST:CONTAINER with non-empty paths")
        mounts.append((parts[0], parts[1]))
    return tuple(mounts)


def _parse_env(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Parse strict KEY=VALUE environment variable arguments."""

    pairs = []
    for value in values:
        key, sep, val = value.partition("=")
        if not sep or not key or not val:
            raise ValueError("env var must be KEY=VALUE with non-empty key and value")
        pairs.append((key, val))
    return tuple(pairs)


def _run_payload(coordinator: RunCoordinator, run_id: str) -> dict[str, object]:
    """Return a JSON-compatible, ordered view of one durable run."""

    run = coordinator.get(run_id)
    if run is None:
        raise ValueError(f"run does not exist: {run_id}")
    run_data = asdict(run)
    run_data["status"] = run.status.value
    steps = []
    for step in coordinator.list_steps(run_id):
        steps.append(_step_payload(step))
    return {"run": run_data, "steps": steps}


def _step_payload(step: RunStep) -> dict[str, object]:
    """Return the standard JSON-compatible view of one durable step."""

    payload = asdict(step)
    payload["status"] = step.status.value
    return payload


def _run_list_payload(
    coordinator: RunCoordinator,
    statuses: Sequence[RunStatus] | None = None,
    agent_id: str | None = None,
    unassigned: bool = False,
) -> list[dict[str, object]]:
    """Return JSON-compatible run summaries in stable identifier order."""

    included_statuses = None if statuses is None else set(statuses)
    summaries = []
    for run in coordinator.list_runs():
        if included_statuses is not None and run.status not in included_statuses:
            continue
        if agent_id is not None and run.agent_id != agent_id:
            continue
        if unassigned and run.agent_id is not None:
            continue
        summary = asdict(run)
        summary["status"] = run.status.value
        summaries.append(summary)
    return summaries


def _agent_payload(agent: Agent) -> dict[str, object]:
    """Return the standard JSON-compatible view of one registered agent."""

    return asdict(agent)


def _chat_provider_spec(
    provider: str, model: str | None, base_url: str | None, api_key_env: str | None
) -> ProviderSpec:
    """Build a provider spec from CLI flags, defaulting unset fields per provider kind."""

    kind = ProviderKind(provider)
    default_spec = next(spec for spec in DEFAULT_PROVIDER_SPECS if spec.kind is kind)
    return ProviderSpec(
        kind=kind,
        model=model or default_spec.model,
        base_url=base_url or default_spec.base_url,
        api_key_env=api_key_env or default_spec.api_key_env,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run a CLI command, defaulting to the foundation capability summary."""

    parser = _parser()
    arguments, extras = parser.parse_known_args(argv)
    if arguments.command == "run" and arguments.run_command == "add-step":
        if extras and extras[0] == "--":
            extras = extras[1:]
        arguments.step_command = extras
    elif extras:
        parser.error(f"unrecognized arguments: {' '.join(extras)}")
    if arguments.command is None:
        print(json.dumps(_foundation_payload(), indent=2, sort_keys=True))
        return

    repository = Path.cwd()
    try:
        if arguments.command == "run":
            if arguments.run_command != "create" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.run_command in {"inspect", "inspect-step", "list"}
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
            elif arguments.run_command == "claim":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                coordinator.claim(arguments.run_id, arguments.agent_id)
                run_id = arguments.run_id
            elif arguments.run_command == "release":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                coordinator.release_claim(arguments.run_id, arguments.agent_id)
                run_id = arguments.run_id
            elif arguments.run_command == "claim-next":
                claimed = coordinator.claim_next(arguments.agent_id)
                if claimed is None:
                    print(
                        json.dumps(
                            {"claim": {"attempted": False}}, indent=2, sort_keys=True
                        )
                    )
                    return
                print(
                    json.dumps(
                        _run_payload(coordinator, claimed.run_id), indent=2, sort_keys=True
                    )
                )
                return
            elif arguments.run_command == "add-step":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                coordinator.add_step(
                    arguments.run_id,
                    arguments.step_id,
                    objective=arguments.objective,
                    command=arguments.step_command or None,
                    timeout=arguments.timeout,
                )
                run_id = arguments.run_id
            elif arguments.run_command == "list":
                if arguments.agent_id is not None and not arguments.agent_id.strip():
                    raise ValueError("agent id must not be empty")
                if arguments.unassigned and arguments.agent_id is not None:
                    raise ValueError("--unassigned cannot be combined with --agent-id")
                statuses = (
                    None
                    if arguments.status is None
                    else [RunStatus(status) for status in arguments.status]
                )
                print(
                    json.dumps(
                        _run_list_payload(
                            coordinator,
                            statuses,
                            arguments.agent_id,
                            arguments.unassigned,
                        ),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            elif arguments.run_command == "inspect-step":
                step = coordinator.get_step(arguments.step_id)
                if step is None:
                    raise ValueError(f"step does not exist: {arguments.step_id}")
                print(json.dumps(_step_payload(step), indent=2, sort_keys=True))
                return
            elif arguments.run_command == "transition":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                output = None
                if arguments.output is not None:
                    try:
                        output = json.loads(arguments.output)
                    except json.JSONDecodeError as error:
                        raise ValueError("run output must be valid JSON") from error
                    if not isinstance(output, dict):
                        raise ValueError("run output must be a JSON object")
                coordinator.transition(
                    arguments.run_id,
                    RunStatus(arguments.status),
                    output=output,
                )
                run_id = arguments.run_id
            elif arguments.run_command == "transition-step":
                if coordinator.get_step(arguments.step_id) is None:
                    raise ValueError(f"step does not exist: {arguments.step_id}")
                output = None
                if arguments.output is not None:
                    try:
                        output = json.loads(arguments.output)
                    except json.JSONDecodeError as error:
                        raise ValueError("step output must be valid JSON") from error
                    if not isinstance(output, dict):
                        raise ValueError("step output must be a JSON object")
                step = coordinator.transition_step(
                    arguments.step_id,
                    StepStatus(arguments.status),
                    output=output,
                )
                print(json.dumps(_step_payload(step), indent=2, sort_keys=True))
                return
            elif arguments.run_command == "prune":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                pruned_run, pruned_steps = coordinator.prune(arguments.run_id)
                print(
                    json.dumps(
                        {
                            "pruned": {
                                "run_id": pruned_run.run_id,
                                "step_count": len(pruned_steps),
                            }
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            if arguments.run_command == "cancel":
                coordinator.cancel(arguments.run_id)
                run_id = arguments.run_id
            elif arguments.run_command == "cancel-step":
                current = coordinator.get_step(arguments.step_id)
                if current is None:
                    raise ValueError(f"step does not exist: {arguments.step_id}")
                if coordinator.get(current.run_id) is None:
                    raise ValueError(f"run does not exist: {current.run_id}")
                step = coordinator.cancel_step(arguments.step_id)
                run_id = step.run_id
            elif arguments.run_command == "execute-next":
                kind = SandboxKind(arguments.sandbox)
                if arguments.image is not None and not arguments.image.strip():
                    raise ValueError("sandbox image must not be empty")
                mounts = _parse_mounts(arguments.mount)
                env = _parse_env(arguments.env)
                spec = (
                    SandboxSpec(kind=kind, image=arguments.image, mounts=mounts, env=env)
                    if arguments.image is not None
                    else SandboxSpec(kind=kind, mounts=mounts, env=env)
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
        elif arguments.command == "agent":
            if arguments.agent_command != "register" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.agent_command == "list"
            registry = AgentRegistry(StateStore(arguments.state_db, read_only=read_only))
            if arguments.agent_command == "register":
                registered = registry.register(arguments.agent_id, label=arguments.label)
                print(json.dumps(_agent_payload(registered), indent=2, sort_keys=True))
            elif arguments.agent_command == "heartbeat":
                agent = registry.heartbeat(arguments.agent_id)
                print(json.dumps(_agent_payload(agent), indent=2, sort_keys=True))
            else:
                print(
                    json.dumps(
                        [_agent_payload(agent) for agent in registry.list_agents()],
                        indent=2,
                        sort_keys=True,
                    )
                )
        elif arguments.command == "provider":
            print(
                json.dumps(
                    [spec.to_dict() for spec in DEFAULT_PROVIDER_SPECS],
                    indent=2,
                    sort_keys=True,
                )
            )
        elif arguments.command == "chat":
            if not arguments.message.strip():
                raise ValueError("chat message must not be empty")
            if arguments.system is not None and not arguments.system.strip():
                raise ValueError("chat system instruction must not be empty")
            spec = _chat_provider_spec(
                arguments.provider, arguments.model, arguments.base_url, arguments.api_key_env
            )
            messages = (
                (ChatMessage("system", arguments.system), ChatMessage("user", arguments.message))
                if arguments.system
                else (ChatMessage("user", arguments.message),)
            )
            response = adapter_for(spec).complete(
                ChatRequest(
                    messages,
                    temperature=arguments.temperature,
                    max_tokens=arguments.max_tokens,
                )
            )
            payload: dict[str, object] = {"content": response.content, "model": response.model}
            if response.raw is not None:
                payload["raw"] = response.raw
            print(json.dumps(payload, indent=2, sort_keys=True))
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
    except (ValueError, RuntimeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
