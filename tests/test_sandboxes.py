import subprocess
from collections.abc import Sequence

import pytest

from codex_agentic_os.sandboxes import ContainerSandbox, SandboxKind, SandboxSpec


@pytest.mark.parametrize("kind", [SandboxKind.DOCKER, SandboxKind.PODMAN])
def test_container_sandbox_executes_with_conservative_defaults(kind: SandboxKind) -> None:
    calls: list[tuple[tuple[str, ...], float | None]] = []

    def runner(command: Sequence[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
        calls.append((tuple(command), timeout))
        return subprocess.CompletedProcess(command, 7, stdout="output\n", stderr="warning\n")

    result = ContainerSandbox(SandboxSpec(kind=kind), runner=runner).execute(
        ("python", "-c", "print('ok')"), timeout=12
    )

    expected = (
        kind.value,
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cpus",
        "2",
        "--memory",
        "4g",
        "python:3.12-slim",
        "python",
        "-c",
        "print('ok')",
    )
    assert calls == [(expected, 12)]
    assert result.command == expected
    assert (result.returncode, result.stdout, result.stderr) == (7, "output\n", "warning\n")


def test_container_sandbox_respects_optional_limits_and_network() -> None:
    spec = SandboxSpec(
        kind=SandboxKind.PODMAN,
        image="busybox:stable",
        network_enabled=True,
        read_only_root=False,
        cpu_limit=None,
        memory_limit=None,
    )

    assert ContainerSandbox(spec).command(("true",)) == (
        "podman",
        "run",
        "--rm",
        "--network",
        "bridge",
        "busybox:stable",
        "true",
    )


def test_container_sandbox_renders_mounts_after_resource_flags() -> None:
    spec = SandboxSpec(
        kind=SandboxKind.DOCKER,
        mounts=(("/host/repo", "/workspace"), ("/host/cache", "/cache")),
    )

    command = ContainerSandbox(spec).command(("true",))

    assert command[-6:] == (
        "--volume",
        "/host/repo:/workspace",
        "--volume",
        "/host/cache:/cache",
        "python:3.12-slim",
        "true",
    )


def test_container_sandbox_renders_one_mount() -> None:
    spec = SandboxSpec(kind=SandboxKind.PODMAN, mounts=(("/host", "/container"),))

    assert ContainerSandbox(spec).command(("true",))[-4:] == (
        "--volume",
        "/host:/container",
        "python:3.12-slim",
        "true",
    )


@pytest.mark.parametrize(
    "mounts", [(('', '/workspace'),), (('/host', ''),), (('/only',),)]
)
def test_sandbox_spec_rejects_malformed_mount_pairs(mounts) -> None:
    with pytest.raises(ValueError, match="non-empty host and container paths"):
        SandboxSpec(kind=SandboxKind.DOCKER, mounts=mounts)


def test_container_sandbox_renders_env_after_mounts() -> None:
    spec = SandboxSpec(
        kind=SandboxKind.DOCKER,
        mounts=(("/host/repo", "/workspace"),),
        env=(("API_KEY", "secret"), ("DEBUG", "1")),
    )

    command = ContainerSandbox(spec).command(("true",))

    assert command[-6:] == (
        "--env",
        "API_KEY=secret",
        "--env",
        "DEBUG=1",
        "python:3.12-slim",
        "true",
    )


def test_container_sandbox_renders_one_env_var() -> None:
    spec = SandboxSpec(kind=SandboxKind.PODMAN, env=(("KEY", "value"),))

    assert ContainerSandbox(spec).command(("true",))[-4:] == (
        "--env",
        "KEY=value",
        "python:3.12-slim",
        "true",
    )


def test_container_sandbox_no_env_is_no_op() -> None:
    spec = SandboxSpec(kind=SandboxKind.DOCKER)

    assert "--env" not in ContainerSandbox(spec).command(("true",))


@pytest.mark.parametrize(
    "env", [(('', 'value'),), (('KEY', ''),), (('ONLY',),)]
)
def test_sandbox_spec_rejects_malformed_env_pairs(env) -> None:
    with pytest.raises(ValueError, match="non-empty key and value"):
        SandboxSpec(kind=SandboxKind.DOCKER, env=env)


@pytest.mark.parametrize(
    ("argv", "timeout", "message"),
    [
        ((), None, "at least one argument"),
        (("true",), 0, "timeout must be positive"),
    ],
)
def test_container_sandbox_rejects_invalid_requests(
    argv: tuple[str, ...], timeout: float | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ContainerSandbox(SandboxSpec(kind=SandboxKind.DOCKER)).execute(argv, timeout=timeout)


def test_container_sandbox_reports_missing_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(RuntimeError, match="sandbox backend is not installed: docker"):
        ContainerSandbox(SandboxSpec(kind=SandboxKind.DOCKER)).execute(("true",))
