import json

import pytest

from codex_agentic_os.chat import (
    AnthropicAdapter,
    ChatMessage,
    ChatRequest,
    ChatToolCall,
    ChatToolDeclaration,
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


def test_compatible_adapter_omits_authorization_header_without_credentials() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.LM_STUDIO, model="local-model"),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["headers"] == {"Content-Type": "application/json"}


def test_openai_compatible_requires_explicit_base_url_before_transport() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        raise AssertionError("transport must not be invoked without an explicit base_url")

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OPENAI_COMPATIBLE, model="custom-model"),
        transport=transport,
    )
    with pytest.raises(ValueError, match="explicit base_url"):
        adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))


@pytest.mark.parametrize(
    ("base_url", "credential", "expected_url", "expected_authorization"),
    [
        (None, None, "https://openrouter.ai/api/v1/chat/completions", None),
        (None, "", "https://openrouter.ai/api/v1/chat/completions", None),
        (None, "secret", "https://openrouter.ai/api/v1/chat/completions", "Bearer secret"),
        ("https://router.example/v1/", "secret", "https://router.example/v1/chat/completions", "Bearer secret"),
    ],
)
def test_openrouter_endpoint_and_optional_credential_policy(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str | None,
    credential: str | None,
    expected_url: str,
    expected_authorization: str | None,
) -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    if credential is not None:
        monkeypatch.setenv("OPENROUTER_API_KEY", credential)
    adapter = OpenAICompatibleAdapter(
        ProviderSpec(
            ProviderKind.OPENROUTER,
            model="openrouter/auto",
            base_url=base_url,
            api_key_env="OPENROUTER_API_KEY",
        ),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["url"] == expected_url
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers.get("Authorization") == expected_authorization


def test_openai_keeps_public_openai_default_endpoint() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["url"] = url
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=transport
    ).complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"


def test_lm_studio_defaults_to_standard_local_base_url_without_credentials() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.LM_STUDIO, model="local-model"),
        transport=transport,
    )
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["url"] == "http://localhost:1234/v1/chat/completions"
    assert captured["headers"] == {"Content-Type": "application/json"}
    assert response.content == "hello"


def test_lm_studio_adds_bearer_header_when_credential_env_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    monkeypatch.setenv("LM_STUDIO_API_KEY", "secret")
    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.LM_STUDIO, model="local-model", api_key_env="LM_STUDIO_API_KEY"),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }


def test_ollama_defaults_to_standard_local_base_url_without_credentials() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OLLAMA, model="llama3.1"),
        transport=transport,
    )
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["headers"] == {"Content-Type": "application/json"}
    assert response.content == "hello"


def test_ollama_adds_bearer_header_when_credential_env_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    monkeypatch.setenv("OLLAMA_API_KEY", "secret")
    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OLLAMA, model="llama3.1", api_key_env="OLLAMA_API_KEY"),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }


def test_openai_compatible_uses_explicit_base_url_and_credential_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(url=url, headers=headers)
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    monkeypatch.setenv("CUSTOM_API_KEY", "secret")
    adapter = OpenAICompatibleAdapter(
        ProviderSpec(
            ProviderKind.OPENAI_COMPATIBLE,
            model="custom-model",
            base_url="https://proxy.example.com/v1",
            api_key_env="CUSTOM_API_KEY",
        ),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert captured["url"] == "https://proxy.example.com/v1/chat/completions"
    assert captured["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }


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
    }
    assert "cache_control" not in captured["body"]
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


_CHAT_TOOLS = (
    ChatToolDeclaration(
        name="search_notes",
        description="Search durable notes",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    ChatToolDeclaration(name="list_notes"),
)


def test_openai_compatible_adapter_maps_native_function_tools() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(headers=headers, body=json.loads(body))
        return json.dumps({"choices": [{"message": {"content": "final"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(
            ProviderKind.OPENAI_COMPATIBLE,
            model="test-model",
            base_url="http://localhost:9000/v1",
        ),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))

    assert captured["headers"] == {"Content-Type": "application/json"}
    assert captured["body"]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_notes",
                "description": "Search durable notes",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_notes",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]


def test_anthropic_adapter_maps_native_tools() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(headers=headers, body=json.loads(body))
        return json.dumps({"content": [{"type": "text", "text": "final"}]}).encode()

    adapter = AnthropicAdapter(
        ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))

    assert captured["headers"] == {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    assert captured["body"]["tools"] == [
        {
            "name": "search_notes",
            "description": "Search durable notes",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "list_notes",
            "input_schema": {"type": "object", "properties": {}},
        },
    ]


def test_google_adapter_maps_native_function_declarations() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured.update(headers=headers, body=json.loads(body))
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "final"}]}}]}
        ).encode()

    adapter = GoogleAdapter(
        ProviderSpec(ProviderKind.GOOGLE, model="gemini-test"), transport=transport
    )
    adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))

    assert captured["headers"] == {"Content-Type": "application/json"}
    assert captured["body"]["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "search_notes",
                    "description": "Search durable notes",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                {
                    "name": "list_notes",
                    "parameters": {"type": "object", "properties": {}},
                },
            ]
        }
    ]


@pytest.mark.parametrize(
    "adapter",
    [
        OpenAICompatibleAdapter(
            ProviderSpec(
                ProviderKind.OPENAI_COMPATIBLE,
                model="test-model",
                base_url="http://localhost:9000/v1",
            ),
            transport=lambda *_: b"{}",
        ),
        AnthropicAdapter(
            ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"),
            transport=lambda *_: b"{}",
        ),
        GoogleAdapter(
            ProviderSpec(ProviderKind.GOOGLE, model="gemini-test"),
            transport=lambda *_: b"{}",
        ),
    ],
)
def test_adapters_reject_non_object_tool_schema_before_transport(adapter) -> None:
    with pytest.raises(ValueError, match="must describe an object"):
        adapter.complete(
            ChatRequest(
                (ChatMessage("user", "Search"),),
                tools=(
                    ChatToolDeclaration(
                        name="search_notes", parameters={"type": "array"}
                    ),
                ),
            )
        )


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


def test_compatible_adapter_normalizes_usage_block() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }
        ).encode()

    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert response.usage.available is True
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 5
    assert response.usage.raw == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}
    assert response.usage.unavailable_reason is None


def test_compatible_adapter_marks_usage_unavailable_without_usage_block() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert response.usage.available is False
    assert response.usage.input_tokens is None
    assert response.usage.output_tokens is None
    assert response.usage.raw is None
    assert response.usage.unavailable_reason is not None
    assert response.content == "hello"


def test_anthropic_adapter_normalizes_usage_block(monkeypatch: pytest.MonkeyPatch) -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "model": "claude-test",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 8, "output_tokens": 3},
            }
        ).encode()

    adapter = AnthropicAdapter(ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert response.usage.available is True
    assert response.usage.input_tokens == 8
    assert response.usage.output_tokens == 3
    assert response.usage.raw == {"input_tokens": 8, "output_tokens": 3}
    assert response.usage.unavailable_reason is None


def test_anthropic_adapter_marks_usage_unavailable_without_usage_block() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps({"content": [{"type": "text", "text": "hello"}]}).encode()

    adapter = AnthropicAdapter(ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert response.usage.available is False
    assert response.usage.unavailable_reason is not None
    assert response.content == "hello"


def test_google_adapter_normalizes_usage_metadata() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "candidates": [{"content": {"parts": [{"text": "hello"}]}}],
                "usageMetadata": {
                    "promptTokenCount": 20,
                    "candidatesTokenCount": 7,
                    "totalTokenCount": 27,
                },
            }
        ).encode()

    adapter = GoogleAdapter(ProviderSpec(ProviderKind.GOOGLE, model="gemini"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert response.usage.available is True
    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 7
    assert response.usage.raw == {
        "promptTokenCount": 20,
        "candidatesTokenCount": 7,
        "totalTokenCount": 27,
    }
    assert response.usage.unavailable_reason is None


def test_google_adapter_marks_usage_unavailable_without_usage_metadata() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}).encode()

    adapter = GoogleAdapter(ProviderSpec(ProviderKind.GOOGLE, model="gemini"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "hi"),)))

    assert response.usage.available is False
    assert response.usage.unavailable_reason is not None
    assert response.content == "hello"


_RESOLVED_CONTEXT_MESSAGES = (
    ChatMessage("system", "Be concise."),
    ChatMessage("user", "Earlier objective"),
    ChatMessage("assistant", "Earlier result"),
    ChatMessage("user", "Current objective"),
)


def test_compatible_adapter_orders_resolved_context_before_current_user_message() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "final"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OPENAI_COMPATIBLE, model="test-model", base_url="http://localhost:9000/v1"),
        transport=transport,
    )
    adapter.complete(ChatRequest(_RESOLVED_CONTEXT_MESSAGES))

    assert captured["body"]["messages"] == [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Earlier objective"},
        {"role": "assistant", "content": "Earlier result"},
        {"role": "user", "content": "Current objective"},
    ]


def test_compatible_adapter_without_context_keeps_single_turn_payload() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "final"}}]}).encode()

    adapter = OpenAICompatibleAdapter(
        ProviderSpec(ProviderKind.OPENAI_COMPATIBLE, model="test-model", base_url="http://localhost:9000/v1"),
        transport=transport,
    )
    adapter.complete(ChatRequest((ChatMessage("user", "Current objective"),)))

    assert captured["body"]["messages"] == [{"role": "user", "content": "Current objective"}]


def test_anthropic_adapter_maps_resolved_context_into_alternating_native_messages() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps({"content": [{"type": "text", "text": "final"}]}).encode()

    adapter = AnthropicAdapter(ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"), transport=transport)
    adapter.complete(ChatRequest(_RESOLVED_CONTEXT_MESSAGES))

    assert captured["body"]["system"] == "Be concise."
    assert captured["body"]["messages"] == [
        {"role": "user", "content": "Earlier objective"},
        {"role": "assistant", "content": "Earlier result"},
        {"role": "user", "content": "Current objective"},
    ]


def test_google_adapter_maps_resolved_context_into_ordered_native_contents() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "final"}]}}]}
        ).encode()

    adapter = GoogleAdapter(ProviderSpec(ProviderKind.GOOGLE, model="gemini-test"), transport=transport)
    adapter.complete(ChatRequest(_RESOLVED_CONTEXT_MESSAGES))

    assert captured["body"]["systemInstruction"] == {"parts": [{"text": "Be concise."}]}
    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "Earlier objective"}]},
        {"role": "model", "parts": [{"text": "Earlier result"}]},
        {"role": "user", "parts": [{"text": "Current objective"}]},
    ]


def test_openai_compatible_adapter_parses_tool_call_response() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search_notes",
                                        "arguments": '{"query": "release"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        ).encode()

    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))

    assert response.content == ""
    assert response.tool_call == ChatToolCall(
        name="search_notes", arguments={"query": "release"}, call_id="call_1"
    )


def test_openai_compatible_adapter_maps_tool_call_and_result_followup_turns() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "final"}}]}).encode()

    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=transport)
    tool_call = ChatToolCall(name="search_notes", arguments={"query": "release"}, call_id="call_1")
    adapter.complete(
        ChatRequest(
            (
                ChatMessage("user", "Search"),
                ChatMessage("assistant", "", tool_call=tool_call),
                ChatMessage("tool", '{"exit_code": 0}', tool_result_for="call_1"),
            ),
            tools=_CHAT_TOOLS,
        )
    )

    assert captured["body"]["messages"] == [
        {"role": "user", "content": "Search"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search_notes", "arguments": '{"query": "release"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"exit_code": 0}'},
    ]


def test_openai_compatible_adapter_rejects_multiple_tool_calls() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"id": "1", "function": {"name": "a", "arguments": "{}"}},
                                {"id": "2", "function": {"name": "b", "arguments": "{}"}},
                            ]
                        }
                    }
                ]
            }
        ).encode()

    adapter = OpenAICompatibleAdapter(ProviderSpec(ProviderKind.OPENAI, model="gpt"), transport=transport)
    with pytest.raises(RuntimeError, match="unsupported number of tool calls"):
        adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))


def test_anthropic_adapter_parses_tool_call_response() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "search_notes",
                        "input": {"query": "release"},
                    }
                ]
            }
        ).encode()

    adapter = AnthropicAdapter(ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))

    assert response.content == ""
    assert response.tool_call == ChatToolCall(
        name="search_notes", arguments={"query": "release"}, call_id="toolu_1"
    )


def test_anthropic_adapter_maps_tool_call_and_result_followup_turns() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps({"content": [{"type": "text", "text": "final"}]}).encode()

    adapter = AnthropicAdapter(ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"), transport=transport)
    tool_call = ChatToolCall(name="search_notes", arguments={"query": "release"}, call_id="toolu_1")
    adapter.complete(
        ChatRequest(
            (
                ChatMessage("user", "Search"),
                ChatMessage("assistant", "", tool_call=tool_call),
                ChatMessage("tool", '{"exit_code": 0}', tool_result_for="toolu_1"),
            ),
            tools=_CHAT_TOOLS,
        )
    )

    assert captured["body"]["messages"] == [
        {"role": "user", "content": "Search"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "search_notes", "input": {"query": "release"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"exit_code": 0}'}
            ],
        },
    ]


def test_anthropic_adapter_rejects_multiple_tool_calls() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "content": [
                    {"type": "tool_use", "id": "1", "name": "a", "input": {}},
                    {"type": "tool_use", "id": "2", "name": "b", "input": {}},
                ]
            }
        ).encode()

    adapter = AnthropicAdapter(ProviderSpec(ProviderKind.ANTHROPIC, model="claude-test"), transport=transport)
    with pytest.raises(RuntimeError, match="unsupported number of tool calls"):
        adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))


def test_google_adapter_parses_tool_call_response() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "search_notes", "args": {"query": "release"}}}
                            ]
                        }
                    }
                ]
            }
        ).encode()

    adapter = GoogleAdapter(ProviderSpec(ProviderKind.GOOGLE, model="gemini-test"), transport=transport)
    response = adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))

    assert response.content == ""
    assert response.tool_call == ChatToolCall(
        name="search_notes", arguments={"query": "release"}, call_id=None
    )


def test_google_adapter_maps_tool_call_and_result_followup_turns() -> None:
    captured: dict[str, object] = {}

    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        captured["body"] = json.loads(body)
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "final"}]}}]}
        ).encode()

    adapter = GoogleAdapter(ProviderSpec(ProviderKind.GOOGLE, model="gemini-test"), transport=transport)
    tool_call = ChatToolCall(name="search_notes", arguments={"query": "release"}, call_id=None)
    adapter.complete(
        ChatRequest(
            (
                ChatMessage("user", "Search"),
                ChatMessage("assistant", "", tool_call=tool_call),
                ChatMessage("tool", '{"exit_code": 0}', tool_result_for="search_notes"),
            ),
            tools=_CHAT_TOOLS,
        )
    )

    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "Search"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "search_notes", "args": {"query": "release"}}}]},
        {
            "role": "user",
            "parts": [
                {"functionResponse": {"name": "search_notes", "response": {"content": '{"exit_code": 0}'}}}
            ],
        },
    ]


def test_google_adapter_rejects_multiple_tool_calls() -> None:
    def transport(url: str, headers: dict[str, str], body: bytes) -> bytes:
        return json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": "a", "args": {}}},
                                {"functionCall": {"name": "b", "args": {}}},
                            ]
                        }
                    }
                ]
            }
        ).encode()

    adapter = GoogleAdapter(ProviderSpec(ProviderKind.GOOGLE, model="gemini-test"), transport=transport)
    with pytest.raises(RuntimeError, match="unsupported number of tool calls"):
        adapter.complete(ChatRequest((ChatMessage("user", "Search"),), tools=_CHAT_TOOLS))
