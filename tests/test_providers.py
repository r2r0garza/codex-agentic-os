from __future__ import annotations

from codex_agentic_os.providers import DEFAULT_PROVIDER_SPECS, ProviderKind, ProviderSpec


def test_provider_spec_capabilities_default_to_empty_tuple() -> None:
    spec = ProviderSpec(kind=ProviderKind.OPENAI_COMPATIBLE, model="custom-model")

    assert spec.capabilities == ()
    assert spec.to_dict()["capabilities"] == []


def test_default_provider_specs_declare_capabilities_for_routing() -> None:
    by_kind = {spec.kind: spec for spec in DEFAULT_PROVIDER_SPECS}

    assert "general" in by_kind[ProviderKind.OPENAI].capabilities
    assert "reasoning" in by_kind[ProviderKind.ANTHROPIC].capabilities
    assert by_kind[ProviderKind.OPENAI_COMPATIBLE].capabilities == ()


def test_provider_spec_to_dict_reports_capabilities_as_a_list() -> None:
    spec = ProviderSpec(
        kind=ProviderKind.OLLAMA, model="llama3.1", capabilities=("general", "tool_use")
    )

    data = spec.to_dict()

    assert data["capabilities"] == ["general", "tool_use"]
    assert isinstance(data["capabilities"], list)
