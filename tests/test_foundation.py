from codex_agentic_os.providers import (
    DEFAULT_PROVIDER_SPECS,
    OPENROUTER_DEFAULT_BASE_URL,
    ProviderKind,
)
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


def test_default_openrouter_spec_uses_canonical_endpoint() -> None:
    openrouter = next(spec for spec in DEFAULT_PROVIDER_SPECS if spec.kind is ProviderKind.OPENROUTER)

    assert openrouter.base_url == OPENROUTER_DEFAULT_BASE_URL


def test_required_sandbox_backends_are_declared() -> None:
    declared = {spec.kind for spec in default_sandboxes()}

    assert declared == {SandboxKind.DOCKER, SandboxKind.PODMAN}
