"""Тесты трансляции профилей в model_list LiteLLM (спец. §5.1)."""
from __future__ import annotations

from app.models.enums import ProviderKind
from app.models.provider import Provider
from app.services.litellm import active_model_names, build_model_list


def _provider(**kw) -> Provider:
    p = Provider(
        name=kw.get("name", "OpenAI"),
        kind=kw.get("kind", ProviderKind.openai_compatible),
        base_url=kw.get("base_url"),
        default_model=kw.get("default_model", "gpt-4o"),
        enabled=kw.get("enabled", True),
        active=kw.get("active", True),
    )
    p.id = kw.get("id", "prov-1")
    return p


def test_multiple_keys_produce_rotatable_entries():
    prov = _provider(default_model="gpt-4o")
    entries = build_model_list([(prov, [("k1", "sk-1"), ("k2", "sk-2")])])
    # Две записи с одинаковым model_name → ротация в роутере LiteLLM.
    assert len(entries) == 2
    assert {e["model_name"] for e in entries} == {"gpt-4o"}
    assert {e["litellm_params"]["api_key"] for e in entries} == {"sk-1", "sk-2"}
    assert entries[0]["litellm_params"]["model"] == "openai/gpt-4o"


def test_anthropic_prefix_and_base_url():
    prov = _provider(
        name="Anthropic", kind=ProviderKind.anthropic, default_model="claude-x",
        base_url="https://api.anthropic.com",
    )
    entries = build_model_list([(prov, [("k1", "sk-ant")])])
    assert entries[0]["litellm_params"]["model"] == "anthropic/claude-x"
    assert entries[0]["litellm_params"]["api_base"] == "https://api.anthropic.com"


def test_keyless_local_provider_gets_placeholder_key():
    prov = _provider(name="LM Studio", default_model="local", base_url="http://localhost:1234/v1")
    entries = build_model_list([(prov, [])])
    assert len(entries) == 1
    assert entries[0]["litellm_params"]["api_key"] == "not-needed"


def test_disabled_provider_excluded():
    prov = _provider(enabled=False)
    assert build_model_list([(prov, [("k1", "s")])]) == []


def test_active_model_names_filters_inactive():
    p1 = _provider(id="1", name="A", default_model="m1", active=True)
    p2 = _provider(id="2", name="B", default_model="m2", active=False)
    assert active_model_names([p1, p2]) == ["m1"]
