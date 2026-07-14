from __future__ import annotations

import pytest

from codex_agentic_os.providers import (
    DEFAULT_PROVIDER_ROUTING_POLICY,
    DEFAULT_PROVIDER_SPECS,
    ProviderKind,
    ProviderRoutingPolicy,
    ProviderSpec,
)


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


def test_provider_routing_policy_is_ordered_inspectable_and_deterministic() -> None:
    specs = (
        ProviderSpec(
            kind=ProviderKind.OPENAI, model="openai-model", capabilities=("reasoning",)
        ),
        ProviderSpec(
            kind=ProviderKind.ANTHROPIC,
            model="anthropic-model",
            capabilities=("reasoning",),
        ),
    )
    policy = ProviderRoutingPolicy((ProviderKind.ANTHROPIC, ProviderKind.OPENAI))

    first = policy.resolve("reasoning", specs)
    repeated = policy.resolve("reasoning", specs)

    assert first == repeated
    assert first.provider is ProviderKind.ANTHROPIC
    assert first.model == "anthropic-model"
    assert first.reason == (
        "policy position 1 selected anthropic as the first configured provider "
        "declaring capability 'reasoning'"
    )
    assert policy.to_dict() == {"provider_order": ["anthropic", "openai"]}


def test_provider_routing_policy_order_and_model_override_control_resolution() -> None:
    specs = tuple(
        spec for spec in DEFAULT_PROVIDER_SPECS if spec.kind in {
            ProviderKind.OPENAI,
            ProviderKind.ANTHROPIC,
        }
    )

    openai_first = ProviderRoutingPolicy(
        (ProviderKind.OPENAI, ProviderKind.ANTHROPIC)
    ).resolve("reasoning", specs, model="operator-model")
    anthropic_first = ProviderRoutingPolicy(
        (ProviderKind.ANTHROPIC, ProviderKind.OPENAI)
    ).resolve("reasoning", specs)

    assert (openai_first.provider, openai_first.model) == (
        ProviderKind.OPENAI,
        "operator-model",
    )
    assert anthropic_first.provider is ProviderKind.ANTHROPIC


def test_provider_routing_policy_rejects_duplicate_preferences() -> None:
    with pytest.raises(ValueError, match="must not contain duplicates"):
        ProviderRoutingPolicy((ProviderKind.OPENAI, ProviderKind.OPENAI))


def test_default_provider_routing_policy_matches_registry_order() -> None:
    assert DEFAULT_PROVIDER_ROUTING_POLICY.provider_order == tuple(
        spec.kind for spec in DEFAULT_PROVIDER_SPECS
    )
