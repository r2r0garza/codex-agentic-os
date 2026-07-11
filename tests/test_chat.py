import json

import pytest

from codex_agentic_os.chat import (
    AnthropicAdapter,
    ChatMessage,
    ChatRequest,
    GoogleAdapter,
    OpenAICompatibleAdapter,
    adapter_for,
)
from codex_agentic_os.providers import ProviderKind, ProviderSpec


def test_compatible_adapter_posts_normalized_payload_and_reads_response() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers, body=json.loads(body))
        return json.dumps({"model": "test-model", "choices": [{"message": {"content": "hello"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OPENAI_COMPATIBLE, model="test-model", base_url="http://localhost:9000/v1"),
        transport=transport,
    )
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),), temperature=0.2, max_tokens=12))

    assert captured["url"] == "http://localhost:9000/v1/chat/completions"
    assert captured["body"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "max_tokens": 12,
    }
    assert response.content == "hello"


def test_anthropic_adapter_posts_native_payload_and_reads_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers, body=json.loads(body))
        return json.dumps(
            {"model": "claude-test", "content": [{"type": "text", "text": "hello"}]}
        ).encode()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    adapter = AnthropicAdapter(
        ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test", api_key_env="ANTHROPIC_API_KEY"),
        transport=transport,
    )
    response = adapter.complete(
        ChatRequest(
            (ChatMessage("system", "Be concise."), ChatMessage("user", "Hi")),
            temperature=0.2,
            max_tokens=24,
        )
    )

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
        "x-api-key": "secret",
    }
    assert captured["body"] == {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "Hi"}],
        "system": "Be concise.",
        "max_tokens": 24,
        "temperature": 0.2,
        "cache_control": {"type": "ephemeral"},
    }
    assert response.content == "hello"
    assert response.model == "claude-test"


def test_google_adapter_posts_native_payload_and_reads_text_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers, body=json.loads(body))
        return json.dumps(
            {
                "modelVersion": "gemini-test-001",
                "candidates": [
                    {"content": {"parts": [{"text": "hello"}, {"text": " there"}]}}
                ],
            }
        ).encode()

    monkeypatch.setenv("GOOGLE_API_KEY", "secret")
    adapter = GoogleAdapter(
        ProviderSpec(ProviderKind.GOOGLE, model="gemini-test", api_key_env="GOOGLE_API_KEY"),
        transport=transport,
    )
    response = adapter.complete(
        ChatRequest(
            (
                ChatMessage("system", "Be concise."),
                ChatMessage("user", "Hi"),
                ChatMessage("assistant", "Hello"),
                ChatMessage("user", "Continue"),
            ),
            temperature=0.2,
            max_tokens=24,
        )
    )

    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    )
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "x-goog-api-key": "secret",
    }
    assert captured["body"] == {
        "contents": [
            {"role": "user", "parts": [{"text": "Hi"}]},
            {"role": "model", "parts": [{"text": "Hello"}]},
            {"role": "user", "parts": [{"text": "Continue"}]},
        ],
        "systemInstruction": {"parts": [{"text": "Be concise."}]},
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 24},
    }
    assert response.content == "hello there"
    assert response.model == "gemini-test-001"


def test_adapter_factory_selects_native_adapters() -> None:
    assert isinstance(adapter_for(ProviderSpec(ProviderKind.ANTHROPIC, model="claude")), AnthropicAdapter)
    assert isinstance(adapter_for(ProviderSpec(ProviderKind.GOOGLE, model="gemini")), GoogleAdapter)


def test_empty_chat_is_rejected_before_transport() -> None:
    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=lambda *_: b"{}")
    with pytest.raises(ValueError, match="at least one message"):
        adapter.complete(ChatRequest(()))


def test_anthropic_requires_a_non_system_message() -> None:
    adapter = AnthropicAdapter(
        ProviderSpec(ProviderKind.ANTHROPIC, model="claude"), transport=lambda *_: b"{}"
    )
    with pytest.raises(ValueError, match="user or assistant"):
        adapter.complete(ChatRequest((ChatMessage("system", "instructions"),)))


def test_google_requires_a_non_system_message() -> None:
    adapter = GoogleAdapter(
        ProviderSpec(ProviderKind.GOOGLE, model="gemini"), transport=lambda *_: b"{}"
    )
    with pytest.raises(ValueError, match="user or assistant"):
        adapter.complete(ChatRequest((ChatMessage("system", "instructions"),)))
