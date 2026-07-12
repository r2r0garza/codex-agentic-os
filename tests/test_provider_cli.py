from __future__ import annotations

import json

from codex_agentic_os.cli import main
from codex_agentic_os.providers import DEFAULT_PROVIDER_SPECS


def test_cli_provider_list_matches_registry_order_and_fields(capsys) -> None:
    main(["provider", "list"])

    payload = json.loads(capsys.readouterr().out)

    assert payload == [spec.to_dict() for spec in DEFAULT_PROVIDER_SPECS]
    assert [entry["kind"] for entry in payload] == [
        spec.kind.value for spec in DEFAULT_PROVIDER_SPECS
    ]
    for entry in payload:
        assert set(entry) == {
            "kind",
            "model",
            "base_url",
            "api_key_env",
            "supports_tools",
            "supports_streaming",
        }


def test_cli_provider_list_never_prints_credential_values(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "another-secret-value")

    main(["provider", "list"])

    output = capsys.readouterr().out
    assert "super-secret-value" not in output
    assert "another-secret-value" not in output


def test_cli_provider_list_performs_no_network_or_state_access(monkeypatch, capsys) -> None:
    def urlopen(request, timeout=120):
        raise AssertionError("provider list must not perform network access")

    monkeypatch.setattr("codex_agentic_os.chat.urlopen", urlopen)

    main(["provider", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == len(DEFAULT_PROVIDER_SPECS)
