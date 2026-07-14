"""Provider-neutral chat requests and native provider transports."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .providers import (
    LM_STUDIO_DEFAULT_BASE_URL,
    OLLAMA_DEFAULT_BASE_URL,
    OPENROUTER_DEFAULT_BASE_URL,
    ProviderKind,
    ProviderSpec,
)

_COMPATIBLE_DEFAULT_BASE_URLS: Mapping[ProviderKind, str] = {
    ProviderKind.OPENROUTER: OPENROUTER_DEFAULT_BASE_URL,
    ProviderKind.LM_STUDIO: LM_STUDIO_DEFAULT_BASE_URL,
    ProviderKind.OLLAMA: OLLAMA_DEFAULT_BASE_URL,
}


@dataclass(frozen=True, slots=True)
class ChatToolCall:
    """A model's normalized request to invoke one declared tool by name."""

    name: str
    arguments: Mapping[str, object]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single message in a chat request.

    ``tool_call`` marks an assistant turn that requested a tool; only valid
    when ``role`` is ``"assistant"``. ``tool_result_for`` marks a turn
    answering that call (by its ``call_id``, or its tool name when the
    provider has no call id), with ``content`` carrying the serialized
    result; only valid when ``role`` is ``"tool"``.
    """

    role: str
    content: str
    tool_call: ChatToolCall | None = None
    tool_result_for: str | None = None


@dataclass(frozen=True, slots=True)
class ChatToolDeclaration:
    """Command-free tool metadata exposed to a model provider."""

    name: str
    description: str | None = None
    parameters: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """The provider-independent portion of a model call."""

    messages: tuple[ChatMessage, ...]
    temperature: float | None = None
    max_tokens: int | None = None
    tools: tuple[ChatToolDeclaration, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatUsage:
    """Normalized token-usage evidence for a completed chat request."""

    available: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: Mapping[str, object] | None = None
    unavailable_reason: str | None = None


def _unavailable_usage(
    reason: str = "provider response did not include a usage block",
) -> ChatUsage:
    return ChatUsage(available=False, unavailable_reason=reason)


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Normalized response returned by an adapter."""

    content: str
    model: str | None = None
    raw: Mapping[str, object] | None = None
    usage: ChatUsage = field(default_factory=_unavailable_usage)
    tool_call: ChatToolCall | None = None


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


def _openai_compatible_usage(raw: Mapping[str, object]) -> ChatUsage:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return _unavailable_usage()
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return _unavailable_usage(
            "provider usage block did not include prompt_tokens/completion_tokens counts"
        )
    return ChatUsage(available=True, input_tokens=input_tokens, output_tokens=output_tokens, raw=dict(usage))


def _anthropic_usage(raw: Mapping[str, object]) -> ChatUsage:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return _unavailable_usage()
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return _unavailable_usage(
            "provider usage block did not include input_tokens/output_tokens counts"
        )
    return ChatUsage(available=True, input_tokens=input_tokens, output_tokens=output_tokens, raw=dict(usage))


def _google_usage(raw: Mapping[str, object]) -> ChatUsage:
    usage = raw.get("usageMetadata")
    if not isinstance(usage, Mapping):
        return _unavailable_usage()
    input_tokens = usage.get("promptTokenCount")
    output_tokens = usage.get("candidatesTokenCount")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return _unavailable_usage(
            "provider usage block did not include promptTokenCount/candidatesTokenCount counts"
        )
    return ChatUsage(available=True, input_tokens=input_tokens, output_tokens=output_tokens, raw=dict(usage))


def _tool_parameters(tool: ChatToolDeclaration) -> dict[str, object]:
    """Return a deterministic object-input schema common to all adapters."""

    parameters = (
        {"type": "object", "properties": {}}
        if tool.parameters is None
        else dict(tool.parameters)
    )
    schema_type = parameters.get("type")
    if schema_type is not None and schema_type != "object":
        raise ValueError(
            f"tool {tool.name!r} parameters must describe an object for provider mapping"
        )
    properties = parameters.get("properties")
    if properties is not None and not isinstance(properties, Mapping):
        raise ValueError(
            f"tool {tool.name!r} parameters properties must be a JSON object"
        )
    return parameters


def _openai_tools(tools: tuple[ChatToolDeclaration, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for tool in tools:
        function: dict[str, object] = {
            "name": tool.name,
            "parameters": _tool_parameters(tool),
        }
        if tool.description is not None:
            function["description"] = tool.description
        result.append({"type": "function", "function": function})
    return result


def _anthropic_tools(tools: tuple[ChatToolDeclaration, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for tool in tools:
        mapped: dict[str, object] = {
            "name": tool.name,
            "input_schema": _tool_parameters(tool),
        }
        if tool.description is not None:
            mapped["description"] = tool.description
        result.append(mapped)
    return result


def _google_tools(tools: tuple[ChatToolDeclaration, ...]) -> list[dict[str, object]]:
    declarations: list[dict[str, object]] = []
    for tool in tools:
        mapped: dict[str, object] = {
            "name": tool.name,
            "parameters": _tool_parameters(tool),
        }
        if tool.description is not None:
            mapped["description"] = tool.description
        declarations.append(mapped)
    return [{"functionDeclarations": declarations}]


def _openai_message(message: ChatMessage) -> dict[str, object]:
    if message.tool_call is not None:
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": message.tool_call.call_id or message.tool_call.name,
                    "type": "function",
                    "function": {
                        "name": message.tool_call.name,
                        "arguments": json.dumps(dict(message.tool_call.arguments)),
                    },
                }
            ],
        }
    if message.tool_result_for is not None:
        return {
            "role": "tool",
            "tool_call_id": message.tool_result_for,
            "content": message.content,
        }
    return {"role": message.role, "content": message.content}


def _openai_tool_call(message: Mapping[str, object]) -> ChatToolCall | None:
    tool_calls = message.get("tool_calls")
    if not tool_calls:
        return None
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise RuntimeError("provider returned an unsupported number of tool calls")
    try:
        function = tool_calls[0]["function"]
        name = function["name"]
        arguments_raw = function.get("arguments", "{}")
    except (KeyError, TypeError) as exc:
        raise RuntimeError("provider returned an unexpected tool call") from exc
    if not isinstance(name, str):
        raise RuntimeError("provider returned an unexpected tool call")
    try:
        arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw
    except json.JSONDecodeError as exc:
        raise RuntimeError("provider returned invalid tool call arguments") from exc
    if not isinstance(arguments, dict):
        raise RuntimeError("provider tool call arguments must be a JSON object")
    call_id = tool_calls[0].get("id")
    return ChatToolCall(name=name, arguments=arguments, call_id=call_id)


def _anthropic_message(message: ChatMessage) -> dict[str, object]:
    if message.tool_call is not None:
        return {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": message.tool_call.call_id or message.tool_call.name,
                    "name": message.tool_call.name,
                    "input": dict(message.tool_call.arguments),
                }
            ],
        }
    if message.tool_result_for is not None:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_result_for,
                    "content": message.content,
                }
            ],
        }
    return {"role": message.role, "content": message.content}


def _anthropic_tool_call(blocks: Sequence[object]) -> ChatToolCall | None:
    tool_use_blocks = [block for block in blocks if isinstance(block, Mapping) and block.get("type") == "tool_use"]
    if not tool_use_blocks:
        return None
    if len(tool_use_blocks) != 1:
        raise RuntimeError("provider returned an unsupported number of tool calls")
    block = tool_use_blocks[0]
    name = block.get("name")
    arguments = block.get("input")
    if not isinstance(name, str) or not isinstance(arguments, Mapping):
        raise RuntimeError("provider returned an unexpected tool call")
    return ChatToolCall(name=name, arguments=dict(arguments), call_id=block.get("id"))


def _google_content(message: ChatMessage) -> dict[str, object]:
    if message.tool_call is not None:
        return {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": message.tool_call.name,
                        "args": dict(message.tool_call.arguments),
                    }
                }
            ],
        }
    if message.tool_result_for is not None:
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": message.tool_result_for,
                        "response": {"content": message.content},
                    }
                }
            ],
        }
    return {
        "role": "model" if message.role == "assistant" else "user",
        "parts": [{"text": message.content}],
    }


def _google_tool_call(parts: Sequence[object]) -> ChatToolCall | None:
    calls = [part["functionCall"] for part in parts if isinstance(part, Mapping) and "functionCall" in part]
    if not calls:
        return None
    if len(calls) != 1:
        raise RuntimeError("provider returned an unsupported number of tool calls")
    call = calls[0]
    name = call.get("name") if isinstance(call, Mapping) else None
    arguments = call.get("args", {}) if isinstance(call, Mapping) else None
    if not isinstance(name, str) or not isinstance(arguments, Mapping):
        raise RuntimeError("provider returned an unexpected tool call")
    call_id = call.get("id") if isinstance(call, Mapping) else None
    return ChatToolCall(name=name, arguments=dict(arguments), call_id=call_id)


class OpenAICompatibleAdapter:
    """Adapter for providers exposing ``POST /chat/completions``."""

    def __init__(self, spec: ProviderSpec, *, transport: Transport = _urlopen_transport) -> None:
        self.spec = spec
        self._transport = transport

    def complete(self, request: ChatRequest) -> ChatResponse:
        if not request.messages:
            raise ValueError("chat requests require at least one message")
        if self.spec.kind is ProviderKind.OPENAI_COMPATIBLE and not self.spec.base_url:
            raise ValueError(
                "openai_compatible provider configuration requires an explicit base_url; "
                "it is never defaulted to the public OpenAI endpoint"
            )

        payload: dict[str, object] = {
            "model": self.spec.model,
            "messages": [_openai_message(message) for message in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            payload["tools"] = _openai_tools(request.tools)

        provider_default = _COMPATIBLE_DEFAULT_BASE_URLS.get(self.spec.kind)
        base_url = (self.spec.base_url or provider_default or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.spec.api_key_env:
            api_key = os.getenv(self.spec.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        raw = json.loads(self._transport(f"{base_url}/chat/completions", headers, json.dumps(payload).encode()))
        try:
            choice = raw["choices"][0]
            message = choice["message"]
            content = message.get("content")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("provider returned an unexpected chat response") from exc
        tool_call = _openai_tool_call(message)
        if tool_call is None:
            if not isinstance(content, str):
                raise RuntimeError("provider returned non-text chat content")
        elif not isinstance(content, str):
            content = ""
        return ChatResponse(
            content=content,
            model=raw.get("model"),
            raw=raw,
            usage=_openai_compatible_usage(raw),
            tool_call=tool_call,
        )


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
            _anthropic_message(message)
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
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = _anthropic_tools(request.tools)

        base_url = (self.spec.base_url or "https://api.anthropic.com").rstrip("/")
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.spec.api_key_env:
            api_key = os.getenv(self.spec.api_key_env)
            if api_key:
                headers["x-api-key"] = api_key

        raw = json.loads(self._transport(f"{base_url}/v1/messages", headers, json.dumps(payload).encode()))
        try:
            blocks = raw["content"]
            content = "".join(block["text"] for block in blocks if block.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise RuntimeError("provider returned an unexpected chat response") from exc
        tool_call = _anthropic_tool_call(blocks)
        if tool_call is None and not content:
            raise RuntimeError("provider returned no text chat content")
        return ChatResponse(
            content=content,
            model=raw.get("model"),
            raw=raw,
            usage=_anthropic_usage(raw),
            tool_call=tool_call,
        )


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
        if any(message.role not in {"user", "assistant", "tool"} for message in messages):
            raise ValueError("Google chat messages must use system, user, or assistant roles")

        payload: dict[str, object] = {
            "contents": [_google_content(message) for message in messages]
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
        if request.tools:
            payload["tools"] = _google_tools(request.tools)

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
        tool_call = _google_tool_call(parts)
        if tool_call is None and not content:
            raise RuntimeError("provider returned no text chat content")
        return ChatResponse(
            content=content,
            model=raw.get("modelVersion"),
            raw=raw,
            usage=_google_usage(raw),
            tool_call=tool_call,
        )


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
