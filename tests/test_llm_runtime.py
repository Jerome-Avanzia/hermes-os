"""Tests for LLM Runtime — Sprint 45.

Covers: LlmProviderInfo model, LlmModel model, ModelCapabilities,
LlmProvider ABC, OllamaProvider, OpenAIProvider, AnthropicProvider,
OpenRouterProvider, GeminiProvider, LlmRuntime, ProviderSummary,
LlmHealthStatus, Gateway endpoints, Context Graph integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermes.models.llm_provider_info import (
    LlmProviderInfo,
    PROVIDER_HEALTH_STATES,
    compute_provider_health,
)
from hermes.models.llm_model import (
    LlmModel,
    ModelCapabilities,
    MODEL_STATUSES,
    MODEL_ATTENTION_STATES,
    compute_model_attention,
)
from hermes.runtime.llm_provider import LlmProvider
from hermes.runtime.llm_runtime import (
    LlmHealthStatus,
    LlmRuntime,
    ProviderSummary,
    _slugify,
    _infer_family,
    _extract_context_window,
    _infer_capabilities,
)


# ── LlmProviderInfo model tests ─────────────────────────────────────────────


def test_provider_info_defaults():
    p = LlmProviderInfo(id="test", name="Test")
    assert p.id == "test"
    assert p.name == "Test"
    assert p.provider_type == ""
    assert p.configured is False
    assert p.authenticated is False
    assert p.reachable is False
    assert p.default_model == ""
    assert p.model_count == 0
    assert p.health_state == "unconfigured"
    assert p.priority == 0


def test_provider_info_all_fields():
    p = LlmProviderInfo(
        id="openai",
        name="OpenAI",
        provider_type="cloud",
        configured=True,
        authenticated=True,
        reachable=True,
        default_model="gpt-4o",
        model_count=5,
        health_state="healthy",
        priority=1,
    )
    assert p.provider_type == "cloud"
    assert p.model_count == 5
    assert p.priority == 1


def test_provider_health_states_valid():
    assert "healthy" in PROVIDER_HEALTH_STATES
    assert "degraded" in PROVIDER_HEALTH_STATES
    assert "unreachable" in PROVIDER_HEALTH_STATES
    assert "unconfigured" in PROVIDER_HEALTH_STATES


# ── compute_provider_health tests ────────────────────────────────────────────


def test_provider_health_healthy():
    assert compute_provider_health(True, True, True) == "healthy"


def test_provider_health_degraded():
    assert compute_provider_health(True, False, True) == "degraded"


def test_provider_health_unreachable():
    assert compute_provider_health(True, True, False) == "unreachable"


def test_provider_health_unreachable_no_auth():
    assert compute_provider_health(True, False, False) == "unreachable"


def test_provider_health_unconfigured():
    assert compute_provider_health(False, False, False) == "unconfigured"


def test_provider_health_unconfigured_overrides_all():
    assert compute_provider_health(False, True, True) == "unconfigured"


# ── ModelCapabilities tests ──────────────────────────────────────────────────


def test_capabilities_defaults():
    c = ModelCapabilities()
    assert c.streaming is False
    assert c.tools is False
    assert c.reasoning is False
    assert c.vision is False


def test_capabilities_all_true():
    c = ModelCapabilities(streaming=True, tools=True, reasoning=True, vision=True)
    assert c.streaming is True
    assert c.tools is True
    assert c.reasoning is True
    assert c.vision is True


def test_capabilities_partial():
    c = ModelCapabilities(streaming=True, tools=True)
    assert c.streaming is True
    assert c.tools is True
    assert c.reasoning is False
    assert c.vision is False


# ── LlmModel tests ──────────────────────────────────────────────────────────


def test_model_defaults():
    m = LlmModel(id="test--model", name="Test Model")
    assert m.id == "test--model"
    assert m.name == "Test Model"
    assert m.provider_id == ""
    assert m.provider == ""
    assert m.family == ""
    assert m.context_window == 0
    assert isinstance(m.capabilities, ModelCapabilities)
    assert m.status == "unknown"
    assert m.attention_state == "ok"


def test_model_post_init_creates_capabilities():
    m = LlmModel(id="x", name="X")
    assert m.capabilities is not None
    assert isinstance(m.capabilities, ModelCapabilities)


def test_model_with_explicit_capabilities():
    caps = ModelCapabilities(streaming=True, tools=True)
    m = LlmModel(id="x", name="X", capabilities=caps)
    assert m.capabilities.streaming is True
    assert m.capabilities.tools is True


def test_model_all_fields():
    caps = ModelCapabilities(streaming=True, tools=True, reasoning=True, vision=True)
    m = LlmModel(
        id="anthropic--claude-sonnet-4-20250514",
        name="claude-sonnet-4-20250514",
        provider_id="anthropic",
        provider="Anthropic",
        family="claude",
        context_window=200000,
        capabilities=caps,
        status="available",
        attention_state="ok",
    )
    assert m.context_window == 200000
    assert m.family == "claude"
    assert m.capabilities.vision is True


def test_model_statuses_valid():
    assert "available" in MODEL_STATUSES
    assert "unavailable" in MODEL_STATUSES
    assert "unknown" in MODEL_STATUSES


def test_model_attention_states_valid():
    assert "ok" in MODEL_ATTENTION_STATES
    assert "warning" in MODEL_ATTENTION_STATES
    assert "critical" in MODEL_ATTENTION_STATES


# ── compute_model_attention tests ────────────────────────────────────────────


def test_model_attention_ok():
    assert compute_model_attention("available", True) == "ok"


def test_model_attention_warning_unavailable():
    assert compute_model_attention("unavailable", True) == "warning"


def test_model_attention_warning_unknown():
    assert compute_model_attention("unknown", True) == "warning"


def test_model_attention_critical():
    assert compute_model_attention("available", False) == "critical"


def test_model_attention_critical_overrides_status():
    assert compute_model_attention("unavailable", False) == "critical"


# ── _slugify tests ───────────────────────────────────────────────────────────


def test_slugify_basic():
    assert _slugify("gpt-4o") == "gpt-4o"


def test_slugify_uppercase():
    assert _slugify("GPT-4o") == "gpt-4o"


def test_slugify_spaces():
    assert _slugify("Claude Sonnet 4") == "claude-sonnet-4"


def test_slugify_special_chars():
    assert _slugify("model@v2!") == "model-v2"


def test_slugify_colons():
    assert _slugify("llama3.2:latest") == "llama3.2-latest"


def test_slugify_preserves_dots():
    assert _slugify("gemini-2.5-pro") == "gemini-2.5-pro"


def test_slugify_empty():
    assert _slugify("") == "unnamed"


def test_slugify_only_special():
    assert _slugify("@@@") == "unnamed"


def test_slugify_strip_whitespace():
    assert _slugify("  gpt-4o  ") == "gpt-4o"


# ── _infer_family tests ─────────────────────────────────────────────────────


def test_infer_family_claude():
    assert _infer_family("claude-sonnet-4-20250514") == "claude"


def test_infer_family_gpt():
    assert _infer_family("gpt-4o") == "gpt"


def test_infer_family_o1():
    assert _infer_family("o1-preview") == "gpt"


def test_infer_family_o3():
    assert _infer_family("o3-mini") == "gpt"


def test_infer_family_llama():
    assert _infer_family("llama3.2:latest") == "llama"


def test_infer_family_mistral():
    assert _infer_family("mistral-large") == "mistral"


def test_infer_family_mixtral():
    assert _infer_family("mixtral-8x7b") == "mistral"


def test_infer_family_gemini():
    assert _infer_family("gemini-2.5-pro") == "gemini"


def test_infer_family_gemma():
    assert _infer_family("gemma-2b") == "gemini"


def test_infer_family_phi():
    assert _infer_family("phi-3") == "phi"


def test_infer_family_qwen():
    assert _infer_family("qwen-72b") == "qwen"


def test_infer_family_deepseek():
    assert _infer_family("deepseek-r1") == "deepseek"


def test_infer_family_codestral():
    assert _infer_family("codestral-latest") == "code"


def test_infer_family_unknown():
    assert _infer_family("some-custom-model") == ""


# ── _extract_context_window tests ────────────────────────────────────────────


def test_context_window_openai_style():
    assert _extract_context_window({"context_length": 128000}) == 128000


def test_context_window_alt_key():
    assert _extract_context_window({"context_window": 200000}) == 200000


def test_context_window_gemini_style():
    assert _extract_context_window({"inputTokenLimit": 1048576}) == 1048576


def test_context_window_none():
    assert _extract_context_window({}) == 0


def test_context_window_zero():
    assert _extract_context_window({"context_length": 0}) == 0


def test_context_window_negative():
    assert _extract_context_window({"context_length": -1}) == 0


# ── _infer_capabilities tests ───────────────────────────────────────────────


def test_capabilities_claude_sonnet():
    caps = _infer_capabilities("claude-sonnet-4", {}, "anthropic")
    assert caps.streaming is True
    assert caps.tools is True
    assert caps.vision is True
    assert caps.reasoning is True


def test_capabilities_claude_haiku():
    caps = _infer_capabilities("claude-haiku-3.5", {}, "anthropic")
    assert caps.streaming is True
    assert caps.tools is True
    assert caps.vision is True
    assert caps.reasoning is False


def test_capabilities_gpt4o():
    caps = _infer_capabilities("gpt-4o", {}, "openai")
    assert caps.tools is True
    assert caps.vision is True


def test_capabilities_gpt35():
    caps = _infer_capabilities("gpt-3.5-turbo", {}, "openai")
    assert caps.tools is True
    assert caps.vision is False


def test_capabilities_o1():
    caps = _infer_capabilities("o1-preview", {}, "openai")
    assert caps.reasoning is True
    assert caps.vision is True


def test_capabilities_gemini_with_methods():
    raw = {"supportedGenerationMethods": ["generateContent"]}
    caps = _infer_capabilities("gemini-2.5-pro", raw, "gemini")
    assert caps.tools is True
    assert caps.vision is True
    assert caps.reasoning is True


def test_capabilities_gemini_no_generate():
    raw = {"supportedGenerationMethods": ["embedContent"]}
    caps = _infer_capabilities("gemini-2.0-flash", raw, "gemini")
    assert caps.tools is False


def test_capabilities_llama3():
    caps = _infer_capabilities("llama3.2", {}, "ollama")
    assert caps.tools is True


def test_capabilities_llama2():
    caps = _infer_capabilities("llama2", {}, "ollama")
    assert caps.tools is False


def test_capabilities_mistral():
    caps = _infer_capabilities("mistral-large", {}, "openrouter")
    assert caps.tools is True


def test_capabilities_default_streaming():
    caps = _infer_capabilities("custom-model", {}, "custom")
    assert caps.streaming is True
    assert caps.tools is False


# ── LlmProvider ABC tests ───────────────────────────────────────────────────


def test_llm_provider_is_abstract():
    with pytest.raises(TypeError):
        LlmProvider()  # type: ignore[abstract]


# ── Stub provider for LlmRuntime tests ──────────────────────────────────────


class _StubProvider(LlmProvider):
    def __init__(
        self,
        pname: str = "stub",
        display: str = "Stub",
        ptype: str = "cloud",
        is_configured: bool = True,
        health_data: dict | None = None,
        models: list[dict] | None = None,
    ) -> None:
        self._name = pname
        self._display = display
        self._ptype = ptype
        self._configured = is_configured
        self._health = health_data or {
            "configured": is_configured,
            "authenticated": is_configured,
            "reachable": is_configured,
        }
        self._models = models or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display

    @property
    def provider_type(self) -> str:
        return self._ptype

    @property
    def configured(self) -> bool:
        return self._configured

    def health(self) -> dict[str, Any]:
        return dict(self._health)

    def list_models(self) -> list[dict[str, Any]]:
        return list(self._models)


# ── ProviderSummary tests ────────────────────────────────────────────────────


def test_provider_summary_defaults():
    s = ProviderSummary(id="x", name="X")
    assert s.id == "x"
    assert s.name == "X"
    assert s.health_state == "unconfigured"


def test_provider_summary_with_state():
    s = ProviderSummary(id="y", name="Y", health_state="healthy")
    assert s.health_state == "healthy"


# ── LlmHealthStatus tests ───────────────────────────────────────────────────


def test_health_status_defaults():
    h = LlmHealthStatus()
    assert h.configured is False
    assert h.provider_count == 0
    assert h.healthy_count == 0
    assert h.model_count == 0
    assert h.default_provider == ""
    assert h.default_model == ""
    assert h.providers == []
    assert h.last_sync == ""
    assert h.refresh_duration_ms == 0


# ── LlmRuntime tests ────────────────────────────────────────────────────────


def test_runtime_configured_with_configured_provider():
    provider = _StubProvider(is_configured=True)
    rt = LlmRuntime([provider])
    assert rt.configured is True


def test_runtime_not_configured_with_no_configured_providers():
    provider = _StubProvider(is_configured=False)
    rt = LlmRuntime([provider])
    assert rt.configured is False


def test_runtime_configured_if_any_configured():
    p1 = _StubProvider(pname="a", is_configured=False)
    p2 = _StubProvider(pname="b", is_configured=True)
    rt = LlmRuntime([p1, p2])
    assert rt.configured is True


def test_runtime_list_providers_excludes_unconfigured():
    p1 = _StubProvider(pname="a", display="A", is_configured=True)
    p2 = _StubProvider(pname="b", display="B", is_configured=False)
    rt = LlmRuntime([p1, p2])
    providers = rt.list_providers()
    assert len(providers) == 1
    assert providers[0].id == "a"


def test_runtime_list_providers_priority():
    """Amendment 2: Priority is the provider's index position."""
    p1 = _StubProvider(pname="first", display="First", is_configured=True)
    p2 = _StubProvider(pname="second", display="Second", is_configured=True)
    rt = LlmRuntime([p1, p2])
    providers = rt.list_providers()
    assert providers[0].priority == 0
    assert providers[1].priority == 1


def test_runtime_list_providers_health_state():
    provider = _StubProvider(
        pname="healthy",
        display="Healthy",
        is_configured=True,
        health_data={"configured": True, "authenticated": True, "reachable": True},
    )
    rt = LlmRuntime([provider])
    providers = rt.list_providers()
    assert providers[0].health_state == "healthy"


def test_runtime_list_providers_unreachable():
    provider = _StubProvider(
        pname="down",
        display="Down",
        is_configured=True,
        health_data={"configured": True, "authenticated": False, "reachable": False},
    )
    rt = LlmRuntime([provider])
    providers = rt.list_providers()
    assert providers[0].health_state == "unreachable"


def test_runtime_get_provider_found():
    provider = _StubProvider(pname="test", display="Test", is_configured=True)
    rt = LlmRuntime([provider])
    info = rt.get_provider("test")
    assert info is not None
    assert info.id == "test"


def test_runtime_get_provider_not_found():
    provider = _StubProvider(pname="test", is_configured=True)
    rt = LlmRuntime([provider])
    assert rt.get_provider("nonexistent") is None


def test_runtime_list_models_basic():
    models = [
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
    ]
    provider = _StubProvider(pname="openai", display="OpenAI", models=models)
    rt = LlmRuntime([provider])
    result = rt.list_models()
    assert len(result) == 2


def test_runtime_list_models_hermes_id_format():
    """Amendment 5: IDs are provider--model-slug."""
    models = [{"id": "claude-sonnet-4-20250514"}]
    provider = _StubProvider(pname="anthropic", display="Anthropic", models=models)
    rt = LlmRuntime([provider])
    result = rt.list_models()
    assert result[0].id == "anthropic--claude-sonnet-4-20250514"


def test_runtime_list_models_immutable_id():
    """Amendment 5: Display name changes never alter canonical ID."""
    models = [{"id": "gpt-4o", "name": "GPT-4o (Latest)"}]
    provider = _StubProvider(pname="openai", display="OpenAI", models=models)
    rt = LlmRuntime([provider])
    result = rt.list_models()
    # ID derived from id field, not name
    assert result[0].id == "openai--gpt-4o"
    # Display name is still the friendly one
    assert result[0].name == "GPT-4o (Latest)"


def test_runtime_list_models_excludes_unconfigured():
    models = [{"id": "model-1"}]
    provider = _StubProvider(pname="x", is_configured=False, models=models)
    rt = LlmRuntime([provider])
    assert len(rt.list_models()) == 0


def test_runtime_list_models_provider_id():
    models = [{"id": "llama3.2"}]
    provider = _StubProvider(pname="ollama", display="Ollama", models=models)
    rt = LlmRuntime([provider])
    result = rt.list_models()
    assert result[0].provider_id == "ollama"
    assert result[0].provider == "Ollama"


def test_runtime_list_models_family():
    models = [{"id": "claude-opus-4"}]
    provider = _StubProvider(pname="anthropic", display="Anthropic", models=models)
    rt = LlmRuntime([provider])
    assert rt.list_models()[0].family == "claude"


def test_runtime_list_models_context_window():
    models = [{"id": "gpt-4o", "context_length": 128000}]
    provider = _StubProvider(pname="openai", display="OpenAI", models=models)
    rt = LlmRuntime([provider])
    assert rt.list_models()[0].context_window == 128000


def test_runtime_list_models_capabilities():
    """Amendment 3: Capabilities as value object."""
    models = [{"id": "claude-sonnet-4"}]
    provider = _StubProvider(pname="anthropic", display="Anthropic", models=models)
    rt = LlmRuntime([provider])
    m = rt.list_models()[0]
    assert isinstance(m.capabilities, ModelCapabilities)
    assert m.capabilities.tools is True
    assert m.capabilities.vision is True


def test_runtime_list_models_status_available():
    models = [{"id": "test-model"}]
    provider = _StubProvider(
        pname="test", display="Test",
        health_data={"configured": True, "authenticated": True, "reachable": True},
        models=models,
    )
    rt = LlmRuntime([provider])
    assert rt.list_models()[0].status == "available"


def test_runtime_list_models_status_unavailable():
    models = [{"id": "test-model"}]
    provider = _StubProvider(
        pname="test", display="Test",
        health_data={"configured": True, "authenticated": True, "reachable": False},
        models=models,
    )
    rt = LlmRuntime([provider])
    assert rt.list_models()[0].status == "unavailable"


def test_runtime_list_models_attention_state():
    models = [{"id": "test-model"}]
    provider = _StubProvider(
        pname="test", display="Test",
        health_data={"configured": True, "authenticated": True, "reachable": True},
        models=models,
    )
    rt = LlmRuntime([provider])
    assert rt.list_models()[0].attention_state == "ok"


def test_runtime_list_models_attention_critical_unreachable():
    models = [{"id": "test-model"}]
    provider = _StubProvider(
        pname="test", display="Test",
        health_data={"configured": True, "authenticated": True, "reachable": False},
        models=models,
    )
    rt = LlmRuntime([provider])
    assert rt.list_models()[0].attention_state == "critical"


def test_runtime_get_model_found():
    models = [{"id": "gpt-4o"}]
    provider = _StubProvider(pname="openai", display="OpenAI", models=models)
    rt = LlmRuntime([provider])
    m = rt.get_model("openai--gpt-4o")
    assert m is not None
    assert m.id == "openai--gpt-4o"


def test_runtime_get_model_not_found():
    models = [{"id": "gpt-4o"}]
    provider = _StubProvider(pname="openai", display="OpenAI", models=models)
    rt = LlmRuntime([provider])
    assert rt.get_model("nonexistent--model") is None


def test_runtime_list_models_for_provider():
    models = [{"id": "gpt-4o"}, {"id": "gpt-3.5-turbo"}]
    provider = _StubProvider(pname="openai", display="OpenAI", models=models)
    rt = LlmRuntime([provider])
    result = rt.list_models_for_provider(provider)
    assert len(result) == 2


def test_runtime_list_models_for_unconfigured_provider():
    models = [{"id": "model-1"}]
    provider = _StubProvider(pname="x", is_configured=False, models=models)
    rt = LlmRuntime([provider])
    assert rt.list_models_for_provider(provider) == []


def test_runtime_multi_provider_aggregation():
    p1 = _StubProvider(
        pname="openai", display="OpenAI",
        models=[{"id": "gpt-4o"}],
    )
    p2 = _StubProvider(
        pname="anthropic", display="Anthropic",
        models=[{"id": "claude-sonnet-4"}],
    )
    rt = LlmRuntime([p1, p2])
    all_models = rt.list_models()
    assert len(all_models) == 2
    ids = {m.id for m in all_models}
    assert "openai--gpt-4o" in ids
    assert "anthropic--claude-sonnet-4" in ids


# ── LlmRuntime.health() tests ───────────────────────────────────────────────


def test_runtime_health_configured():
    provider = _StubProvider(pname="test", display="Test", is_configured=True)
    rt = LlmRuntime([provider])
    h = rt.health()
    assert h.configured is True
    assert h.provider_count == 1
    assert h.model_count == 0


def test_runtime_health_not_configured():
    provider = _StubProvider(pname="test", display="Test", is_configured=False)
    rt = LlmRuntime([provider])
    h = rt.health()
    assert h.configured is False
    assert h.provider_count == 0


def test_runtime_health_healthy_count():
    p1 = _StubProvider(
        pname="good", display="Good",
        health_data={"configured": True, "authenticated": True, "reachable": True},
    )
    p2 = _StubProvider(
        pname="bad", display="Bad",
        health_data={"configured": True, "authenticated": True, "reachable": False},
    )
    rt = LlmRuntime([p1, p2])
    h = rt.health()
    assert h.healthy_count == 1
    assert h.provider_count == 2


def test_runtime_health_model_count():
    models = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
    provider = _StubProvider(pname="test", display="Test", models=models)
    rt = LlmRuntime([provider])
    h = rt.health()
    assert h.model_count == 3


def test_runtime_health_default_provider():
    rt = LlmRuntime([], default_provider="anthropic", default_model="claude-sonnet-4")
    h = rt.health()
    assert h.default_provider == "anthropic"
    assert h.default_model == "claude-sonnet-4"


def test_runtime_health_provider_summaries():
    """Amendment 4: ProviderSummary list."""
    p1 = _StubProvider(
        pname="openai", display="OpenAI",
        health_data={"configured": True, "authenticated": True, "reachable": True},
    )
    p2 = _StubProvider(
        pname="ollama", display="Ollama",
        health_data={"configured": True, "authenticated": True, "reachable": False},
    )
    rt = LlmRuntime([p1, p2])
    h = rt.health()
    assert len(h.providers) == 2
    assert h.providers[0].id == "openai"
    assert h.providers[0].health_state == "healthy"
    assert h.providers[1].id == "ollama"
    assert h.providers[1].health_state == "unreachable"


def test_runtime_health_last_sync():
    provider = _StubProvider(pname="test", display="Test", is_configured=True)
    rt = LlmRuntime([provider])
    h = rt.health()
    assert h.last_sync != ""


def test_runtime_health_refresh_duration():
    provider = _StubProvider(pname="test", display="Test", is_configured=True)
    rt = LlmRuntime([provider])
    h = rt.health()
    assert isinstance(h.refresh_duration_ms, int)
    assert h.refresh_duration_ms >= 0


# ── LlmRuntime error handling ───────────────────────────────────────────────


def test_runtime_health_exception_handled():
    """If a provider's health() raises, it's caught and treated as unreachable."""

    class _ExplodingProvider(_StubProvider):
        def health(self):
            raise ConnectionError("boom")

    provider = _ExplodingProvider(pname="boom", display="Boom", is_configured=True)
    rt = LlmRuntime([provider])
    providers = rt.list_providers()
    assert len(providers) == 1
    assert providers[0].health_state == "unreachable"


def test_runtime_provider_default_model():
    """First model's name becomes provider's default_model."""
    models = [{"id": "m1", "name": "First Model"}, {"id": "m2", "name": "Second"}]
    provider = _StubProvider(pname="test", display="Test", models=models)
    rt = LlmRuntime([provider])
    info = rt.list_providers()[0]
    assert info.default_model == "First Model"
    assert info.model_count == 2


def test_runtime_provider_no_models():
    provider = _StubProvider(pname="test", display="Test", models=[])
    rt = LlmRuntime([provider])
    info = rt.list_providers()[0]
    assert info.default_model == ""
    assert info.model_count == 0


def test_runtime_empty_providers():
    rt = LlmRuntime([])
    assert rt.configured is False
    assert rt.list_providers() == []
    assert rt.list_models() == []
    h = rt.health()
    assert h.configured is False
    assert h.provider_count == 0


# ── Provider implementations: basic structure tests ─────────────────────────


def test_ollama_provider_properties():
    from hermes.runtime.ollama_provider import OllamaProvider

    p = OllamaProvider("")
    assert p.name == "ollama"
    assert p.display_name == "Ollama"
    assert p.provider_type == "local"
    assert p.configured is False


def test_ollama_provider_configured_with_url():
    from hermes.runtime.ollama_provider import OllamaProvider

    p = OllamaProvider("http://localhost:11434")
    assert p.configured is True


def test_ollama_provider_unconfigured_health():
    from hermes.runtime.ollama_provider import OllamaProvider

    p = OllamaProvider("")
    h = p.health()
    assert h["configured"] is False
    assert h["reachable"] is False


def test_ollama_provider_unconfigured_list_models():
    from hermes.runtime.ollama_provider import OllamaProvider

    p = OllamaProvider("")
    assert p.list_models() == []


def test_openai_provider_properties():
    from hermes.runtime.openai_provider import OpenAIProvider

    p = OpenAIProvider("")
    assert p.name == "openai"
    assert p.display_name == "OpenAI"
    assert p.provider_type == "cloud"
    assert p.configured is False


def test_openai_provider_configured_with_key():
    from hermes.runtime.openai_provider import OpenAIProvider

    p = OpenAIProvider("sk-test")
    assert p.configured is True


def test_openai_provider_unconfigured_list_models():
    from hermes.runtime.openai_provider import OpenAIProvider

    p = OpenAIProvider("")
    assert p.list_models() == []


def test_anthropic_provider_properties():
    from hermes.runtime.anthropic_provider import AnthropicProvider

    p = AnthropicProvider("")
    assert p.name == "anthropic"
    assert p.display_name == "Anthropic"
    assert p.provider_type == "cloud"
    assert p.configured is False


def test_anthropic_provider_configured_with_key():
    from hermes.runtime.anthropic_provider import AnthropicProvider

    p = AnthropicProvider("sk-ant-test")
    assert p.configured is True


def test_anthropic_provider_unconfigured_list_models():
    from hermes.runtime.anthropic_provider import AnthropicProvider

    p = AnthropicProvider("")
    assert p.list_models() == []


def test_openrouter_provider_properties():
    from hermes.runtime.openrouter_provider import OpenRouterProvider

    p = OpenRouterProvider("")
    assert p.name == "openrouter"
    assert p.display_name == "OpenRouter"
    assert p.provider_type == "cloud"
    assert p.configured is False


def test_openrouter_provider_configured_with_key():
    from hermes.runtime.openrouter_provider import OpenRouterProvider

    p = OpenRouterProvider("sk-or-test")
    assert p.configured is True


def test_openrouter_provider_unconfigured_list_models():
    from hermes.runtime.openrouter_provider import OpenRouterProvider

    p = OpenRouterProvider("")
    assert p.list_models() == []


def test_gemini_provider_properties():
    from hermes.runtime.gemini_provider import GeminiProvider

    p = GeminiProvider("")
    assert p.name == "gemini"
    assert p.display_name == "Gemini"
    assert p.provider_type == "cloud"
    assert p.configured is False


def test_gemini_provider_configured_with_key():
    from hermes.runtime.gemini_provider import GeminiProvider

    p = GeminiProvider("AIzaSy-test")
    assert p.configured is True


def test_gemini_provider_unconfigured_list_models():
    from hermes.runtime.gemini_provider import GeminiProvider

    p = GeminiProvider("")
    assert p.list_models() == []


# ── Gateway endpoint tests ──────────────────────────────────────────────────


@pytest.fixture
def _mock_llm_runtime():
    """Patch HermesService._get_llm_runtime to return a stub LlmRuntime."""
    models = [
        {"id": "gpt-4o", "name": "GPT-4o", "context_length": 128000},
        {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
    ]
    provider = _StubProvider(
        pname="openai", display="OpenAI",
        health_data={"configured": True, "authenticated": True, "reachable": True},
        models=models,
    )
    rt = LlmRuntime([provider], default_provider="openai", default_model="gpt-4o")
    with patch("hermes.service.HermesService._get_llm_runtime", return_value=rt):
        yield rt


@pytest.fixture
def gateway_client():
    from hermes.gateway.app import app
    from starlette.testclient import TestClient

    return TestClient(app)


class TestGatewayLlmProviders:
    def test_list_providers(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/v1/llm-providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "openai"
        assert data[0]["name"] == "OpenAI"
        assert data[0]["health_state"] == "healthy"

    def test_get_provider(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/v1/llm-providers/openai")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "openai"
        assert "models" in data

    def test_get_provider_not_found(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/v1/llm-providers/nonexistent")
        assert resp.status_code == 404


class TestGatewayLlmModels:
    def test_list_models(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/v1/llm-models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2

    def test_get_model(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/v1/llm-models/openai--gpt-4o")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "openai--gpt-4o"
        assert "capabilities" in data

    def test_get_model_not_found(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/v1/llm-models/nonexistent--model")
        assert resp.status_code == 404


class TestGatewayLlmHealth:
    def test_health(self, gateway_client, _mock_llm_runtime):
        resp = gateway_client.get("/health/llm")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["provider_count"] == 1
        assert data["healthy_count"] == 1
        assert data["model_count"] == 2
        assert "providers" in data


# ── Context Graph integration tests ─────────────────────────────────────────


def test_context_graph_supported_types_include_llm():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert "llm_provider" in SUPPORTED_TYPES
    assert "model" in SUPPORTED_TYPES


def test_context_graph_relation_keys_include_llm():
    from hermes.context.context_graph import ALL_RELATION_KEYS
    assert "llm_providers" in ALL_RELATION_KEYS
    assert "models" in ALL_RELATION_KEYS


def test_context_graph_llm_provider_edges_exist():
    from hermes.context.context_graph import _EDGES
    assert "llm_provider" in _EDGES
    edges = _EDGES["llm_provider"]
    assert "models" in edges
    assert "capabilities" in edges
    assert "goals" in edges


def test_context_graph_model_edges_exist():
    from hermes.context.context_graph import _EDGES
    assert "model" in _EDGES
    edges = _EDGES["model"]
    assert "llm_providers" in edges
    assert "capabilities" in edges
    assert "goals" in edges


def test_context_graph_existing_types_have_llm_edges():
    from hermes.context.context_graph import _EDGES
    for etype in ["goal", "person", "department", "capability", "operation",
                  "repository", "service", "workflow", "database", "table"]:
        assert "llm_providers" in _EDGES[etype], f"{etype} missing llm_providers edge"
        assert "models" in _EDGES[etype], f"{etype} missing models edge"


def test_context_graph_finders_include_llm():
    from hermes.context.context_graph import _FINDERS
    assert "llm_provider" in _FINDERS
    assert "model" in _FINDERS


def test_context_graph_summarizers_include_llm():
    from hermes.context.context_graph import _SUMMARIZERS
    assert "llm_provider" in _SUMMARIZERS
    assert "model" in _SUMMARIZERS


def test_context_graph_serializer_llm_provider():
    from hermes.context.context_graph import _ser_llm_provider

    p = LlmProviderInfo(
        id="openai", name="OpenAI", provider_type="cloud",
        health_state="healthy", model_count=5,
    )
    result = _ser_llm_provider(p)
    assert result["id"] == "openai"
    assert result["name"] == "OpenAI"
    assert result["provider_type"] == "cloud"
    assert result["health_state"] == "healthy"
    assert result["model_count"] == 5


def test_context_graph_serializer_llm_model():
    from hermes.context.context_graph import _ser_llm_model

    m = LlmModel(
        id="openai--gpt-4o", name="GPT-4o",
        provider_id="openai", family="gpt",
        status="available", attention_state="ok",
    )
    result = _ser_llm_model(m)
    assert result["id"] == "openai--gpt-4o"
    assert result["name"] == "GPT-4o"
    assert result["provider_id"] == "openai"
    assert result["family"] == "gpt"
    assert result["status"] == "available"
    assert result["attention_state"] == "ok"


# ── Capability model_refs test ──────────────────────────────────────────────


def test_capability_has_model_refs():
    from hermes.models.capability import Capability

    c = Capability(
        id="cap-1", name="NLP", version="1.0",
        provides=["nlp"], keywords=["ai"],
        model_refs=["openai--gpt-4o"],
    )
    assert c.model_refs == ["openai--gpt-4o"]


def test_capability_model_refs_default():
    from hermes.models.capability import Capability

    c = Capability(
        id="cap-1", name="Test", version="1.0",
        provides=["test"], keywords=["test"],
    )
    assert c.model_refs == []


# ── Config functions test ────────────────────────────────────────────────────


def test_config_llm_functions_exist():
    from hermes import config
    assert callable(config.ollama_url)
    assert callable(config.openai_api_key)
    assert callable(config.anthropic_api_key)
    assert callable(config.openrouter_api_key)
    assert callable(config.gemini_api_key)
    assert callable(config.llm_default_provider)
    assert callable(config.llm_default_model)


def test_config_llm_defaults():
    from hermes import config
    # Without env vars set, these should return empty strings
    assert isinstance(config.ollama_url(), str)
    assert isinstance(config.openai_api_key(), str)
    assert isinstance(config.anthropic_api_key(), str)
    assert isinstance(config.openrouter_api_key(), str)
    assert isinstance(config.gemini_api_key(), str)
    assert isinstance(config.llm_default_provider(), str)
    assert isinstance(config.llm_default_model(), str)
