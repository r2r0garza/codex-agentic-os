from codex_agentic_os.providers import DEFAULT_PROVIDER_SPECS, ProviderKind
from codex_agentic_os.sandboxes import SandboxKind, default_sandboxes


def test_required_provider_families_are_declared() -> None:
    declared = {spec.kind for spec in DEFAULT_PROVIDER_SPECS}

    assert declared == {
        ProviderKind.OPENAI,
        ProviderKind.ANTHROPIC,
        ProviderKind.GOOGLE,
        ProviderKind.OPENROUTER,
        ProviderKind.LM_STUDIO,
        ProviderKind.OLLAMA,
        ProviderKind.OPENAI_COMPATIBLE,
    }


def test_required_sandbox_backends_are_declared() -> None:
    declared = {spec.kind for spec in default_sandboxes()}

    assert declared == {SandboxKind.DOCKER, SandboxKind.PODMAN}
