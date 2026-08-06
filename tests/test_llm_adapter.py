"""Sprint 61 — LLM Adapter tests.

Tests the provider-agnostic LLM Adapter layer:

  Execution Gateway → LLM Adapter → Provider

Validates:
  • typed contracts (all frozen, all slots=True)
  • request translation (ExecutionRequest → LLMRequest)
  • response translation (raw dict → LLMResponse)
  • provider registration (first-wins, capability discovery)
  • execution flow (validate → build → call → parse → result)
  • error translation (network failure, parse failure, validation failure)
  • provider independence (dispatch through driver registry, no provider names in adapter)
  • streaming metadata (streaming flag carried through correctly)
  • structured output metadata (schema carried through correctly)
  • deterministic translation (same inputs → same LLMRequest)
  • immutable contracts (all models are frozen dataclasses)

No real provider calls. The HTTP caller is replaced with a mock driver
in all tests to isolate translation logic from network availability.
"""

from __future__ import annotations

import dataclasses

import pytest

from hermes.adapters.llm_adapter import LlmAdapter, ProviderDriver
from hermes.providers.anthropic_driver import ANTHROPIC_CAPABILITIES
from hermes.providers.gemini_driver import GEMINI_CAPABILITIES
from hermes.providers.ollama_driver import (
    OLLAMA_CAPABILITIES,
    OLLAMA_DRIVER,
    _build_ollama_payload,
    _parse_ollama_response,
)
from hermes.providers.openai_driver import OPENAI_CAPABILITIES
from hermes.providers.openrouter_driver import OPENROUTER_CAPABILITIES
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionStatus,
)
from hermes.models.llm_adapter import (
    AdapterConfiguration,
    AdapterExecutionResult,
    AdapterValidationResult,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
)


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_config(
    provider: LLMProvider = LLMProvider.OLLAMA,
    model: str = "llama3.2",
    base_url: str = "http://localhost:11434",
    api_key: str = "",
    max_tokens: int = 256,
    temperature: float = 0.0,
    streaming: bool = False,
    structured_output_schema: str = "",
    timeout_seconds: int = 30,
) -> AdapterConfiguration:
    return AdapterConfiguration(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
        streaming=streaming,
        structured_output_schema=structured_output_schema,
    )


def _make_gateway_request(
    request_id: str = "req-001",
    operation_id: str = "op-draft",
    action_id: str = "chat",
    payload: dict | None = None,
) -> object:
    """Build an ExecutionRequest via the Gateway (uses build_request factory)."""
    gateway = ExecutionGateway()
    return gateway.build_request(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=ExecutionAdapter.LLM,
        action_id=action_id,
        payload=payload or {"prompt": "Draft the announcement"},
    )


def _make_ollama_raw_response(
    content: str = "Here is the draft.",
    model: str = "llama3.2",
    prompt_eval_count: int = 42,
    eval_count: int = 18,
    done_reason: str = "stop",
) -> dict:
    """Construct a realistic Ollama /api/chat response dict."""
    return {
        "model": model,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "total_duration": 1_234_567_890,
        "load_duration": 100_000,
        "eval_duration": 900_000_000,
    }


def _mock_call_returns(response_dict: dict):
    """Return a mock call_provider function that returns a fixed dict."""
    def _mock(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
        return response_dict
    return _mock


def _mock_call_raises(exc: Exception):
    """Return a mock call_provider function that raises an exception."""
    def _mock(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
        raise exc
    return _mock


def _make_ollama_adapter(
    raw_response: dict | None = None,
    call_raises: Exception | None = None,
) -> LlmAdapter:
    """Build an LlmAdapter with Ollama registered and a mocked HTTP caller."""
    adapter = LlmAdapter()

    if call_raises is not None:
        caller = _mock_call_raises(call_raises)
    else:
        caller = _mock_call_returns(
            raw_response if raw_response is not None else _make_ollama_raw_response()
        )

    mock_driver = ProviderDriver(
        build_payload=_build_ollama_payload,
        call_provider=caller,
        parse_response=_parse_ollama_response,
        endpoint_path="/api/chat",
    )
    adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=mock_driver)
    return adapter


# ══════════════════════════════════════════════════════════════════════════════
# 1. Typed contracts — frozen dataclasses with slots
# ══════════════════════════════════════════════════════════════════════════════


class TestTypedContracts:
    """All LLM adapter contracts are frozen dataclasses with slots."""

    def test_provider_capabilities_is_frozen(self) -> None:
        caps = OLLAMA_CAPABILITIES
        assert dataclasses.is_dataclass(caps)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            caps.supports_streaming = False  # type: ignore[misc]

    def test_adapter_configuration_is_frozen(self) -> None:
        config = _make_config()
        assert dataclasses.is_dataclass(config)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            config.model = "mutated"  # type: ignore[misc]

    def test_llm_request_is_frozen(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config()
        llm_request = adapter.build_llm_request(request, config)
        assert dataclasses.is_dataclass(llm_request)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            llm_request.user_prompt = "mutated"  # type: ignore[misc]

    def test_llm_response_is_frozen(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config()
        llm_request = adapter.build_llm_request(request, config)
        raw = _make_ollama_raw_response()
        response = _parse_ollama_response(raw, llm_request)
        assert dataclasses.is_dataclass(response)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            response.content = "mutated"  # type: ignore[misc]

    def test_adapter_validation_result_is_frozen(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config()
        result = adapter.validate(request, config)
        assert dataclasses.is_dataclass(result)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.valid = False  # type: ignore[misc]

    def test_adapter_execution_result_is_frozen(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config()
        result = adapter.execute(request, config)
        assert dataclasses.is_dataclass(result)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.success = False  # type: ignore[misc]

    def test_all_contracts_have_slots(self) -> None:
        """All frozen contracts use __slots__ for memory efficiency."""
        for cls in [
            AdapterConfiguration,
            AdapterExecutionResult,
            AdapterValidationResult,
            LLMRequest,
            LLMResponse,
            ProviderCapabilities,
        ]:
            assert hasattr(cls, "__slots__"), (
                f"{cls.__name__} must use slots=True"
            )


# ══════════════════════════════════════════════════════════════════════════════
# 2. Provider registration
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderRegistration:
    """Provider registration uses first-wins semantics."""

    def test_register_provider_with_driver_returns_true(self) -> None:
        adapter = LlmAdapter()
        result = adapter.register_provider(
            LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=OLLAMA_DRIVER,
        )
        assert result is True

    def test_register_provider_without_driver_returns_true(self) -> None:
        adapter = LlmAdapter()
        result = adapter.register_provider(LLMProvider.ANTHROPIC, ANTHROPIC_CAPABILITIES)
        assert result is True

    def test_duplicate_registration_returns_false(self) -> None:
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES)
        result = adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES)
        assert result is False

    def test_first_registration_wins(self) -> None:
        """Second registration of same provider is silently ignored."""
        adapter = LlmAdapter()
        caps_v1 = ProviderCapabilities(
            provider=LLMProvider.OPENAI,
            supports_streaming=True,
            supports_structured_output=False,
            supports_system_prompt=True,
            supports_tool_use=False,
            max_context_tokens=4096,
            default_model="gpt-3.5-turbo",
            requires_api_key=True,
        )
        caps_v2 = ProviderCapabilities(
            provider=LLMProvider.OPENAI,
            supports_streaming=True,
            supports_structured_output=True,
            supports_system_prompt=True,
            supports_tool_use=True,
            max_context_tokens=128_000,
            default_model="gpt-4o",
            requires_api_key=True,
        )
        adapter.register_provider(LLMProvider.OPENAI, caps_v1)
        adapter.register_provider(LLMProvider.OPENAI, caps_v2)  # ignored

        discovered = adapter.discover_capabilities(LLMProvider.OPENAI)
        assert discovered == caps_v1
        assert discovered.default_model == "gpt-3.5-turbo"

    def test_list_providers_returns_all_registered(self) -> None:
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES)
        adapter.register_provider(LLMProvider.ANTHROPIC, ANTHROPIC_CAPABILITIES)
        adapter.register_provider(LLMProvider.OPENAI, OPENAI_CAPABILITIES)

        providers = adapter.list_providers()
        assert LLMProvider.OLLAMA in providers
        assert LLMProvider.ANTHROPIC in providers
        assert LLMProvider.OPENAI in providers

    def test_list_providers_is_deterministic_order(self) -> None:
        """list_providers() is sorted by provider value regardless of registration order."""
        adapter = LlmAdapter()
        # Register in reverse alphabetical order
        adapter.register_provider(LLMProvider.OPENROUTER, OPENROUTER_CAPABILITIES)
        adapter.register_provider(LLMProvider.GEMINI, GEMINI_CAPABILITIES)
        adapter.register_provider(LLMProvider.ANTHROPIC, ANTHROPIC_CAPABILITIES)

        providers = adapter.list_providers()
        values = [p.value for p in providers]
        assert values == sorted(values)

    def test_discover_capabilities_returns_none_for_unregistered(self) -> None:
        adapter = LlmAdapter()
        assert adapter.discover_capabilities(LLMProvider.OPENAI) is None

    def test_has_driver_true_when_driver_registered(self) -> None:
        adapter = LlmAdapter()
        adapter.register_provider(
            LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=OLLAMA_DRIVER,
        )
        assert adapter.has_driver(LLMProvider.OLLAMA) is True

    def test_has_driver_false_when_only_capabilities_registered(self) -> None:
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.ANTHROPIC, ANTHROPIC_CAPABILITIES)
        assert adapter.has_driver(LLMProvider.ANTHROPIC) is False

    def test_all_five_providers_have_declared_capabilities(self) -> None:
        """All Sprint 61 providers have declared ProviderCapabilities constants."""
        all_caps = [
            OLLAMA_CAPABILITIES,
            ANTHROPIC_CAPABILITIES,
            OPENAI_CAPABILITIES,
            GEMINI_CAPABILITIES,
            OPENROUTER_CAPABILITIES,
        ]
        expected_providers = {p for p in LLMProvider}
        declared_providers = {caps.provider for caps in all_caps}
        assert expected_providers == declared_providers


# ══════════════════════════════════════════════════════════════════════════════
# 3. Capability discovery
# ══════════════════════════════════════════════════════════════════════════════


class TestCapabilityDiscovery:
    """Capability discovery works independently of execution drivers."""

    def test_ollama_capabilities_are_correct(self) -> None:
        caps = OLLAMA_CAPABILITIES
        assert caps.provider == LLMProvider.OLLAMA
        assert caps.supports_system_prompt is True
        assert caps.requires_api_key is False
        assert caps.max_context_tokens > 0
        assert caps.default_model != ""

    def test_anthropic_capabilities_require_api_key(self) -> None:
        assert ANTHROPIC_CAPABILITIES.requires_api_key is True

    def test_gemini_has_largest_context(self) -> None:
        """Gemini 2.0 Flash has the largest declared context window."""
        all_caps = [
            OLLAMA_CAPABILITIES,
            ANTHROPIC_CAPABILITIES,
            OPENAI_CAPABILITIES,
            GEMINI_CAPABILITIES,
            OPENROUTER_CAPABILITIES,
        ]
        max_context = max(c.max_context_tokens for c in all_caps)
        assert GEMINI_CAPABILITIES.max_context_tokens == max_context

    def test_register_all_capabilities_and_discover(self) -> None:
        adapter = LlmAdapter()
        for caps in [
            OLLAMA_CAPABILITIES, ANTHROPIC_CAPABILITIES, OPENAI_CAPABILITIES,
            GEMINI_CAPABILITIES, OPENROUTER_CAPABILITIES,
        ]:
            adapter.register_provider(caps.provider, caps)

        for provider in LLMProvider:
            discovered = adapter.discover_capabilities(provider)
            assert discovered is not None
            assert discovered.provider == provider

    def test_capability_flags_are_typed_bools(self) -> None:
        for caps in [
            OLLAMA_CAPABILITIES, ANTHROPIC_CAPABILITIES, OPENAI_CAPABILITIES,
        ]:
            assert isinstance(caps.supports_streaming, bool)
            assert isinstance(caps.supports_structured_output, bool)
            assert isinstance(caps.supports_system_prompt, bool)
            assert isinstance(caps.supports_tool_use, bool)
            assert isinstance(caps.requires_api_key, bool)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Request translation — ExecutionRequest → LLMRequest
# ══════════════════════════════════════════════════════════════════════════════


class TestRequestTranslation:
    """build_llm_request() produces correct normalized LLMRequest."""

    def test_user_prompt_extracted_from_payload(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(payload={"prompt": "Draft the announcement"})
        config = _make_config()
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.user_prompt == "Draft the announcement"

    def test_system_prompt_extracted_from_payload(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(
            payload={"prompt": "Draft it", "system_prompt": "You are a copywriter"},
        )
        config = _make_config()
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.system_prompt == "You are a copywriter"

    def test_system_prompt_empty_when_not_provided(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(payload={"prompt": "Analyse this"})
        config = _make_config()
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.system_prompt == ""

    def test_schema_from_payload_overrides_config_schema(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(
            payload={"prompt": "List items", "schema": '{"type":"array"}'},
        )
        config = _make_config(structured_output_schema='{"type":"object"}')
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.structured_output_schema == '{"type":"array"}'

    def test_schema_from_config_when_not_in_payload(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(payload={"prompt": "List items"})
        config = _make_config(structured_output_schema='{"type":"object"}')
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.structured_output_schema == '{"type":"object"}'

    def test_remaining_payload_becomes_metadata(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(
            payload={"prompt": "Summarize", "context_id": "ctx-001", "tone": "formal"},
        )
        config = _make_config()
        llm_req = adapter.build_llm_request(request, config)
        meta_dict = dict(llm_req.metadata)
        assert meta_dict.get("context_id") == "ctx-001"
        assert meta_dict.get("tone") == "formal"

    def test_metadata_is_sorted(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(
            payload={"prompt": "P", "z_key": "z", "a_key": "a", "m_key": "m"},
        )
        config = _make_config()
        llm_req = adapter.build_llm_request(request, config)
        keys = [k for k, _ in llm_req.metadata]
        assert keys == sorted(keys)

    def test_provider_and_model_from_config(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(provider=LLMProvider.OLLAMA, model="llama3.2")
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.provider == LLMProvider.OLLAMA
        assert llm_req.model == "llama3.2"

    def test_max_tokens_from_config(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(max_tokens=512)
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.max_tokens == 512

    def test_temperature_from_config(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(temperature=0.7)
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.temperature == 0.7

    def test_streaming_flag_from_config(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(streaming=True)
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.streaming is True

    def test_request_id_preserved(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(request_id="req-xyz")
        config = _make_config()
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.request_id == "req-xyz"

    def test_translation_is_deterministic(self) -> None:
        """Same ExecutionRequest + AdapterConfiguration always → same LLMRequest."""
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(
            payload={"prompt": "Draft announcement", "system_prompt": "Be concise"},
        )
        config = _make_config(model="llama3.2", max_tokens=256, temperature=0.0)
        r1 = adapter.build_llm_request(request, config)
        r2 = adapter.build_llm_request(request, config)
        assert r1 == r2


# ══════════════════════════════════════════════════════════════════════════════
# 5. Ollama payload building
# ══════════════════════════════════════════════════════════════════════════════


class TestOllamaPayloadBuilding:
    """_build_ollama_payload() produces correct Ollama /api/chat JSON."""

    def _make_llm_request(
        self,
        user_prompt: str = "Draft it",
        system_prompt: str = "",
        schema: str = "",
        streaming: bool = False,
        max_tokens: int = 256,
        temperature: float = 0.0,
        model: str = "llama3.2",
    ) -> LLMRequest:
        return LLMRequest(
            request_id="req-001",
            provider=LLMProvider.OLLAMA,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            streaming=streaming,
            structured_output_schema=schema,
            metadata=(),
        )

    def test_user_message_in_messages(self) -> None:
        config = _make_config()
        req = self._make_llm_request(user_prompt="Hello")
        payload = _build_ollama_payload(req, config)
        messages = payload["messages"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "Hello"

    def test_system_message_included_when_present(self) -> None:
        config = _make_config()
        req = self._make_llm_request(system_prompt="You are a writer", user_prompt="Draft")
        payload = _build_ollama_payload(req, config)
        messages = payload["messages"]
        sys_msgs = [m for m in messages if m["role"] == "system"]
        assert len(sys_msgs) == 1
        assert sys_msgs[0]["content"] == "You are a writer"

    def test_system_message_omitted_when_empty(self) -> None:
        config = _make_config()
        req = self._make_llm_request(system_prompt="")
        payload = _build_ollama_payload(req, config)
        roles = [m["role"] for m in payload["messages"]]
        assert "system" not in roles

    def test_stream_is_false(self) -> None:
        """Sprint 61: streaming disabled in payload regardless of config flag."""
        config = _make_config(streaming=True)
        req = self._make_llm_request(streaming=True)
        payload = _build_ollama_payload(req, config)
        assert payload["stream"] is False

    def test_model_in_payload(self) -> None:
        config = _make_config(model="qwen2.5")
        req = self._make_llm_request(model="qwen2.5")
        payload = _build_ollama_payload(req, config)
        assert payload["model"] == "qwen2.5"

    def test_options_carry_max_tokens_and_temperature(self) -> None:
        config = _make_config(max_tokens=512, temperature=0.3)
        req = self._make_llm_request(max_tokens=512, temperature=0.3)
        payload = _build_ollama_payload(req, config)
        opts = payload["options"]
        assert opts["num_predict"] == 512
        assert opts["temperature"] == 0.3

    def test_format_json_added_when_schema_present(self) -> None:
        config = _make_config()
        req = self._make_llm_request(schema='{"type":"object"}')
        payload = _build_ollama_payload(req, config)
        assert payload.get("format") == "json"

    def test_format_absent_when_no_schema(self) -> None:
        config = _make_config()
        req = self._make_llm_request(schema="")
        payload = _build_ollama_payload(req, config)
        assert "format" not in payload

    def test_payload_is_deterministic(self) -> None:
        config = _make_config(max_tokens=128, temperature=0.0)
        req = self._make_llm_request(user_prompt="Analyse", system_prompt="Be brief")
        p1 = _build_ollama_payload(req, config)
        p2 = _build_ollama_payload(req, config)
        assert p1 == p2


# ══════════════════════════════════════════════════════════════════════════════
# 6. Response translation — raw dict → LLMResponse
# ══════════════════════════════════════════════════════════════════════════════


class TestResponseTranslation:
    """_parse_ollama_response() correctly translates raw Ollama responses."""

    def _base_llm_request(self, request_id: str = "req-001") -> LLMRequest:
        return LLMRequest(
            request_id=request_id,
            provider=LLMProvider.OLLAMA,
            model="llama3.2",
            system_prompt="",
            user_prompt="Draft",
            max_tokens=256,
            temperature=0.0,
            streaming=False,
            structured_output_schema="",
            metadata=(),
        )

    def test_content_extracted(self) -> None:
        raw = _make_ollama_raw_response(content="Here is the draft.")
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.content == "Here is the draft."

    def test_token_counts_extracted(self) -> None:
        raw = _make_ollama_raw_response(prompt_eval_count=50, eval_count=20)
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.input_tokens == 50
        assert resp.output_tokens == 20
        assert resp.total_tokens == 70

    def test_finish_reason_extracted(self) -> None:
        raw = _make_ollama_raw_response(done_reason="length")
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.finish_reason == "length"

    def test_finish_reason_incomplete_when_not_done(self) -> None:
        raw = _make_ollama_raw_response()
        raw["done"] = False
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.finish_reason == "incomplete"

    def test_model_from_response_not_request(self) -> None:
        """If Ollama returns a different model name, use the response value."""
        raw = _make_ollama_raw_response(model="llama3.2:instruct")
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.model == "llama3.2:instruct"

    def test_provider_is_ollama(self) -> None:
        raw = _make_ollama_raw_response()
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.provider == LLMProvider.OLLAMA

    def test_streaming_used_is_false_in_sprint61(self) -> None:
        raw = _make_ollama_raw_response()
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.streaming_used is False

    def test_structured_output_used_true_when_schema_set(self) -> None:
        raw = _make_ollama_raw_response()
        req = LLMRequest(
            request_id="req-001",
            provider=LLMProvider.OLLAMA,
            model="llama3.2",
            system_prompt="",
            user_prompt="List",
            max_tokens=256,
            temperature=0.0,
            streaming=False,
            structured_output_schema='{"type":"array"}',
            metadata=(),
        )
        resp = _parse_ollama_response(raw, req)
        assert resp.structured_output_used is True

    def test_structured_output_used_false_when_no_schema(self) -> None:
        raw = _make_ollama_raw_response()
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.structured_output_used is False

    def test_timing_metadata_extracted(self) -> None:
        raw = _make_ollama_raw_response()
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        meta_dict = dict(resp.metadata)
        assert "eval_duration" in meta_dict
        assert "total_duration" in meta_dict

    def test_metadata_is_sorted(self) -> None:
        raw = _make_ollama_raw_response()
        req = self._base_llm_request()
        resp = _parse_ollama_response(raw, req)
        keys = [k for k, _ in resp.metadata]
        assert keys == sorted(keys)

    def test_request_id_preserved(self) -> None:
        raw = _make_ollama_raw_response()
        req = self._base_llm_request(request_id="req-xyz-123")
        resp = _parse_ollama_response(raw, req)
        assert resp.request_id == "req-xyz-123"


# ══════════════════════════════════════════════════════════════════════════════
# 7. Adapter-level validation
# ══════════════════════════════════════════════════════════════════════════════


class TestAdapterValidation:
    """validate() checks adapter-level preconditions only."""

    def test_valid_request_and_config(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config()
        result = adapter.validate(request, config)
        assert result.valid is True
        assert result.errors == ()

    def test_wrong_adapter_type_fails(self) -> None:
        adapter = _make_ollama_adapter()
        gateway = ExecutionGateway()
        # Build a non-LLM request
        request = gateway.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.GIT,
            action_id="clone",
        )
        config = _make_config()
        result = adapter.validate(request, config)
        assert result.valid is False
        assert any("LLM" in e for e in result.errors)

    def test_empty_model_fails(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(model="")
        result = adapter.validate(request, config)
        assert result.valid is False
        assert any("model" in e for e in result.errors)

    def test_empty_base_url_fails(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(base_url="")
        result = adapter.validate(request, config)
        assert result.valid is False
        assert any("base_url" in e for e in result.errors)

    def test_zero_max_tokens_fails(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(max_tokens=0)
        result = adapter.validate(request, config)
        assert result.valid is False
        assert any("max_tokens" in e for e in result.errors)

    def test_negative_max_tokens_fails(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(max_tokens=-1)
        result = adapter.validate(request, config)
        assert result.valid is False

    def test_zero_timeout_fails(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(timeout_seconds=0)
        result = adapter.validate(request, config)
        assert result.valid is False
        assert any("timeout" in e for e in result.errors)

    def test_unregistered_provider_fails(self) -> None:
        adapter = LlmAdapter()  # no providers registered
        request = _make_gateway_request()
        config = _make_config(provider=LLMProvider.OLLAMA)
        result = adapter.validate(request, config)
        assert result.valid is False
        assert any("ollama" in e.lower() for e in result.errors)

    def test_multiple_errors_accumulated(self) -> None:
        adapter = LlmAdapter()  # no providers
        request = _make_gateway_request()
        config = _make_config(model="", max_tokens=0)
        result = adapter.validate(request, config)
        assert result.valid is False
        assert len(result.errors) >= 2

    def test_validation_does_not_re_validate_gateway_fields(self) -> None:
        """Adapter validation skips request_id/operation_id/action_id — Gateway owns those."""
        adapter = _make_ollama_adapter()
        # Valid request — adapter should not validate request_id content
        request = _make_gateway_request(request_id="req-001")
        config = _make_config()
        result = adapter.validate(request, config)
        assert result.valid is True


# ══════════════════════════════════════════════════════════════════════════════
# 8. Execution flow
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutionFlow:
    """execute() runs the full 8-step pipeline."""

    def test_successful_execution_returns_content(self) -> None:
        raw = _make_ollama_raw_response(content="Here is the announcement draft.")
        adapter = _make_ollama_adapter(raw_response=raw)
        request = _make_gateway_request(payload={"prompt": "Draft the announcement"})
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.success is True
        assert result.llm_response is not None
        assert result.llm_response.content == "Here is the announcement draft."
        assert result.error is None

    def test_execution_result_has_llm_request(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(payload={"prompt": "Analyse"})
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.llm_request is not None
        assert result.llm_request.user_prompt == "Analyse"

    def test_execution_result_preserves_request_id(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(request_id="req-preserve")
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.request_id == "req-preserve"

    def test_execution_result_preserves_operation_id(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request(operation_id="op-important")
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.operation_id == "op-important"

    def test_adapter_metadata_is_sorted(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config()

        result = adapter.execute(request, config)

        keys = [k for k, _ in result.adapter_metadata]
        assert keys == sorted(keys)

    def test_adapter_metadata_contains_provider_and_model(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(model="llama3.2")

        result = adapter.execute(request, config)

        meta = dict(result.adapter_metadata)
        assert meta.get("provider") == "ollama"
        assert meta.get("model") == "llama3.2"

    def test_execution_with_system_prompt(self) -> None:
        raw = _make_ollama_raw_response(content="Formal draft.")
        adapter = _make_ollama_adapter(raw_response=raw)
        request = _make_gateway_request(
            payload={"prompt": "Write", "system_prompt": "Be formal"},
        )
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.success is True
        assert result.llm_request.system_prompt == "Be formal"

    def test_execution_token_counts_in_response(self) -> None:
        raw = _make_ollama_raw_response(prompt_eval_count=100, eval_count=50)
        adapter = _make_ollama_adapter(raw_response=raw)
        request = _make_gateway_request()
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.llm_response.input_tokens == 100
        assert result.llm_response.output_tokens == 50
        assert result.llm_response.total_tokens == 150


# ══════════════════════════════════════════════════════════════════════════════
# 9. Error translation
# ══════════════════════════════════════════════════════════════════════════════


class TestErrorTranslation:
    """All failures return AdapterExecutionResult(success=False) — no exceptions."""

    def test_validation_failure_returns_success_false(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(model="")  # invalid

        result = adapter.execute(request, config)

        assert result.success is False
        assert result.error is not None
        assert result.llm_request is None   # failed before translation
        assert result.llm_response is None

    def test_no_driver_returns_success_false(self) -> None:
        """Provider registered with capabilities only (no driver) → failure."""
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.ANTHROPIC, ANTHROPIC_CAPABILITIES)

        request = _make_gateway_request()
        config = _make_config(
            provider=LLMProvider.ANTHROPIC,
            base_url="https://api.anthropic.com",
            api_key="sk-test",
        )
        result = adapter.execute(request, config)

        assert result.success is False
        assert result.error is not None
        assert "anthropic" in result.error.lower()
        assert result.llm_request is not None   # translation completed
        assert result.llm_response is None

    def test_network_error_captured_in_result(self) -> None:
        import httpx
        adapter = _make_ollama_adapter(
            call_raises=httpx.ConnectError("Cannot connect"),
        )
        request = _make_gateway_request()
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.success is False
        assert result.error is not None
        assert "ConnectError" in result.error
        assert result.llm_request is not None   # translation completed
        assert result.llm_response is None

    def test_timeout_error_captured_in_result(self) -> None:
        import httpx
        adapter = _make_ollama_adapter(
            call_raises=httpx.TimeoutException("Timed out"),
        )
        request = _make_gateway_request()
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.success is False
        assert "TimeoutException" in result.error

    def test_generic_exception_captured_in_result(self) -> None:
        adapter = _make_ollama_adapter(
            call_raises=RuntimeError("Unexpected failure"),
        )
        request = _make_gateway_request()
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.success is False
        assert "RuntimeError" in result.error

    def test_error_result_never_raises(self) -> None:
        """execute() must never propagate exceptions — always returns a result."""
        adapter = _make_ollama_adapter(
            call_raises=Exception("Catastrophic failure"),
        )
        request = _make_gateway_request()
        config = _make_config()

        # Must not raise
        result = adapter.execute(request, config)
        assert isinstance(result, AdapterExecutionResult)
        assert result.success is False

    def test_parse_error_captured_in_result(self) -> None:
        """If parse_response raises, execute() captures it as a failure."""
        def _bad_parse(raw: dict, req: LLMRequest) -> LLMResponse:
            raise ValueError("Unexpected response format")

        adapter = LlmAdapter()
        bad_driver = ProviderDriver(
            build_payload=_build_ollama_payload,
            call_provider=_mock_call_returns({}),
            parse_response=_bad_parse,
            endpoint_path="/api/chat",
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=bad_driver)

        request = _make_gateway_request()
        config = _make_config()

        result = adapter.execute(request, config)

        assert result.success is False
        assert "ValueError" in result.error
        assert result.llm_request is not None
        assert result.llm_response is None


# ══════════════════════════════════════════════════════════════════════════════
# 10. Provider independence
# ══════════════════════════════════════════════════════════════════════════════


class TestProviderIndependence:
    """The adapter dispatches through the driver registry — never branches on provider names."""

    def test_adapter_dispatches_to_any_registered_driver(self) -> None:
        """Any ProviderDriver with the right interface works with the adapter."""
        call_log: list[str] = []

        def custom_build(req: LLMRequest, cfg: AdapterConfiguration) -> dict:
            return {"custom": "payload", "prompt": req.user_prompt}

        def custom_call(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
            call_log.append(endpoint)
            return {"content": "Custom response", "done": True}

        def custom_parse(raw: dict, req: LLMRequest) -> LLMResponse:
            return LLMResponse(
                request_id=req.request_id,
                provider=req.provider,
                model=req.model,
                content=raw["content"],
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                streaming_used=False,
                structured_output_used=False,
                finish_reason="stop",
                metadata=(),
            )

        custom_caps = ProviderCapabilities(
            provider=LLMProvider.GEMINI,
            supports_streaming=False,
            supports_structured_output=False,
            supports_system_prompt=True,
            supports_tool_use=False,
            max_context_tokens=1_000_000,
            default_model="gemini-2.0-flash",
            requires_api_key=True,
        )
        custom_driver = ProviderDriver(
            build_payload=custom_build,
            call_provider=custom_call,
            parse_response=custom_parse,
            endpoint_path="/v1/custom",
        )

        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.GEMINI, custom_caps, driver=custom_driver)

        request = _make_gateway_request()
        config = _make_config(
            provider=LLMProvider.GEMINI,
            base_url="https://api.gemini.test",
            api_key="test-key",
        )
        result = adapter.execute(request, config)

        assert result.success is True
        assert result.llm_response.content == "Custom response"
        assert len(call_log) == 1
        assert call_log[0] == "https://api.gemini.test/v1/custom"

    def test_multiple_providers_coexist_independently(self) -> None:
        """Two providers registered to the same adapter execute independently."""
        ollama_raw = _make_ollama_raw_response(content="Ollama response")
        openai_responses: list[str] = []

        def openai_call(endpoint, payload, timeout, api_key) -> dict:
            openai_responses.append("called")
            return {"choices": [{"message": {"content": "OpenAI response"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    "model": "gpt-4o"}

        def openai_parse(raw: dict, req: LLMRequest) -> LLMResponse:
            choice = raw["choices"][0]
            usage = raw.get("usage", {})
            return LLMResponse(
                request_id=req.request_id, provider=LLMProvider.OPENAI,
                model=raw.get("model", req.model),
                content=choice["message"]["content"],
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                streaming_used=False, structured_output_used=False,
                finish_reason=choice.get("finish_reason", "stop"), metadata=(),
            )

        def openai_build(req: LLMRequest, cfg: AdapterConfiguration) -> dict:
            messages = []
            if req.system_prompt:
                messages.append({"role": "system", "content": req.system_prompt})
            messages.append({"role": "user", "content": req.user_prompt})
            return {"model": req.model, "messages": messages,
                    "max_tokens": req.max_tokens, "temperature": req.temperature}

        openai_driver = ProviderDriver(
            build_payload=openai_build, call_provider=openai_call,
            parse_response=openai_parse, endpoint_path="/v1/chat/completions",
        )
        openai_caps = ProviderCapabilities(
            provider=LLMProvider.OPENAI, supports_streaming=True,
            supports_structured_output=True, supports_system_prompt=True,
            supports_tool_use=True, max_context_tokens=128_000,
            default_model="gpt-4o", requires_api_key=True,
        )

        adapter = LlmAdapter()

        ollama_mock_driver = ProviderDriver(
            build_payload=_build_ollama_payload,
            call_provider=_mock_call_returns(ollama_raw),
            parse_response=_parse_ollama_response,
            endpoint_path="/api/chat",
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=ollama_mock_driver)
        adapter.register_provider(LLMProvider.OPENAI, openai_caps, driver=openai_driver)

        # Ollama request
        ollama_req = _make_gateway_request(request_id="ollama-req")
        ollama_cfg = _make_config(provider=LLMProvider.OLLAMA, base_url="http://localhost:11434")
        ollama_result = adapter.execute(ollama_req, ollama_cfg)

        # OpenAI request
        openai_req = _make_gateway_request(request_id="openai-req")
        openai_cfg = _make_config(
            provider=LLMProvider.OPENAI, model="gpt-4o",
            base_url="https://api.openai.com", api_key="sk-test",
        )
        openai_result = adapter.execute(openai_req, openai_cfg)

        assert ollama_result.success is True
        assert ollama_result.llm_response.content == "Ollama response"
        assert ollama_result.provider == LLMProvider.OLLAMA

        assert openai_result.success is True
        assert openai_result.llm_response.content == "OpenAI response"
        assert openai_result.provider == LLMProvider.OPENAI
        assert len(openai_responses) == 1

    def test_endpoint_built_from_base_url_and_driver_path(self) -> None:
        """Endpoint = config.base_url + driver.endpoint_path (no provider-specific logic)."""
        endpoints_called: list[str] = []

        def capturing_call(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
            endpoints_called.append(endpoint)
            return _make_ollama_raw_response()

        driver = ProviderDriver(
            build_payload=_build_ollama_payload,
            call_provider=capturing_call,
            parse_response=_parse_ollama_response,
            endpoint_path="/api/chat",
        )
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=driver)

        request = _make_gateway_request()
        config = _make_config(base_url="http://localhost:11434")
        adapter.execute(request, config)

        assert endpoints_called == ["http://localhost:11434/api/chat"]

    def test_base_url_trailing_slash_normalised(self) -> None:
        """Trailing slash on base_url is stripped before endpoint path appended."""
        endpoints_called: list[str] = []

        def capturing_call(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
            endpoints_called.append(endpoint)
            return _make_ollama_raw_response()

        driver = ProviderDriver(
            build_payload=_build_ollama_payload,
            call_provider=capturing_call,
            parse_response=_parse_ollama_response,
            endpoint_path="/api/chat",
        )
        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=driver)

        request = _make_gateway_request()
        config = _make_config(base_url="http://localhost:11434/")  # trailing slash
        adapter.execute(request, config)

        # No double slash
        assert endpoints_called == ["http://localhost:11434/api/chat"]


# ══════════════════════════════════════════════════════════════════════════════
# 11. Streaming and structured output metadata
# ══════════════════════════════════════════════════════════════════════════════


class TestStreamingAndStructuredOutput:
    """Streaming and structured output flags are carried correctly through the pipeline."""

    def test_streaming_flag_carried_to_llm_request(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(streaming=True)
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.streaming is True

    def test_structured_output_schema_carried_to_llm_request(self) -> None:
        adapter = _make_ollama_adapter()
        request = _make_gateway_request()
        config = _make_config(structured_output_schema='{"type":"object","properties":{}}')
        llm_req = adapter.build_llm_request(request, config)
        assert llm_req.structured_output_schema == '{"type":"object","properties":{}}'

    def test_structured_output_triggers_format_json_in_ollama_payload(self) -> None:
        raw = _make_ollama_raw_response(content='{"key": "value"}')
        adapter = _make_ollama_adapter(raw_response=raw)
        request = _make_gateway_request(
            payload={"prompt": "Return JSON", "schema": '{"type":"object"}'},
        )
        config = _make_config()
        result = adapter.execute(request, config)

        assert result.success is True
        assert result.llm_response.structured_output_used is True

    def test_streaming_metadata_in_response_is_false_sprint61(self) -> None:
        """Sprint 61: streaming is not invoked; streaming_used is always False."""
        raw = _make_ollama_raw_response()
        adapter = _make_ollama_adapter(raw_response=raw)
        request = _make_gateway_request()
        config = _make_config(streaming=True)  # requested but not executed

        result = adapter.execute(request, config)

        assert result.llm_response.streaming_used is False

    def test_non_streaming_config_gives_streaming_false(self) -> None:
        raw = _make_ollama_raw_response()
        adapter = _make_ollama_adapter(raw_response=raw)
        request = _make_gateway_request()
        config = _make_config(streaming=False)

        result = adapter.execute(request, config)

        assert result.llm_response.streaming_used is False


# ══════════════════════════════════════════════════════════════════════════════
# 12. Gateway → LLM Adapter integration
# ══════════════════════════════════════════════════════════════════════════════


class TestGatewayAdapterIntegration:
    """Prove the Gateway dispatches to the LLM Adapter correctly."""

    def test_gateway_dispatches_then_adapter_executes(self) -> None:
        """Full flow: Gateway dispatch contract → LLM Adapter execution."""
        # Step 1: Gateway registers the LLM adapter
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-ollama",
            available=True,
            description="Ollama LLM adapter",
        ))

        # Step 2: Gateway produces a dispatch contract
        gw_request = gateway.build_request(
            request_id="e2e-001",
            operation_id="op-draft",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload={"prompt": "Draft the launch announcement"},
        )
        gw_result = gateway.dispatch(gw_request)
        assert gw_result.status == ExecutionStatus.DISPATCHED

        # Step 3: LLM Adapter executes the same request
        raw = _make_ollama_raw_response(content="The launch is live.")
        adapter = _make_ollama_adapter(raw_response=raw)
        config = _make_config()
        adapter_result = adapter.execute(gw_request, config)

        assert adapter_result.success is True
        assert adapter_result.llm_response.content == "The launch is live."
        assert adapter_result.request_id == gw_result.request_id

    def test_gateway_request_flows_through_adapter_unmodified(self) -> None:
        """ExecutionRequest from Gateway is accepted by LLM Adapter as-is."""
        gateway = ExecutionGateway()
        gw_request = gateway.build_request(
            request_id="flow-001",
            operation_id="op-analyse",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload={"prompt": "Analyse the data", "system_prompt": "Be concise"},
        )

        adapter = _make_ollama_adapter()
        config = _make_config()
        adapter_result = adapter.execute(gw_request, config)

        assert adapter_result.request_id == "flow-001"
        assert adapter_result.operation_id == "op-analyse"
        assert adapter_result.llm_request.user_prompt == "Analyse the data"
        assert adapter_result.llm_request.system_prompt == "Be concise"

    def test_architecture_boundary_is_preserved(self) -> None:
        """Gateway never invokes the adapter; adapter receives the contract."""
        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-ollama",
            available=True,
            description="Ollama",
        ))

        gw_request = gateway.build_request(
            request_id="arch-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
        )
        gw_result = gateway.dispatch(gw_request)

        # Gateway produces contract — no execution (output is empty)
        assert gw_result.output == ""
        assert gw_result.status == ExecutionStatus.DISPATCHED

        # Adapter executes — output comes from provider
        adapter = _make_ollama_adapter(
            raw_response=_make_ollama_raw_response(content="Generated content")
        )
        adapter_result = adapter.execute(gw_request, _make_config())

        assert adapter_result.success is True
        assert adapter_result.llm_response.content == "Generated content"
