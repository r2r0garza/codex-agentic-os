"""Command-line entrypoint for the OS foundation and repository index."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Sequence

from .api import build_server, is_loopback_bind_host, serve_until_stopped
from .chat import ChatMessage, ChatRequest, adapter_for
from .index import (
    build_clean_index,
    build_incremental_index,
    check_index,
    explain_symbol,
    unstaged_index_paths,
)
from .providers import (
    DEFAULT_PROVIDER_ROUTING_POLICY,
    DEFAULT_PROVIDER_SPECS,
    ProviderKind,
    ProviderRoutingPolicy,
    ProviderSpec,
)
from .runtime import (
    Agent,
    AgentRegistry,
    ApprovalStatus,
    ArtifactDeclaration,
    ArtifactRecord,
    ClaimStaleness,
    DelegationPendingError,
    DelegationSpec,
    ExecutionPolicyRegistry,
    ExecutionPolicyRule,
    MEMORY_ENTRY_KINDS,
    MemoryEntry,
    MemoryRegistry,
    PlanDraft,
    PlanStepProposal,
    POLICY_CRITERION_KINDS,
    ProviderMessage,
    RunCoordinator,
    RunHistoryEntry,
    RunStatus,
    RunStep,
    RuntimeSpec,
    SandboxPolicy,
    StepRecoveryReason,
    StepStatus,
    ToolDeclaration,
)
from .payloads import (
    _approval_payload,
    _artifact_record_payload,
    _history_payload,
    _run_list_payload,
    _run_payload,
    _step_payload,
    _usage_payload,
)
from .sandboxes import ContainerSandbox, SandboxKind, SandboxSpec, default_sandboxes
from .state import StateStore
from .worker import run_worker


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
        help="append a queued command or provider-message step to a durable run",
        usage=(
            "%(prog)s [-h] --objective OBJECTIVE [--timeout TIMEOUT] "
            "[--state-db STATE_DB] run_id step_id [command ...]"
        ),
        epilog=(
            "The trailing command (optionally introduced with '--') is parsed "
            "manually rather than as an argparse positional; omit it for a "
            "provider-message step."
        ),
    )
    plan = run_commands.add_parser(
        "plan",
        help=(
            "dispatch a run's objective through a provider adapter and persist "
            "a durable plan draft, queuing no steps"
        ),
    )
    inspect_plan = run_commands.add_parser(
        "inspect-plan", help="show one durable plan draft, read-only"
    )
    accept_plan = run_commands.add_parser(
        "accept-plan", help="atomically accept one plan draft and queue all proposed steps"
    )
    reject_plan = run_commands.add_parser(
        "reject-plan", help="atomically reject one plan draft without queuing steps"
    )
    list_runs = run_commands.add_parser("list", help="list durable runs")
    inspect = run_commands.add_parser("inspect", help="show a run and its ordered steps")
    history = run_commands.add_parser(
        "history", help="show one run's durable lifecycle history in order"
    )
    watch = run_commands.add_parser(
        "watch",
        help="poll one run's durable history live until it reaches a terminal status",
    )
    approvals = run_commands.add_parser(
        "approvals", help="show one run's sanitized step approval requests"
    )
    staleness = run_commands.add_parser(
        "staleness",
        help="report whether a claimed run's owning agent is stale relative to a threshold",
    )
    usage_command = run_commands.add_parser(
        "usage",
        help="show one run's provider usage evidence and a token aggregate",
    )
    list_artifacts_command = run_commands.add_parser(
        "list-artifacts",
        help="list one run's durable artifact records in stable order",
    )
    export_artifact_command = run_commands.add_parser(
        "export-artifact",
        help="export one captured run artifact's content to an operator-chosen path",
    )
    reassign_claim = run_commands.add_parser(
        "reassign-claim",
        help="atomically transfer a demonstrably stale run claim to a replacement agent",
    )
    retry_step = run_commands.add_parser(
        "retry-step",
        help="atomically create a new attempt for one retry-eligible failed step",
    )
    approve = run_commands.add_parser("approve", help="approve one pending step")
    reject = run_commands.add_parser("reject", help="reject one pending step")
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
        "execute-next", help="execute the next queued command or provider-message step"
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
    add_step.add_argument("--provider", help="provider name for a model step")
    add_step.add_argument(
        "--capability",
        help=(
            "required capability for a capability-routed model step; "
            "mutually exclusive with --provider"
        ),
    )
    add_step.add_argument("--message", help="user content for a model step")
    add_step.add_argument("--model", help="optional provider model override")
    add_step.add_argument("--system", help="optional system instruction")
    add_step.add_argument("--temperature", type=float, help="optional non-negative sampling temperature")
    add_step.add_argument("--max-tokens", type=int, help="optional positive response token limit")
    add_step.add_argument(
        "--context-step",
        action="append",
        default=[],
        metavar="STEP_ID",
        help=(
            "include an earlier same-run step as provider context; repeat to preserve order"
        ),
    )
    add_step.add_argument(
        "--approval-required",
        action="store_true",
        help="require an explicit operator decision before dispatch",
    )
    add_step.add_argument(
        "--sandbox", choices=[kind.value for kind in SandboxKind],
        help="persist a sandbox kind for a command step",
    )
    add_step.add_argument("--image", help="persisted container image override")
    add_step.add_argument(
        "--mount",
        action="append",
        default=[],
        metavar="HOST:CONTAINER",
        help="persist a bind mount for the command step; repeat for multiple mounts",
    )
    add_step.add_argument(
        "--env-passthrough",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "persist an environment variable name to resolve from the worker's "
            "environment at dispatch time; repeat for multiple names"
        ),
    )
    add_step.add_argument(
        "--workdir",
        help="persisted absolute working directory inside the container",
    )
    add_step.add_argument(
        "--network",
        action="store_true",
        help="persist explicit opt-in to enable container network access",
    )
    add_step.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help=(
            "declare a named workspace artifact path to capture after a "
            "successful command; the path must resolve within a persisted "
            "sandbox mount; repeat for multiple artifacts"
        ),
    )
    add_step.add_argument(
        "--response-artifact",
        metavar="NAME",
        help="capture a successful provider step's normalized response as a named artifact",
    )
    add_step.add_argument(
        "--tool",
        action="append",
        default=[],
        metavar="JSON",
        help=(
            'declare a named tool for a provider-message step as a JSON object '
            '{"name": ..., "command": [...], "description": ..., "parameters": ...}; '
            "requires a persisted sandbox policy; repeat for multiple tools"
        ),
    )
    add_step.add_argument(
        "--tool-iteration-budget",
        type=int,
        metavar="COUNT",
        help=(
            "required positive maximum tool-iteration count for a step that "
            "declares at least one --tool; rejected for steps without tools"
        ),
    )
    add_step.add_argument(
        "--delegate-objective",
        metavar="OBJECTIVE",
        help=(
            "declare a child-run delegation step with this objective for the "
            "spawned child run; mutually exclusive with a command or provider message"
        ),
    )
    add_step.add_argument(
        "--delegate-target-agent",
        metavar="AGENT_ID",
        help="optional registered agent pre-assigned to the delegation step's spawned child run",
    )
    plan.add_argument("run_id")
    plan.add_argument("plan_id")
    plan.add_argument(
        "--provider", required=True, choices=[kind.value for kind in ProviderKind]
    )
    plan.add_argument("--model", help="optional provider model override")
    plan.add_argument(
        "--objective",
        help="override objective sent for planning (defaults to the run's own objective)",
    )
    plan.add_argument(
        "--temperature", type=float, help="optional non-negative sampling temperature"
    )
    plan.add_argument(
        "--max-tokens", type=int, help="optional positive response token limit"
    )
    inspect_plan.add_argument("plan_id")
    inspect_plan.add_argument(
        "--state-db",
        type=Path,
        default=Path(".codex-agentic-os/state.sqlite3"),
        help="path to the runtime state database",
    )
    for command in (accept_plan, reject_plan):
        command.add_argument("plan_id")
        command.add_argument(
            "--expected-revision",
            type=int,
            required=True,
            help="the draft's current revision, as read from prior inspection",
        )
        command.add_argument(
            "--agent-id", help="registered agent recording the operator decision"
        )
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
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
    reassign_claim.add_argument("run_id")
    reassign_claim.add_argument("replacement_agent_id")
    reassign_claim.add_argument(
        "--expected-agent-id",
        required=True,
        help="the run's current owning agent, as read from prior inspection",
    )
    reassign_claim.add_argument(
        "--expected-revision",
        type=int,
        required=True,
        help="the run's current revision, as read from prior inspection",
    )
    reassign_claim.add_argument(
        "--threshold-seconds",
        type=float,
        required=True,
        help="positive staleness threshold in seconds compared against the current owner's heartbeat",
    )
    retry_step.add_argument("step_id")
    retry_step.add_argument("new_step_id")
    retry_step.add_argument(
        "--expected-step-revision",
        type=int,
        required=True,
        help="the failed step's current revision, as read from prior inspection",
    )
    retry_step.add_argument(
        "--expected-run-revision",
        type=int,
        required=True,
        help="the failed run's current revision, as read from prior inspection",
    )
    for command in (
        create,
        claim,
        release,
        claim_next,
        add_step,
        plan,
        list_runs,
        reassign_claim,
        retry_step,
    ):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
    for command in (
        inspect,
        history,
        watch,
        approvals,
        staleness,
        usage_command,
        list_artifacts_command,
        export_artifact_command,
        inspect_step,
        approve,
        reject,
        cancel,
        cancel_step,
        prune,
        execute_next,
        recover,
    ):
        identifier = (
            "step_id"
            if command in (inspect_step, approve, reject, cancel_step, recover)
            else "run_id"
        )
        command.add_argument(identifier)
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )
    staleness.add_argument(
        "--threshold-seconds",
        type=float,
        required=True,
        help="positive staleness threshold in seconds compared against the owner's heartbeat",
    )
    watch.add_argument(
        "--interval",
        type=float,
        required=True,
        help="positive polling interval in seconds between history checks",
    )
    watch.add_argument(
        "--after-sequence",
        type=int,
        default=0,
        help="emit only durable history entries after this non-negative sequence",
    )
    list_artifacts_command.add_argument(
        "--step", dest="step_id", help="only list artifacts declared or captured by one step"
    )
    export_artifact_command.add_argument(
        "--name", required=True, help="declared or response artifact name to export"
    )
    export_artifact_command.add_argument(
        "--step",
        dest="step_id",
        help="disambiguate an artifact name declared by more than one step",
    )
    export_artifact_command.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="operator-chosen path to write the artifact's stored content",
    )
    recover.add_argument(
        "reason", choices=[reason.value for reason in StepRecoveryReason]
    )
    approve.add_argument("--agent-id", help="registered agent recording the decision")
    reject.add_argument("--agent-id", help="registered agent recording the decision")
    recover.add_argument("--detail", help="operator context for the recovery")
    execute_next.add_argument(
        "--sandbox", choices=[kind.value for kind in SandboxKind]
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
    execute_next.add_argument(
        "--workdir",
        help="absolute working directory inside the container",
    )
    execute_next.add_argument(
        "--network",
        action="store_true",
        help="explicit opt-in to enable container network access (default: isolated, no network)",
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
    agent_inspect = agent_commands.add_parser(
        "inspect", help="show one registered agent identity"
    )
    agent_inspect.add_argument("agent_id")
    for command in (agent_register, agent_list, agent_heartbeat, agent_inspect):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )

    policy = commands.add_parser(
        "policy", help="persist and inspect declarative execution policy rules"
    )
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    policy_create = policy_commands.add_parser(
        "create", help="persist a new finite-criterion execution policy rule"
    )
    policy_create.add_argument("rule_id")
    policy_create.add_argument(
        "--criterion-kind",
        required=True,
        choices=sorted(POLICY_CRITERION_KINDS),
        help="finite criterion kind this rule matches on",
    )
    policy_create.add_argument(
        "--criterion-value",
        required=True,
        help="value the criterion kind must match",
    )
    policy_create.add_argument(
        "--reason",
        required=True,
        help="operator-readable reason for this rule",
    )
    policy_create.add_argument(
        "--precedence",
        type=int,
        required=True,
        help="non-negative integer precedence used to order rules",
    )
    policy_create.add_argument(
        "--disabled",
        action="store_true",
        help="persist the rule in a disabled state (default: enabled)",
    )
    policy_list = policy_commands.add_parser(
        "list", help="list persisted execution policy rules"
    )
    policy_inspect = policy_commands.add_parser(
        "inspect", help="show one persisted execution policy rule"
    )
    policy_inspect.add_argument("rule_id")
    for command in (policy_create, policy_list, policy_inspect):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )

    memory = commands.add_parser(
        "memory", help="persist and inspect durable named memory entries"
    )
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    memory_create = memory_commands.add_parser(
        "create", help="persist a new named memory entry"
    )
    memory_create.add_argument("name")
    memory_create.add_argument("--body", required=True, help="memory entry body")
    memory_create.add_argument(
        "--kind", required=True, choices=sorted(MEMORY_ENTRY_KINDS)
    )
    memory_create.add_argument("--agent-id", help="creating agent provenance")
    memory_create.add_argument("--run-id", help="creating run provenance")
    memory_create.add_argument("--step-id", help="creating step provenance")
    memory_list = memory_commands.add_parser(
        "list", help="list named memory entries in stable order"
    )
    memory_inspect = memory_commands.add_parser(
        "inspect", help="show one named memory entry"
    )
    memory_inspect.add_argument("name")
    for command in (memory_create, memory_list, memory_inspect):
        command.add_argument(
            "--state-db",
            type=Path,
            default=Path(".codex-agentic-os/state.sqlite3"),
            help="path to the runtime state database",
        )

    worker = commands.add_parser("worker", help="run a foreground autonomous worker")
    worker_commands = worker.add_subparsers(dest="worker_command", required=True)
    worker_run = worker_commands.add_parser(
        "run",
        help=(
            "register or resume a durable agent identity and repeatedly claim "
            "and execute run steps until stopped"
        ),
    )
    worker_run.add_argument(
        "--agent-id", required=True, help="durable agent identity to register or resume"
    )
    worker_run.add_argument(
        "--heartbeat-interval",
        type=float,
        required=True,
        help="positive seconds between agent heartbeat refreshes",
    )
    worker_run.add_argument(
        "--poll-interval",
        type=float,
        required=True,
        help="positive seconds to wait when no eligible work is available",
    )
    worker_run.add_argument(
        "--label", help="optional human-readable label for a newly registered agent"
    )
    worker_run.add_argument(
        "--state-db",
        type=Path,
        default=Path(".codex-agentic-os/state.sqlite3"),
        help="path to the runtime state database",
    )

    api = commands.add_parser("api", help="run a local operator HTTP API")
    api_commands = api.add_subparsers(dest="api_command", required=True)
    api_serve = api_commands.add_parser(
        "serve",
        help=(
            "start a loopback-only HTTP server exposing read-only run "
            "inspection endpoints plus approve/reject/cancel/retry "
            "mutation endpoints"
        ),
    )
    api_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="explicit loopback bind host, e.g. 127.0.0.1 or ::1 (default: 127.0.0.1)",
    )
    api_serve.add_argument(
        "--port", type=int, required=True, help="loopback bind port"
    )
    api_serve.add_argument(
        "--state-db",
        type=Path,
        default=Path(".codex-agentic-os/state.sqlite3"),
        help="path to the runtime state database",
    )

    provider = commands.add_parser("provider", help="inspect configured model providers")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)
    provider_commands.add_parser("list", help="list default provider specs")
    routing_policy = provider_commands.add_parser(
        "routing-policy", help="show capability-routing provider preference order"
    )
    provider_commands.add_parser(
        "credentials", help="report default provider credential readiness"
    )
    for command in (execute_next, worker_run, routing_policy):
        command.add_argument(
            "--provider-preference",
            action="append",
            default=[],
            choices=[kind.value for kind in ProviderKind],
            metavar="PROVIDER",
            help=(
                "ordered provider preference for capability routing; repeat to "
                "override the default registry order"
            ),
        )

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


def _parse_artifacts(values: Sequence[str]) -> tuple[ArtifactDeclaration, ...]:
    """Parse strict NAME=PATH declared artifact arguments."""

    declarations = []
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise ValueError("artifact must be NAME=PATH with non-empty name and path")
        declarations.append(ArtifactDeclaration(name=name, path=path))
    return tuple(declarations)


def _parse_tools(values: Sequence[str]) -> tuple[ToolDeclaration, ...]:
    """Parse each declared tool from a JSON object argument."""

    declarations = []
    for value in values:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("tool declaration must be valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("tool declaration must be a JSON object")
        allowed = {"name", "command", "description", "parameters"}
        if set(parsed) - allowed:
            raise ValueError("tool declaration has unknown fields")
        name = parsed.get("name")
        command = parsed.get("command")
        if not isinstance(name, str) or not name:
            raise ValueError("tool declaration requires a non-empty name")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(argument, str) for argument in command)
        ):
            raise ValueError(
                "tool declaration requires a non-empty command list of strings"
            )
        declarations.append(
            ToolDeclaration(
                name=name,
                command=tuple(command),
                description=parsed.get("description"),
                parameters=parsed.get("parameters"),
            )
        )
    return tuple(declarations)


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


def _plan_draft_payload(draft: PlanDraft) -> dict[str, object]:
    """Return a JSON-compatible, ordered view of one durable plan draft."""

    payload: dict[str, object] = {
        "plan_id": draft.plan_id,
        "run_id": draft.run_id,
        "status": draft.status,
        "revision": draft.revision,
        "steps": [_plan_step_proposal_payload(step) for step in draft.steps],
    }
    if draft.evidence is not None:
        payload["evidence"] = dict(draft.evidence)
    if draft.error is not None:
        payload["error"] = draft.error
    if draft.decision_agent_id is not None:
        payload["decision_agent_id"] = draft.decision_agent_id
    return payload


def _plan_step_proposal_payload(step: PlanStepProposal) -> dict[str, object]:
    """Return the standard JSON-compatible view of one proposed plan step."""

    payload = asdict(step)
    if step.message is None:
        payload.pop("message")
    if step.sandbox_policy is None:
        payload.pop("sandbox_policy")
    else:
        payload["sandbox_policy"]["kind"] = step.sandbox_policy.kind.value
    return payload


_WATCH_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)


def _watch_blocked_step(coordinator: RunCoordinator, run_id: str) -> RunStep | None:
    """Return the next queued step blocking dispatch on a pending approval, if any."""

    for step in coordinator.list_steps(run_id):
        if step.status is StepStatus.QUEUED:
            return step if step.approval_status is ApprovalStatus.PENDING else None
    return None


def _watch_run(
    coordinator: RunCoordinator,
    run_id: str,
    *,
    interval: float,
    after_sequence: int = 0,
    emit: Callable[[dict[str, object]], None],
    sleeper: Callable[[float], None] | None = None,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Poll one run's durable history until it is terminal or interrupted.

    Reuses ``RunCoordinator.list_history``/``list_steps`` only, so a watch
    session never creates or mutates state. Each durable entry is emitted
    exactly once per session, tracked by its sequence number; a step blocked
    on a pending approval is emitted once when first observed rather than on
    every poll tick, so a long-blocked run does not repeat the same notice.
    """

    if sleeper is None:
        sleeper = time.sleep
    last_sequence = after_sequence
    announced_blocked_step_id: str | None = None
    while should_continue():
        for entry in coordinator.list_history(run_id):
            if entry.sequence <= last_sequence:
                continue
            emit({"event": "history", **_history_payload([entry])[0]})
            last_sequence = entry.sequence
        blocked_step = _watch_blocked_step(coordinator, run_id)
        if blocked_step is None:
            announced_blocked_step_id = None
        elif blocked_step.step_id != announced_blocked_step_id:
            emit(
                {
                    "event": "blocked",
                    "run_id": run_id,
                    "step_id": blocked_step.step_id,
                    "position": blocked_step.position,
                    "objective": blocked_step.objective,
                    "reason": "approval_pending",
                }
            )
            announced_blocked_step_id = blocked_step.step_id
        run = coordinator.get(run_id)
        if run is None:
            raise ValueError(f"run does not exist: {run_id}")
        if run.status in _WATCH_TERMINAL_RUN_STATUSES:
            return
        if not should_continue():
            return
        sleeper(interval)


def _staleness_payload(evaluation: ClaimStaleness) -> dict[str, object]:
    """Return the standard JSON-compatible view of one staleness evaluation."""

    return asdict(evaluation)


def _artifact_listing_payload(artifacts: Sequence[ArtifactRecord]) -> list[dict[str, object]]:
    """Return one run's redacted artifact records with explicit run/step provenance."""

    payloads = []
    for artifact in artifacts:
        payload = _artifact_record_payload(artifact)
        payload["run_id"] = artifact.run_id
        payload["step_id"] = artifact.step_id
        payloads.append(payload)
    return payloads


def _agent_payload(agent: Agent) -> dict[str, object]:
    """Return the standard JSON-compatible view of one registered agent."""

    return asdict(agent)


def _policy_rule_payload(rule: ExecutionPolicyRule) -> dict[str, object]:
    """Return the standard JSON-compatible view of one policy rule."""

    return asdict(rule)


def _memory_entry_payload(entry: MemoryEntry) -> dict[str, object]:
    """Return the entry itself and only its explicit provenance."""

    return asdict(entry)


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


def _provider_adapter_resolver() -> Callable[[ProviderMessage], object]:
    """Build a chat adapter resolver from provider-message defaults, for dispatch."""

    def resolve(message: ProviderMessage):
        provider_spec = _chat_provider_spec(message.provider, message.model, None, None)
        return adapter_for(provider_spec)

    return resolve


def _provider_routing_policy(preferences: Sequence[str]) -> ProviderRoutingPolicy:
    """Build the explicit capability-routing policy for one CLI invocation."""

    if not preferences:
        return DEFAULT_PROVIDER_ROUTING_POLICY
    return ProviderRoutingPolicy(tuple(ProviderKind(value) for value in preferences))


def _install_worker_shutdown_signals() -> tuple[Callable[[], bool], Callable[[], None]]:
    """Request a clean worker stop on SIGINT/SIGTERM instead of raising.

    Returns a ``should_continue`` callable for ``run_worker`` that flips to
    ``False`` once either signal arrives, so the worker's own loop stops at
    its existing between-step boundary rather than being torn down mid-call,
    plus a ``restore`` callable the caller must invoke once the worker loop
    returns to put the process's prior signal disposition back.
    """

    stop_requested = False

    def _request_stop(signum: int, frame: object) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {
        sig: signal.signal(sig, _request_stop)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }

    def restore() -> None:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    return (lambda: not stop_requested), restore


def _persisted_sandbox_resolver() -> Callable[[SandboxPolicy], ContainerSandbox]:
    """Build a sandbox resolver that only trusts a step's persisted policy."""

    def resolve(policy: SandboxPolicy) -> ContainerSandbox:
        missing = tuple(
            name for name in policy.env_passthrough if name not in os.environ
        )
        if missing:
            raise ValueError(
                "environment variable is not set: " + ", ".join(missing)
            )
        return ContainerSandbox(
            SandboxSpec(
                kind=policy.kind,
                image=policy.image,
                mounts=policy.mounts,
                env=tuple(
                    (name, os.environ[name]) for name in policy.env_passthrough
                ),
                working_dir=policy.working_dir,
                network_enabled=policy.network_enabled,
            )
        )

    return resolve


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
            if arguments.run_command == "watch" and arguments.interval <= 0:
                raise ValueError("watch interval must be a positive number of seconds")
            if arguments.run_command == "watch" and arguments.after_sequence < 0:
                raise ValueError("watch after-sequence must be a non-negative integer")
            if arguments.run_command != "create" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.run_command in {
                "inspect",
                "inspect-step",
                "inspect-plan",
                "list",
                "history",
                "watch",
                "approvals",
                "staleness",
                "usage",
                "list-artifacts",
                "export-artifact",
            }
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
                message = None
                if any(
                    value is not None
                    for value in (
                        arguments.provider,
                        arguments.capability,
                        arguments.message,
                        arguments.model,
                        arguments.system,
                        arguments.temperature,
                        arguments.max_tokens,
                    )
                ):
                    message = ProviderMessage(
                        provider=arguments.provider,
                        content=arguments.message or "",
                        model=arguments.model,
                        system=arguments.system,
                        temperature=arguments.temperature,
                        max_tokens=arguments.max_tokens,
                        required_capability=arguments.capability,
                    )
                sandbox_policy = None
                if any(
                    value not in (None, False, [])
                    for value in (
                        arguments.sandbox,
                        arguments.image,
                        arguments.mount,
                        arguments.env_passthrough,
                        arguments.workdir,
                        arguments.network,
                    )
                ):
                    if arguments.sandbox is None:
                        raise ValueError("sandbox policy requires --sandbox")
                    mounts = _parse_mounts(arguments.mount)
                    sandbox_policy = (
                        SandboxPolicy(
                            kind=SandboxKind(arguments.sandbox),
                            image=arguments.image,
                            mounts=mounts,
                            working_dir=arguments.workdir,
                            env_passthrough=tuple(arguments.env_passthrough),
                            network_enabled=arguments.network,
                        )
                        if arguments.image is not None
                        else SandboxPolicy(
                            kind=SandboxKind(arguments.sandbox),
                            mounts=mounts,
                            working_dir=arguments.workdir,
                            env_passthrough=tuple(arguments.env_passthrough),
                            network_enabled=arguments.network,
                        )
                    )
                delegation = None
                if arguments.delegate_objective is not None:
                    delegation = DelegationSpec(
                        child_objective=arguments.delegate_objective,
                        target_agent_id=arguments.delegate_target_agent,
                    )
                elif arguments.delegate_target_agent is not None:
                    raise ValueError(
                        "--delegate-target-agent requires --delegate-objective"
                    )
                coordinator.add_step(
                    arguments.run_id,
                    arguments.step_id,
                    objective=arguments.objective,
                    command=arguments.step_command or None,
                    timeout=arguments.timeout,
                    message=message,
                    context_step_ids=arguments.context_step,
                    approval_required=arguments.approval_required,
                    sandbox_policy=sandbox_policy,
                    tools=_parse_tools(arguments.tool) or None,
                    tool_iteration_budget=arguments.tool_iteration_budget,
                    artifacts=_parse_artifacts(arguments.artifact) or None,
                    response_artifact_name=arguments.response_artifact,
                    delegation=delegation,
                )
                run_id = arguments.run_id
            elif arguments.run_command == "plan":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                draft = coordinator.propose_plan(
                    arguments.run_id,
                    arguments.plan_id,
                    adapter_resolver=_provider_adapter_resolver(),
                    provider=arguments.provider,
                    model=arguments.model,
                    temperature=arguments.temperature,
                    max_tokens=arguments.max_tokens,
                    objective=arguments.objective,
                )
                print(json.dumps(_plan_draft_payload(draft), indent=2, sort_keys=True))
                return
            elif arguments.run_command == "inspect-plan":
                draft = coordinator.get_plan(arguments.plan_id)
                if draft is None:
                    raise ValueError(f"plan does not exist: {arguments.plan_id}")
                print(json.dumps(_plan_draft_payload(draft), indent=2, sort_keys=True))
                return
            elif arguments.run_command == "accept-plan":
                draft, _ = coordinator.accept_plan(
                    arguments.plan_id,
                    expected_revision=arguments.expected_revision,
                    agent_id=arguments.agent_id,
                )
                print(json.dumps(_plan_draft_payload(draft), indent=2, sort_keys=True))
                return
            elif arguments.run_command == "reject-plan":
                draft = coordinator.reject_plan(
                    arguments.plan_id,
                    expected_revision=arguments.expected_revision,
                    agent_id=arguments.agent_id,
                )
                print(json.dumps(_plan_draft_payload(draft), indent=2, sort_keys=True))
                return
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
                retry_entries = tuple(
                    entry
                    for entry in coordinator.list_history(step.run_id)
                    if entry.transition == "step_retried"
                )
                retried_from_step_id = next(
                    (
                        entry.retried_step_id
                        for entry in retry_entries
                        if entry.step_id == step.step_id
                    ),
                    None,
                )
                retried_into_step_id = next(
                    (
                        entry.step_id
                        for entry in retry_entries
                        if entry.retried_step_id == step.step_id
                    ),
                    None,
                )
                print(
                    json.dumps(
                        _step_payload(
                            step,
                            retried_from_step_id=retried_from_step_id,
                            retried_into_step_id=retried_into_step_id,
                            artifacts=coordinator.list_artifacts(
                                step.run_id, step_id=step.step_id
                            ),
                        ),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            elif arguments.run_command == "history":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                print(
                    json.dumps(
                        _history_payload(coordinator.list_history(arguments.run_id)),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            elif arguments.run_command == "watch":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                should_continue, restore_shutdown_signals = (
                    _install_worker_shutdown_signals()
                )
                try:
                    _watch_run(
                        coordinator,
                        arguments.run_id,
                        interval=arguments.interval,
                        after_sequence=arguments.after_sequence,
                        emit=lambda payload: print(json.dumps(payload, sort_keys=True)),
                        should_continue=should_continue,
                    )
                finally:
                    restore_shutdown_signals()
                return
            elif arguments.run_command == "approvals":
                print(
                    json.dumps(
                        _approval_payload(coordinator, arguments.run_id),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            elif arguments.run_command == "usage":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                print(
                    json.dumps(
                        _usage_payload(coordinator, arguments.run_id),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            elif arguments.run_command == "staleness":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                evaluation = coordinator.evaluate_claim_staleness(
                    arguments.run_id, threshold_seconds=arguments.threshold_seconds
                )
                print(
                    json.dumps(
                        _staleness_payload(evaluation), indent=2, sort_keys=True
                    )
                )
                return
            elif arguments.run_command == "list-artifacts":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                artifacts = coordinator.list_artifacts(
                    arguments.run_id, step_id=arguments.step_id
                )
                print(
                    json.dumps(
                        _artifact_listing_payload(artifacts), indent=2, sort_keys=True
                    )
                )
                return
            elif arguments.run_command == "export-artifact":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                matches = [
                    artifact
                    for artifact in coordinator.list_artifacts(
                        arguments.run_id, step_id=arguments.step_id
                    )
                    if artifact.name == arguments.name
                ]
                if not matches:
                    raise ValueError(f"artifact not found: {arguments.name}")
                if len(matches) > 1:
                    raise ValueError(
                        f"ambiguous artifact name across steps: {arguments.name}; "
                        "disambiguate with --step"
                    )
                artifact = matches[0]
                content = coordinator.read_artifact_content(artifact.artifact_id)
                arguments.destination.write_bytes(content)
                print(
                    json.dumps(
                        {
                            "run_id": artifact.run_id,
                            "step_id": artifact.step_id,
                            "name": artifact.name,
                            "artifact_id": artifact.artifact_id,
                            "content_hash": artifact.content_hash,
                            "size_bytes": artifact.size_bytes,
                            "destination": str(arguments.destination),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
                return
            elif arguments.run_command == "reassign-claim":
                if coordinator.get(arguments.run_id) is None:
                    raise ValueError(f"run does not exist: {arguments.run_id}")
                coordinator.reassign_stale_claim(
                    arguments.run_id,
                    arguments.replacement_agent_id,
                    expected_agent_id=arguments.expected_agent_id,
                    expected_revision=arguments.expected_revision,
                    threshold_seconds=arguments.threshold_seconds,
                )
                run_id = arguments.run_id
            elif arguments.run_command == "retry-step":
                step = coordinator.get_step(arguments.step_id)
                if step is None:
                    raise ValueError(f"step does not exist: {arguments.step_id}")
                coordinator.retry_step(
                    arguments.step_id,
                    arguments.new_step_id,
                    expected_step_revision=arguments.expected_step_revision,
                    expected_run_revision=arguments.expected_run_revision,
                )
                run_id = step.run_id
            elif arguments.run_command in {"approve", "reject"}:
                step = coordinator.get_step(arguments.step_id)
                if step is None:
                    raise ValueError(f"step does not exist: {arguments.step_id}")
                if arguments.run_command == "approve":
                    coordinator.approve_step(
                        arguments.step_id, agent_id=arguments.agent_id
                    )
                else:
                    coordinator.reject_step(
                        arguments.step_id, agent_id=arguments.agent_id
                    )
                run_id = step.run_id
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
                steps = coordinator.list_steps(arguments.run_id)
                running_delegation = next(
                    (
                        step
                        for step in steps
                        if step.status is StepStatus.RUNNING
                        and step.delegation is not None
                    ),
                    None,
                )
                if running_delegation is not None:
                    try:
                        coordinator.execute_next_step(arguments.run_id)
                    except DelegationPendingError:
                        payload = _run_payload(coordinator, arguments.run_id)
                        payload["execution"] = {"attempted": False}
                        print(json.dumps(payload, indent=2, sort_keys=True))
                        return
                    print(
                        json.dumps(
                            _run_payload(coordinator, arguments.run_id),
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    return
                next_step = next(
                    (
                        step
                        for step in steps
                        if step.status is StepStatus.QUEUED
                    ),
                    None,
                )
                if next_step is None:
                    payload = _run_payload(coordinator, arguments.run_id)
                    payload["execution"] = {"attempted": False}
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return
                if next_step.delegation is not None:
                    result = coordinator.execute_next_step(arguments.run_id)
                    run_id = arguments.run_id
                    if result is None:
                        payload = _run_payload(coordinator, run_id)
                        payload["execution"] = {"attempted": False}
                        print(json.dumps(payload, indent=2, sort_keys=True))
                        return
                    print(
                        json.dumps(_run_payload(coordinator, run_id), indent=2, sort_keys=True)
                    )
                    return
                sandbox_flags_supplied = any(
                    (
                        arguments.sandbox is not None,
                        arguments.image is not None,
                        bool(arguments.mount),
                        bool(arguments.env),
                        arguments.workdir is not None,
                        arguments.network,
                    )
                )
                if next_step.sandbox_policy is not None and sandbox_flags_supplied:
                    raise ValueError(
                        "next command step has a persisted sandbox policy; "
                        "per-invocation sandbox flags are not allowed"
                    )
                if (
                    next_step.command is not None
                    and next_step.sandbox_policy is None
                    and arguments.sandbox is None
                ):
                    raise ValueError("next command step requires --sandbox")
                kind = (
                    SandboxKind(arguments.sandbox)
                    if arguments.sandbox is not None
                    else None
                )
                if arguments.image is not None and not arguments.image.strip():
                    raise ValueError("sandbox image must not be empty")
                mounts = _parse_mounts(arguments.mount)
                env = _parse_env(arguments.env)
                working_dir = arguments.workdir
                network_enabled = arguments.network
                if next_step.message is not None:
                    result = coordinator.execute_next_step(
                        arguments.run_id,
                        adapter_resolver=_provider_adapter_resolver(),
                        routing_policy=_provider_routing_policy(
                            arguments.provider_preference
                        ),
                        sandbox_resolver=(
                            _persisted_sandbox_resolver()
                            if next_step.tool_declarations
                            else None
                        ),
                    )
                else:
                    if next_step.sandbox_policy is not None:
                        result = coordinator.execute_next_step(
                            arguments.run_id,
                            sandbox_resolver=_persisted_sandbox_resolver(),
                        )
                    else:
                        assert kind is not None
                        spec = (
                            SandboxSpec(
                                kind=kind,
                                image=arguments.image,
                                mounts=mounts,
                                env=env,
                                working_dir=working_dir,
                                network_enabled=network_enabled,
                            )
                            if arguments.image is not None
                            else SandboxSpec(
                                kind=kind,
                                mounts=mounts,
                                env=env,
                                working_dir=working_dir,
                                network_enabled=network_enabled,
                            )
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
            elif arguments.run_command in {"approve", "reject", "retry-step"}:
                pass
            else:
                run_id = arguments.run_id
            print(json.dumps(_run_payload(coordinator, run_id), indent=2, sort_keys=True))
        elif arguments.command == "agent":
            if arguments.agent_command != "register" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.agent_command in {"inspect", "list"}
            registry = AgentRegistry(StateStore(arguments.state_db, read_only=read_only))
            if arguments.agent_command == "register":
                registered = registry.register(arguments.agent_id, label=arguments.label)
                print(json.dumps(_agent_payload(registered), indent=2, sort_keys=True))
            elif arguments.agent_command == "heartbeat":
                agent = registry.heartbeat(arguments.agent_id)
                print(json.dumps(_agent_payload(agent), indent=2, sort_keys=True))
            elif arguments.agent_command == "inspect":
                agent = registry.get(arguments.agent_id)
                if agent is None:
                    raise ValueError(f"agent does not exist: {arguments.agent_id}")
                print(json.dumps(_agent_payload(agent), indent=2, sort_keys=True))
            else:
                print(
                    json.dumps(
                        [_agent_payload(agent) for agent in registry.list_agents()],
                        indent=2,
                        sort_keys=True,
                    )
                )
        elif arguments.command == "policy":
            if arguments.policy_command != "create" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.policy_command in {"inspect", "list"}
            policy_registry = ExecutionPolicyRegistry(
                StateStore(arguments.state_db, read_only=read_only)
            )
            if arguments.policy_command == "create":
                created = policy_registry.create_rule(
                    arguments.rule_id,
                    criterion_kind=arguments.criterion_kind,
                    criterion_value=arguments.criterion_value,
                    reason=arguments.reason,
                    precedence=arguments.precedence,
                    enabled=not arguments.disabled,
                )
                print(json.dumps(_policy_rule_payload(created), indent=2, sort_keys=True))
            elif arguments.policy_command == "inspect":
                rule = policy_registry.get(arguments.rule_id)
                if rule is None:
                    raise ValueError(f"policy rule does not exist: {arguments.rule_id}")
                print(json.dumps(_policy_rule_payload(rule), indent=2, sort_keys=True))
            else:
                print(
                    json.dumps(
                        [
                            _policy_rule_payload(rule)
                            for rule in policy_registry.list_rules()
                        ],
                        indent=2,
                        sort_keys=True,
                    )
                )
        elif arguments.command == "memory":
            if arguments.memory_command != "create" and not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            read_only = arguments.memory_command in {"inspect", "list"}
            memory_registry = MemoryRegistry(
                StateStore(arguments.state_db, read_only=read_only)
            )
            if arguments.memory_command == "create":
                created = memory_registry.create(
                    arguments.name,
                    body=arguments.body,
                    kind=arguments.kind,
                    agent_id=arguments.agent_id,
                    run_id=arguments.run_id,
                    step_id=arguments.step_id,
                )
                print(json.dumps(_memory_entry_payload(created), indent=2, sort_keys=True))
            elif arguments.memory_command == "inspect":
                entry = memory_registry.get(arguments.name)
                if entry is None:
                    raise ValueError(f"memory entry does not exist: {arguments.name}")
                print(json.dumps(_memory_entry_payload(entry), indent=2, sort_keys=True))
            else:
                print(
                    json.dumps(
                        [
                            _memory_entry_payload(entry)
                            for entry in memory_registry.list_entries()
                        ],
                        indent=2,
                        sort_keys=True,
                    )
                )
        elif arguments.command == "worker":
            store = StateStore(arguments.state_db)
            coordinator = RunCoordinator(store)
            registry = AgentRegistry(store)
            should_continue, restore_shutdown_signals = _install_worker_shutdown_signals()
            try:
                summary = run_worker(
                    coordinator,
                    registry,
                    arguments.agent_id,
                    heartbeat_interval=arguments.heartbeat_interval,
                    poll_interval=arguments.poll_interval,
                    sandbox_resolver=_persisted_sandbox_resolver(),
                    adapter_resolver=_provider_adapter_resolver(),
                    routing_policy=_provider_routing_policy(
                        arguments.provider_preference
                    ),
                    label=arguments.label,
                    should_continue=should_continue,
                )
            finally:
                restore_shutdown_signals()
            print(
                json.dumps(
                    {
                        "agent_id": summary.agent_id,
                        "claimed_run_ids": list(summary.claimed_run_ids),
                        "executed_step_ids": list(summary.executed_step_ids),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        elif arguments.command == "api":
            if not is_loopback_bind_host(arguments.host):
                raise ValueError(
                    "HTTP API host must be an explicit loopback address, not "
                    f"{arguments.host!r}"
                )
            if not arguments.state_db.is_file():
                raise ValueError(f"state database does not exist: {arguments.state_db}")
            coordinator = RunCoordinator(StateStore(arguments.state_db, read_only=True))
            server = build_server(coordinator, arguments.host, arguments.port)
            should_continue, restore_shutdown_signals = _install_worker_shutdown_signals()
            print(
                json.dumps(
                    {"host": arguments.host, "port": server.server_address[1]},
                    indent=2,
                    sort_keys=True,
                )
            )
            try:
                serve_until_stopped(server, should_continue=should_continue)
            finally:
                server.server_close()
                restore_shutdown_signals()
        elif arguments.command == "provider":
            if arguments.provider_command == "list":
                providers: object = [
                    spec.to_dict() for spec in DEFAULT_PROVIDER_SPECS
                ]
            elif arguments.provider_command == "routing-policy":
                providers = _provider_routing_policy(
                    arguments.provider_preference
                ).to_dict()
            else:
                providers = [
                    {
                        "kind": spec.kind.value,
                        "api_key_env": spec.api_key_env,
                        "configured": (
                            spec.api_key_env is None or bool(os.getenv(spec.api_key_env))
                        ),
                    }
                    for spec in DEFAULT_PROVIDER_SPECS
                ]
            print(
                json.dumps(
                    providers,
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
