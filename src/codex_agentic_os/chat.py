"""Provider-neutral chat requests and a small OpenAI-compatible transport."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .providers import ProviderKind, ProviderSpec


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single message in a chat request."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """The provider-independent portion of a model call."""

    messages: tuple[ChatMessage, ...]
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Normalized response returned by an adapter."""

    content: str
    model: str | None = None
    raw: Mapping[str, object] | None = None


class ChatAdapter(Protocol):
    """Interface implemented by provider-specific chat transports."""

    def complete(self, request: ChatRequest) -> ChatResponse:
        """Complete a chat request."""


Transport = Callable[[str, Mapping[str, str], bytes], bytes]


def _urlopen_transport(url: str, headers: Mapping[str, str], body: bytes) -> bytes:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=120) as response:  # noqa: S310 - configured endpoint
            return response.read()
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"chat request failed: {exc}") from exc


class OpenAICompatibleAdapter:
    """Adapter for providers exposing ``POST /chat/completions``."""

    def __init__(self, spec: ProviderSpec, *, transport: Transport = _urlopen_transport) -> None:
        self.spec = spec
        self._transport = transport

    def complete(self, request: ChatRequest) -> ChatResponse:
        if not request.messages:
            raise ValueError("chat requests require at least one message")

        payload: dict[str, object] = {
            "model": self.spec.model,
            "messages": [{"role": message.role, "content": message.content} for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        base_url = (self.spec.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.spec.api_key_env:
            api_key = os.getenv(self.spec.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        raw = json.loads(self._transport(f"{base_url}/chat/completions", headers, json.dumps(payload).encode()))
        try:
            choice = raw["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("provider returned an unexpected chat response") from exc
        if not isinstance(content, str):
            raise RuntimeError("provider returned non-text chat content")
        return ChatResponse(content=content, model=raw.get("model"), raw=raw)


def adapter_for(spec: ProviderSpec, *, transport: Transport = _urlopen_transport) -> ChatAdapter:
    """Build the supported adapter for a provider specification."""

    compatible = {
        ProviderKind.OPENAI,
        ProviderKind.OPENROUTER,
        ProviderKind.LM_STUDIO,
        ProviderKind.OLLAMA,
        ProviderKind.OPENAI_COMPATIBLE,
    }
    if spec.kind in compatible:
        return OpenAICompatibleAdapter(spec, transport=transport)
    raise NotImplementedError(f"no chat adapter implemented for {spec.kind.value}")
