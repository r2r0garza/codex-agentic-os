"""Model-provider registry primitives.

The OS is intentionally provider-neutral. This module captures the providers
that first-class agents must be able to target without hard-coding runtime
implementations too early.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class ProviderKind(StrEnum):
    """Supported model provider families."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OPENROUTER = "openrouter"
    LM_STUDIO = "lm_studio"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


# Canonical endpoints used both as registry defaults and as chat-adapter
# fallbacks when a provider spec omits ``base_url``.
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
LM_STUDIO_DEFAULT_BASE_URL = "http://localhost:1234/v1"
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Configuration needed to route an agent request to a model provider.

    Endpoint and credential policy, enforced by ``chat.OpenAICompatibleAdapter``:

    - ``OPENAI_COMPATIBLE`` requires an explicit ``base_url``; it is never defaulted
      to a public endpoint, so a misconfigured spec fails before any request is sent.
    - ``LM_STUDIO`` and ``OLLAMA`` fall back to their standard local server URLs
      (``LM_STUDIO_DEFAULT_BASE_URL`` / ``OLLAMA_DEFAULT_BASE_URL``) when ``base_url``
      is omitted, and never require ``api_key_env`` for local use.
    - ``OPENROUTER`` falls back to ``OPENROUTER_DEFAULT_BASE_URL`` when ``base_url``
      is omitted; an explicit URL still takes precedence.
    - All provider kinds treat ``api_key_env`` as optional: when unset, or when the
      named environment variable has no value, requests omit the Authorization
      header instead of failing.
    """

    kind: ProviderKind
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    supports_tools: bool = True
    supports_streaming: bool = True
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        data = asdict(self)
        data["kind"] = self.kind.value
        data["capabilities"] = list(self.capabilities)
        return data


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """One deterministic capability-routing decision."""

    required_capability: str
    provider: ProviderKind
    model: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Return inspectable, credential-free routing provenance."""

        return {
            "required_capability": self.required_capability,
            "provider": self.provider.value,
            "model": self.model,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProviderRoutingPolicy:
    """Explicit provider preference order for capability-routed messages."""

    provider_order: tuple[ProviderKind, ...]

    def __post_init__(self) -> None:
        if len(set(self.provider_order)) != len(self.provider_order):
            raise ValueError("provider routing policy must not contain duplicates")

    def to_dict(self) -> dict[str, list[str]]:
        """Return the operator-visible ordered preference policy."""

        return {"provider_order": [kind.value for kind in self.provider_order]}

    def resolve(
        self,
        required_capability: str,
        provider_specs: tuple[ProviderSpec, ...],
        *,
        model: str | None = None,
    ) -> ProviderRoute:
        """Choose the first configured capable provider in policy order."""

        if not required_capability.strip():
            raise ValueError("required capability must not be empty")
        specs_by_kind: dict[ProviderKind, ProviderSpec] = {}
        for spec in provider_specs:
            if spec.kind in specs_by_kind:
                raise ValueError(f"duplicate configured provider: {spec.kind.value}")
            specs_by_kind[spec.kind] = spec
        for position, kind in enumerate(self.provider_order, start=1):
            spec = specs_by_kind.get(kind)
            if spec is None or required_capability not in spec.capabilities:
                continue
            resolved_model = model or spec.model
            return ProviderRoute(
                required_capability=required_capability,
                provider=kind,
                model=resolved_model,
                reason=(
                    f"policy position {position} selected {kind.value} as the first "
                    f"configured provider declaring capability {required_capability!r}"
                ),
            )
        raise ValueError(
            "no configured provider satisfies required capability: "
            f"{required_capability}"
        )


DEFAULT_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        kind=ProviderKind.OPENAI,
        model="gpt-5.5",
        api_key_env="OPENAI_API_KEY",
        capabilities=("general", "reasoning", "tool_use", "vision"),
    ),
    ProviderSpec(
        kind=ProviderKind.ANTHROPIC,
        model="claude-sonnet-4",
        api_key_env="ANTHROPIC_API_KEY",
        capabilities=("general", "reasoning", "tool_use", "vision"),
    ),
    ProviderSpec(
        kind=ProviderKind.GOOGLE,
        model="gemini-2.5-pro",
        api_key_env="GOOGLE_API_KEY",
        capabilities=("general", "reasoning", "tool_use", "vision"),
    ),
    ProviderSpec(
        kind=ProviderKind.OPENROUTER,
        model="openrouter/auto",
        base_url=OPENROUTER_DEFAULT_BASE_URL,
        api_key_env="OPENROUTER_API_KEY",
        capabilities=("general", "tool_use"),
    ),
    ProviderSpec(
        kind=ProviderKind.LM_STUDIO,
        model="local-model",
        base_url=LM_STUDIO_DEFAULT_BASE_URL,
        capabilities=("general",),
    ),
    ProviderSpec(
        kind=ProviderKind.OLLAMA,
        model="llama3.1",
        base_url=OLLAMA_DEFAULT_BASE_URL,
        capabilities=("general",),
    ),
    ProviderSpec(kind=ProviderKind.OPENAI_COMPATIBLE, model="custom-model"),
)


DEFAULT_PROVIDER_ROUTING_POLICY = ProviderRoutingPolicy(
    tuple(spec.kind for spec in DEFAULT_PROVIDER_SPECS)
)
