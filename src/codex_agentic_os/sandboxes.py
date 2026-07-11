"""Sandbox backend selection for agent execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class SandboxKind(StrEnum):
    """Supported sandbox/container execution backends."""

    DOCKER = "docker"
    PODMAN = "podman"


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """Container sandbox settings for a single agent task."""

    kind: SandboxKind
    image: str = "python:3.12-slim"
    network_enabled: bool = False
    read_only_root: bool = True
    cpu_limit: str | None = "2"
    memory_limit: str | None = "4g"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        data = asdict(self)
        data["kind"] = self.kind.value
        return data


def default_sandboxes() -> tuple[SandboxSpec, SandboxSpec]:
    """Return the required sandbox backends in preferred order."""

    return (SandboxSpec(kind=SandboxKind.DOCKER), SandboxSpec(kind=SandboxKind.PODMAN))
