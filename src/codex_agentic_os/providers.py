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


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Configuration needed to route an agent request to a model provider."""

    kind: ProviderKind
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    supports_tools: bool = True
    supports_streaming: bool = True

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        data = asdict(self)
        data["kind"] = self.kind.value
        return data


DEFAULT_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(kind=ProviderKind.OPENAI, model="gpt-5.5", api_key_env="OPENAI_API_KEY"),
    ProviderSpec(kind=ProviderKind.ANTHROPIC, model="claude-sonnet-4", api_key_env="ANTHROPIC_API_KEY"),
    ProviderSpec(kind=ProviderKind.GOOGLE, model="gemini-2.5-pro", api_key_env="GOOGLE_API_KEY"),
    ProviderSpec(kind=ProviderKind.OPENROUTER, model="openrouter/auto", api_key_env="OPENROUTER_API_KEY"),
    ProviderSpec(kind=ProviderKind.LM_STUDIO, model="local-model", base_url="http://localhost:1234/v1"),
    ProviderSpec(kind=ProviderKind.OLLAMA, model="llama3.1", base_url="http://localhost:11434/v1"),
    ProviderSpec(kind=ProviderKind.OPENAI_COMPATIBLE, model="custom-model"),
)
