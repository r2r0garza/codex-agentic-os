"""JSON-compatible read-only run payload builders shared by the CLI and HTTP API.

Kept separate from ``cli.py`` so the loopback HTTP API (``api.py``) can reuse
these exact durable-run JSON contracts instead of inventing divergent shapes.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Sequence

from .runtime import (
    ArtifactRecord,
    RunCoordinator,
    RunHistoryEntry,
    RunStatus,
    RunStep,
    StepStatus,
)


def _artifact_record_payload(artifact: ArtifactRecord) -> dict[str, object]:
    """Return the standard JSON-compatible, redacted view of one artifact record."""

    payload: dict[str, object] = {
        "artifact_id": artifact.artifact_id,
        "name": artifact.name,
        "status": artifact.status.value,
        "source_path": artifact.source_path,
    }
    if artifact.content_hash is not None:
        payload["content_hash"] = artifact.content_hash
    if artifact.size_bytes is not None:
        payload["size_bytes"] = artifact.size_bytes
    if artifact.size_limit_bytes is not None:
        payload["size_limit_bytes"] = artifact.size_limit_bytes
    return payload


def _step_payload(
    step: RunStep,
    *,
    retried_from_step_id: str | None = None,
    retried_into_step_id: str | None = None,
    artifacts: Sequence[ArtifactRecord] = (),
) -> dict[str, object]:
    """Return the standard JSON-compatible view of one durable step."""

    payload = asdict(step)
    # Approval presentation is introduced with the dedicated Sprint 6 CLI slice.
    payload.pop("approval_required")
    payload.pop("approval_status")
    if step.message is None:
        payload.pop("message")
    if not step.context_step_ids:
        payload.pop("context_step_ids")
    if step.sandbox_policy is None:
        payload.pop("sandbox_policy")
    else:
        payload["sandbox_policy"]["kind"] = step.sandbox_policy.kind.value
    if not step.tool_declarations:
        payload.pop("tool_declarations")
    if step.tool_iteration_budget is None:
        payload.pop("tool_iteration_budget")
    if not step.tool_iterations:
        payload.pop("tool_iterations")
    else:
        for iteration in payload["tool_iterations"]:
            iteration["tool_call"]["phase"] = iteration["tool_call"]["phase"].value
        # Preserve the established trusted single-round view as a latest-call alias.
        payload["tool_call"] = dict(payload["tool_iterations"][-1]["tool_call"])
    if not step.artifact_declarations:
        payload.pop("artifact_declarations")
    if step.response_artifact_name is None:
        payload.pop("response_artifact_name")
    if step.delegation is None:
        payload.pop("delegation")
    if step.delegated_run_id is None:
        payload.pop("delegated_run_id")
    if step.policy_rule_id is None:
        payload.pop("policy_rule_id")
        payload.pop("policy_reason")
    if artifacts:
        payload["artifacts"] = [_artifact_record_payload(artifact) for artifact in artifacts]
    payload["status"] = step.status.value
    if step.status is StepStatus.FAILED:
        payload["failure_kind"] = (
            None if step.failure_kind is None else step.failure_kind.value
        )
        payload["retry_eligible"] = step.retry_eligible
    if retried_from_step_id is not None:
        payload["retried_from_step_id"] = retried_from_step_id
    if retried_into_step_id is not None:
        payload["retried_into_step_id"] = retried_into_step_id
    return payload


def _run_payload(coordinator: RunCoordinator, run_id: str) -> dict[str, object]:
    """Return a JSON-compatible, ordered view of one durable run."""

    run = coordinator.get(run_id)
    if run is None:
        raise ValueError(f"run does not exist: {run_id}")
    run_data = asdict(run)
    run_data["status"] = run.status.value
    retry_lineage = {
        entry.retried_step_id: entry.step_id
        for entry in coordinator.list_history(run_id)
        if entry.transition == "step_retried"
        and entry.retried_step_id is not None
        and entry.step_id is not None
    }
    retried_from = {
        new_step_id: prior_step_id
        for prior_step_id, new_step_id in retry_lineage.items()
    }
    steps = []
    for step in coordinator.list_steps(run_id):
        steps.append(
            _step_payload(
                step,
                retried_from_step_id=retried_from.get(step.step_id),
                retried_into_step_id=retry_lineage.get(step.step_id),
                artifacts=coordinator.list_artifacts(run_id, step_id=step.step_id),
            )
        )
    return {"run": run_data, "steps": steps}


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


def _history_payload(entries: Sequence[RunHistoryEntry]) -> list[dict[str, object]]:
    """Return one run's durable history entries in stable sequence order."""

    payloads = []
    for entry in entries:
        payload = asdict(entry)
        for optional_field in (
            "plan_id",
            "required_capability",
            "resolved_provider",
            "resolved_model",
            "routing_reason",
            "artifact_name",
            "parent_run_id",
            "parent_step_id",
            "delegated_run_id",
            "tool_name",
            "tool_outcome",
            "tool_iteration",
            "tool_phase",
            "policy_rule_id",
            "policy_reason",
        ):
            if getattr(entry, optional_field) is None:
                payload.pop(optional_field)
        payloads.append(payload)
    return payloads


def _approval_payload(
    coordinator: RunCoordinator, run_id: str
) -> list[dict[str, object]]:
    """Return sanitized approval requests and their known agent attribution."""

    run = coordinator.get(run_id)
    if run is None:
        raise ValueError(f"run does not exist: {run_id}")
    deciding_agents = {
        entry.step_id: entry.agent_id
        for entry in coordinator.list_history(run_id)
        if entry.transition in {"step_approved", "step_rejected"}
    }
    requests = []
    for step in coordinator.list_steps(run_id):
        if not step.approval_required:
            continue
        requests.append(
            {
                "step_id": step.step_id,
                "run_id": step.run_id,
                "position": step.position,
                "objective": step.objective,
                "step_status": step.status.value,
                "approval_required": True,
                "approval_status": (
                    None if step.approval_status is None else step.approval_status.value
                ),
                "execution_kind": (
                    "command"
                    if step.command is not None
                    else "delegation" if step.delegation is not None else "provider"
                ),
                "requesting_agent_id": run.agent_id,
                "deciding_agent_id": deciding_agents.get(step.step_id),
            }
        )
    return requests


def _usage_payload(coordinator: RunCoordinator, run_id: str) -> dict[str, object]:
    """Return one run's provider usage evidence in order and a token aggregate."""

    if coordinator.get(run_id) is None:
        raise ValueError(f"run does not exist: {run_id}")
    steps = []
    available_count = 0
    unavailable_count = 0
    total_input_tokens = 0
    total_output_tokens = 0
    for step in coordinator.list_steps(run_id):
        if step.message is None:
            continue
        raw_usage = step.output.get("usage") if step.output is not None else None
        if isinstance(raw_usage, dict):
            usage = {
                "available": bool(raw_usage.get("available")),
                "input_tokens": raw_usage.get("input_tokens"),
                "output_tokens": raw_usage.get("output_tokens"),
                "raw": raw_usage.get("raw"),
                "unavailable_reason": raw_usage.get("unavailable_reason"),
            }
        else:
            usage = {
                "available": False,
                "input_tokens": None,
                "output_tokens": None,
                "raw": None,
                "unavailable_reason": (
                    f"no usage recorded for step status {step.status.value}"
                ),
            }
        if usage["available"]:
            available_count += 1
            if isinstance(usage["input_tokens"], int):
                total_input_tokens += usage["input_tokens"]
            if isinstance(usage["output_tokens"], int):
                total_output_tokens += usage["output_tokens"]
        else:
            unavailable_count += 1
        model = None
        if step.output is not None and isinstance(step.output.get("model"), str):
            model = step.output.get("model")
        if model is None:
            model = step.message.model
        steps.append(
            {
                "step_id": step.step_id,
                "position": step.position,
                "status": step.status.value,
                "provider": step.message.provider,
                "model": model,
                "usage": usage,
            }
        )
    return {
        "run_id": run_id,
        "steps": steps,
        "aggregate": {
            "steps_with_usage_available": available_count,
            "steps_with_usage_unavailable": unavailable_count,
            "input_tokens": total_input_tokens if available_count else None,
            "output_tokens": total_output_tokens if available_count else None,
        },
    }
