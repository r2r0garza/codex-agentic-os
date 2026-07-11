import json

import pytest

from codex_agentic_os.chat import ChatMessage, ChatRequest, OpenAICompatibleAdapter, adapter_for
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


def test_adapter_factory_rejects_unimplemented_native_protocols() -> None:
    with pytest.raises(NotImplementedError):
        adapter_for(ProviderSpec(ProviderKind.ANTHROPIC, model="claude"))


def test_empty_chat_is_rejected_before_transport() -> None:
    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=lambda *_: b"{}")
    with pytest.raises(ValueError, match="at least one message"):
        adapter.complete(ChatRequest(()))
