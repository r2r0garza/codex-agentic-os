"""Provider-neutral chat requests and native provider transports."""

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


class AnthropicAdapter:
    """Adapter for Anthropic's native ``POST /v1/messages`` API."""

    def __init__(self, spec: ProviderSpec, *, transport: Transport = _urlopen_transport) -> None:
        self.spec = spec
        self._transport = transport

    def complete(self, request: ChatRequest) -> ChatResponse:
        if not request.messages:
            raise ValueError("chat requests require at least one message")

        system_messages = [message.content for message in request.messages if message.role == "system"]
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        if not messages:
            raise ValueError("Anthropic chat requests require at least one user or assistant message")
        if any(message["role"] not in {"user", "assistant"} for message in messages):
            raise ValueError("Anthropic chat messages must use system, user, or assistant roles")

        payload: dict[str, object] = {
            "model": self.spec.model,
            "messages": messages,
            "max_tokens": request.max_tokens if request.max_tokens is not None else 16_000,
            "cache_control": {"type": "ephemeral"},
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        base_url = (self.spec.base_url or "https://api.anthropic.com").rstrip("/")
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.spec.api_key_env:
            api_key = os.getenv(self.spec.api_key_env)
            if api_key:
                headers["x-api-key"] = api_key

        raw = json.loads(self._transport(f"{base_url}/v1/messages", headers, json.dumps(payload).encode()))
        try:
            content = "".join(block["text"] for block in raw["content"] if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise RuntimeError("provider returned an unexpected chat response") from exc
        if not content:
            raise RuntimeError("provider returned no text chat content")
        return ChatResponse(content=content, model=raw.get("model"), raw=raw)


class GoogleAdapter:
    """Adapter for Google's native ``models.generateContent`` API."""

    def __init__(self, spec: ProviderSpec, *, transport: Transport = _urlopen_transport) -> None:
        self.spec = spec
        self._transport = transport

    def complete(self, request: ChatRequest) -> ChatResponse:
        if not request.messages:
            raise ValueError("chat requests require at least one message")

        system_messages = [message.content for message in request.messages if message.role == "system"]
        messages = [message for message in request.messages if message.role != "system"]
        if not messages:
            raise ValueError("Google chat requests require at least one user or assistant message")
        if any(message.role not in {"user", "assistant"} for message in messages):
            raise ValueError("Google chat messages must use system, user, or assistant roles")

        payload: dict[str, object] = {
            "contents": [
                {
                    "role": "model" if message.role == "assistant" else "user",
                    "parts": [{"text": message.content}],
                }
                for message in messages
            ]
        }
        if system_messages:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_messages)}]
            }
        generation_config: dict[str, object] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if generation_config:
            payload["generationConfig"] = generation_config

        base_url = (self.spec.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        model = self.spec.model.removeprefix("models/")
        headers = {"Content-Type": "application/json"}
        if self.spec.api_key_env:
            api_key = os.getenv(self.spec.api_key_env)
            if api_key:
                headers["x-goog-api-key"] = api_key

        raw = json.loads(
            self._transport(
                f"{base_url}/models/{model}:generateContent",
                headers,
                json.dumps(payload).encode(),
            )
        )
        try:
            parts = raw["candidates"][0]["content"]["parts"]
            content = "".join(part["text"] for part in parts if "text" in part)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("provider returned an unexpected chat response") from exc
        if not content:
            raise RuntimeError("provider returned no text chat content")
        return ChatResponse(content=content, model=raw.get("modelVersion"), raw=raw)


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
    if spec.kind is ProviderKind.ANTHROPIC:
        return AnthropicAdapter(spec, transport=transport)
    if spec.kind is ProviderKind.GOOGLE:
        return GoogleAdapter(spec, transport=transport)
    raise NotImplementedError(f"no chat adapter implemented for {spec.kind.value}")
