from __future__ import annotations

import json

import pytest

from codex_agentic_os.cli import main


class _FakeResponse:
    """Mimics the ``urlopen`` context-manager response used by ``_urlopen_transport``."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _fake_urlopen(payload: dict[str, object], captured: dict[str, object]):
    def urlopen(request, timeout=120):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        if request.data is not None:
            captured["body"] = json.loads(request.data)
        return _FakeResponse(json.dumps(payload).encode())

    return urlopen


def test_cli_chat_send_uses_provider_default_model_and_base_url(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen(
            {"model": "local-model", "choices": [{"message": {"content": "hello"}}]}, captured
        ),
    )

    main(["chat", "send", "--provider", "lm_studio", "hi"])

    assert captured["url"] == "http://localhost:1234/v1/chat/completions"
    assert captured["body"] == {"model": "local-model", "messages": [{"role": "user", "content": "hi"}]}
    payload = json.loads(capsys.readouterr().out)
    assert payload["content"] == "hello"
    assert payload["model"] == "local-model"
    assert payload["raw"]["choices"][0]["message"]["content"] == "hello"


def test_cli_chat_send_overrides_model_base_url_and_credential_env(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("CUSTOM_KEY", "secret")
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen({"choices": [{"message": {"content": "hi there"}}]}, captured),
    )

    main(
        [
            "chat", "send",
            "--provider", "openai_compatible",
            "--model", "custom-model",
            "--base-url", "https://proxy.example.com/v1",
            "--api-key-env", "CUSTOM_KEY",
            "--temperature", "0.4",
            "--max-tokens", "64",
            "hello there",
        ]
    )

    assert captured["url"] == "https://proxy.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"] == {
        "model": "custom-model",
        "messages": [{"role": "user", "content": "hello there"}],
        "temperature": 0.4,
        "max_tokens": 64,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["content"] == "hi there"


def test_cli_chat_send_anthropic_uses_native_payload_and_credential(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen(
            {"model": "claude-test", "content": [{"type": "text", "text": "hi"}]}, captured
        ),
    )

    main(["chat", "send", "--provider", "anthropic", "--model", "claude-test", "hello"])

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["X-api-key"] == "secret"
    assert captured["body"] == {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16_000,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["content"] == "hi"
    assert payload["model"] == "claude-test"


def test_cli_chat_send_google_uses_native_payload_and_credential(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("GOOGLE_API_KEY", "secret")
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen(
            {
                "modelVersion": "gemini-test-001",
                "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            },
            captured,
        ),
    )

    main(["chat", "send", "--provider", "google", "--model", "gemini-test", "hello"])

    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    )
    assert captured["headers"]["X-goog-api-key"] == "secret"
    payload = json.loads(capsys.readouterr().out)
    assert payload["content"] == "hi"
    assert payload["model"] == "gemini-test-001"


def test_cli_chat_send_system_option_orders_system_then_user_for_compatible_provider(
    monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen({"choices": [{"message": {"content": "hi there"}}]}, captured),
    )

    main(
        [
            "chat", "send",
            "--provider", "lm_studio",
            "--system", "be terse",
            "hello",
        ]
    )

    assert captured["body"]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload["content"] == "hi there"


def test_cli_chat_send_system_option_uses_native_anthropic_system_field(
    monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen(
            {"model": "claude-test", "content": [{"type": "text", "text": "hi"}]}, captured
        ),
    )

    main(
        [
            "chat", "send",
            "--provider", "anthropic",
            "--model", "claude-test",
            "--system", "be terse",
            "hello",
        ]
    )

    assert captured["body"] == {
        "model": "claude-test",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16_000,
        "system": "be terse",
    }


def test_cli_chat_send_system_option_uses_native_google_system_instruction(
    monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("GOOGLE_API_KEY", "secret")
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen(
            {
                "modelVersion": "gemini-test-001",
                "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
            },
            captured,
        ),
    )

    main(
        [
            "chat", "send",
            "--provider", "google",
            "--model", "gemini-test",
            "--system", "be terse",
            "hello",
        ]
    )

    assert captured["body"]["systemInstruction"] == {"parts": [{"text": "be terse"}]}
    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "hello"}]}
    ]


def test_cli_chat_send_omitting_system_preserves_current_payload(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "codex_agentic_os.chat.urlopen",
        _fake_urlopen({"choices": [{"message": {"content": "hello"}}]}, captured),
    )

    main(["chat", "send", "--provider", "lm_studio", "hi"])

    assert captured["body"] == {"model": "local-model", "messages": [{"role": "user", "content": "hi"}]}


def test_cli_chat_send_rejects_empty_system_before_network_call(monkeypatch, capsys) -> None:
    def urlopen(request, timeout=120):
        raise AssertionError("transport must not be invoked for an empty system instruction")

    monkeypatch.setattr("codex_agentic_os.chat.urlopen", urlopen)

    with pytest.raises(SystemExit) as exit_info:
        main(["chat", "send", "--provider", "openai", "--system", "  ", "hello"])

    assert exit_info.value.code == 2
    assert "chat system instruction must not be empty" in capsys.readouterr().err


def test_cli_chat_send_rejects_empty_message_before_network_call(monkeypatch, capsys) -> None:
    def urlopen(request, timeout=120):
        raise AssertionError("transport must not be invoked for an empty message")

    monkeypatch.setattr("codex_agentic_os.chat.urlopen", urlopen)

    with pytest.raises(SystemExit) as exit_info:
        main(["chat", "send", "--provider", "openai", " "])

    assert exit_info.value.code == 2
    assert "chat message must not be empty" in capsys.readouterr().err


def test_cli_chat_send_rejects_unknown_provider_before_network_call(monkeypatch, capsys) -> None:
    def urlopen(request, timeout=120):
        raise AssertionError("transport must not be invoked for an unknown provider")

    monkeypatch.setattr("codex_agentic_os.chat.urlopen", urlopen)

    with pytest.raises(SystemExit) as exit_info:
        main(["chat", "send", "--provider", "bogus", "hello"])

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_cli_chat_send_surfaces_adapter_error_as_clean_message(monkeypatch, capsys) -> None:
    def urlopen(request, timeout=120):
        return _FakeResponse(json.dumps({"choices": []}).encode())

    monkeypatch.setattr("codex_agentic_os.chat.urlopen", urlopen)

    with pytest.raises(SystemExit) as exit_info:
        main(["chat", "send", "--provider", "openai", "hello"])

    assert exit_info.value.code == 2
    assert "error: provider returned an unexpected chat response" in capsys.readouterr().err
