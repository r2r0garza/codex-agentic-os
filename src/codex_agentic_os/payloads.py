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
    if not step.artifact_declarations:
        payload.pop("artifact_declarations")
    if step.response_artifact_name is None:
        payload.pop("response_artifact_name")
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
        ):
            if getattr(entry, optional_field) is None:
                payload.pop(optional_field)
        payloads.append(payload)
    return payloads
