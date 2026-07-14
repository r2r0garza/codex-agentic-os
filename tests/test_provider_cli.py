from __future__ import annotations

import json

from codex_agentic_os.cli import main
from codex_agentic_os.providers import (
    DEFAULT_PROVIDER_ROUTING_POLICY,
    DEFAULT_PROVIDER_SPECS,
)


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
            "capabilities",
        }
    assert [entry["capabilities"] for entry in payload] == [
        list(spec.capabilities) for spec in DEFAULT_PROVIDER_SPECS
    ]
    assert any(entry["capabilities"] for entry in payload)


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


def test_cli_provider_routing_policy_reports_order_without_accessing_secrets(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")

    main([
        "provider",
        "routing-policy",
        "--provider-preference",
        "anthropic",
        "--provider-preference",
        "openai",
    ])

    output = capsys.readouterr().out
    assert json.loads(output) == {"provider_order": ["anthropic", "openai"]}
    assert "super-secret-value" not in output


def test_cli_provider_routing_policy_defaults_to_registry_order(capsys) -> None:
    main(["provider", "routing-policy"])

    assert json.loads(capsys.readouterr().out) == (
        DEFAULT_PROVIDER_ROUTING_POLICY.to_dict()
    )


def test_cli_provider_credentials_reports_ordered_readiness(monkeypatch, capsys) -> None:
    for spec in DEFAULT_PROVIDER_SPECS:
        if spec.api_key_env:
            monkeypatch.delenv(spec.api_key_env, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "configured-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    main(["provider", "credentials"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert [entry["kind"] for entry in payload] == [
        spec.kind.value for spec in DEFAULT_PROVIDER_SPECS
    ]
    assert payload == [
        {
            "kind": spec.kind.value,
            "api_key_env": spec.api_key_env,
            "configured": (
                spec.api_key_env is None or spec.api_key_env == "OPENAI_API_KEY"
            ),
        }
        for spec in DEFAULT_PROVIDER_SPECS
    ]
    assert "configured-secret" not in output


def test_cli_provider_credentials_performs_no_network_or_state_access(
    monkeypatch, capsys
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("provider credentials must not access network or state")

    monkeypatch.setattr("codex_agentic_os.chat.urlopen", forbidden)
    monkeypatch.setattr("codex_agentic_os.cli.StateStore", forbidden)

    main(["provider", "credentials"])

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == len(DEFAULT_PROVIDER_SPECS)
