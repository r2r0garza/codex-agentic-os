"""Sandbox backend selection for agent execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import subprocess
from typing import Callable, Sequence


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
    mounts: tuple[tuple[str, str], ...] = ()
    env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate host-to-container bind mount pairs and environment variables."""

        for mount in self.mounts:
            if len(mount) != 2 or not all(
                isinstance(path, str) and path for path in mount
            ):
                raise ValueError("sandbox mounts require non-empty host and container paths")
        for pair in self.env:
            if len(pair) != 2 or not all(
                isinstance(part, str) and part for part in pair
            ):
                raise ValueError("sandbox env vars require non-empty key and value")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Captured result of a command executed in a container sandbox."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[Sequence[str], float | None], subprocess.CompletedProcess[str]]


def _subprocess_runner(
    command: Sequence[str], timeout: float | None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - argv is constructed without a shell
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"sandbox backend is not installed: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"sandbox command timed out after {timeout} seconds") from exc


class ContainerSandbox:
    """Execute commands with Docker or Podman using conservative defaults."""

    def __init__(self, spec: SandboxSpec, *, runner: ProcessRunner = _subprocess_runner) -> None:
        self.spec = spec
        self._runner = runner

    def command(self, argv: Sequence[str]) -> tuple[str, ...]:
        """Build the deterministic container-engine argument vector."""

        if not argv:
            raise ValueError("sandbox commands require at least one argument")
        if not self.spec.image.strip():
            raise ValueError("sandbox image must not be empty")

        command = [self.spec.kind.value, "run", "--rm"]
        command.extend(("--network", "bridge" if self.spec.network_enabled else "none"))
        if self.spec.read_only_root:
            command.append("--read-only")
        if self.spec.cpu_limit is not None:
            command.extend(("--cpus", self.spec.cpu_limit))
        if self.spec.memory_limit is not None:
            command.extend(("--memory", self.spec.memory_limit))
        for host_path, container_path in self.spec.mounts:
            command.extend(("--volume", f"{host_path}:{container_path}"))
        for key, value in self.spec.env:
            command.extend(("--env", f"{key}={value}"))
        command.append(self.spec.image)
        command.extend(argv)
        return tuple(command)

    def execute(self, argv: Sequence[str], *, timeout: float | None = None) -> SandboxResult:
        """Run a command and return its exit status and captured output."""

        if timeout is not None and timeout <= 0:
            raise ValueError("sandbox timeout must be positive")
        command = self.command(argv)
        completed = self._runner(command, timeout)
        return SandboxResult(
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def default_sandboxes() -> tuple[SandboxSpec, SandboxSpec]:
    """Return the required sandbox backends in preferred order."""

    return (SandboxSpec(kind=SandboxKind.DOCKER), SandboxSpec(kind=SandboxKind.PODMAN))
