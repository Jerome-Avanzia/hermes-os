"""Tests for the Ollama provider driver — Sprint 66.

Coverage:
  - OllamaMode enum values
  - OllamaEnvConfig frozen dataclass
  - read_ollama_env(): OLLAMA_MODE, OLLAMA_LOCAL_URL, OLLAMA_CLOUD_URL, OLLAMA_API_KEY
  - configure_from_env(): mode → correct driver + capabilities
  - _build_ollama_payload(): streaming flag, structured output, messages construction
  - _call_ollama(): non-streaming POST, auth header, streaming delegation
  - _call_ollama_streaming(): NDJSON chunk collection, auth, complete dict shape
  - _call_ollama_with_retry(): retry on 429/502/503, no retry on 401, Retry-After
  - _parse_ollama_response(): content, tokens, streaming_used, finish_reason
  - Authentication flow: Bearer header present/absent
  - Timeout handling
  - HTTP error handling (400, 401, 429, 500)
  - Provider errors (malformed response)
  - Determinism of request construction
  - LOCAL mode: format:"json" present when schema non-empty
  - CLOUD mode: format:"json" suppressed even when schema present
  - Full end-to-end flow (mocked HTTP): LOCAL and CLOUD

Test strategy:
  - All HTTP calls mocked via unittest.mock.patch or test fakes.
  - No real Ollama instance required.
  - Environment variables isolated per test via monkeypatch.
"""

from __future__ import annotations

import dataclasses
import json
from io import StringIO
from unittest.mock import MagicMock, patch, call

import httpx
import pytest

from hermes.providers.ollama_driver import (
    OLLAMA_CLOUD_CAPABILITIES,
    OLLAMA_CLOUD_DRIVER,
    OLLAMA_LOCAL_CAPABILITIES,
    OLLAMA_LOCAL_DRIVER,
    OllamaEnvConfig,
    OllamaMode,
    _DEFAULT_CLOUD_URL,
    _DEFAULT_LOCAL_URL,
    _CLOUD_RETRY_DELAYS,
    _CLOUD_RETRY_STATUS_CODES,
    _build_ollama_payload,
    _call_ollama,
    _call_ollama_streaming,
    _call_ollama_with_retry,
    _parse_ollama_response,
    configure_from_env,
    make_ollama_driver,
    read_ollama_env,
)
from hermes.adapters.llm_adapter import LlmAdapter, ProviderDriver
from hermes.models.llm_adapter import (
    AdapterConfiguration,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
)


# ── Fixtures and helpers ───────────────────────────────────────────────────────


def _make_llm_request(
    *,
    request_id: str = "req-001",
    model: str = "llama3.2",
    system_prompt: str = "",
    user_prompt: str = "Write a hello world function",
    max_tokens: int = 512,
    temperature: float = 0.0,
    streaming: bool = False,
    structured_output_schema: str = "",
) -> LLMRequest:
    return LLMRequest(
        request_id=request_id,
        provider=LLMProvider.OLLAMA,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        streaming=streaming,
        structured_output_schema=structured_output_schema,
        metadata=(),
    )


def _make_config(
    *,
    provider: LLMProvider = LLMProvider.OLLAMA,
    model: str = "llama3.2",
    base_url: str = "http://localhost:11434",
    api_key: str = "",
    timeout_seconds: int = 30,
    max_tokens: int = 512,
    temperature: float = 0.0,
    streaming: bool = False,
    structured_output_schema: str = "",
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


def _make_ollama_raw(
    *,
    model: str = "llama3.2",
    content: str = "def hello():\n    return 'Hello, World!'",
    done_reason: str = "stop",
    prompt_eval_count: int = 10,
    eval_count: int = 20,
) -> dict:
    return {
        "model": model,
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "total_duration": 1234567890,
        "load_duration": 12345678,
        "eval_duration": 1000000000,
    }


def _make_httpx_response(
    status_code: int = 200,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> MagicMock:
    """Build a mock httpx.Response."""
    m = MagicMock(spec=httpx.Response)
    m.status_code = status_code
    m.headers = httpx.Headers(headers or {})
    m.json.return_value = json_body or {}
    if status_code >= 400:
        m.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=m,
        )
    else:
        m.raise_for_status.return_value = None
    return m


def _ndjson_lines(*chunks: dict) -> list[str]:
    """Convert dicts to NDJSON string lines for streaming mock."""
    return [json.dumps(c) for c in chunks]


# ══════════════════════════════════════════════════════════════════════════════
# 1. OllamaMode enum
# ══════════════════════════════════════════════════════════════════════════════


class TestOllamaMode:
    def test_local_value(self):
        assert OllamaMode.LOCAL.value == "local"

    def test_cloud_value(self):
        assert OllamaMode.CLOUD.value == "cloud"

    def test_two_values(self):
        assert len(OllamaMode) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 2. OllamaEnvConfig dataclass
# ══════════════════════════════════════════════════════════════════════════════


class TestOllamaEnvConfig:
    def test_frozen(self):
        cfg = OllamaEnvConfig(
            mode=OllamaMode.LOCAL,
            base_url="http://localhost:11434",
            api_key="",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.mode = OllamaMode.CLOUD  # type: ignore[misc]

    def test_slots(self):
        cfg = OllamaEnvConfig(
            mode=OllamaMode.LOCAL,
            base_url="http://localhost:11434",
            api_key="",
        )
        assert not hasattr(cfg, "__dict__")

    def test_fields(self):
        cfg = OllamaEnvConfig(
            mode=OllamaMode.CLOUD,
            base_url="https://ollama.ai",
            api_key="sk-test",
        )
        assert cfg.mode == OllamaMode.CLOUD
        assert cfg.base_url == "https://ollama.ai"
        assert cfg.api_key == "sk-test"

    def test_equality(self):
        cfg1 = OllamaEnvConfig(mode=OllamaMode.LOCAL, base_url="http://localhost:11434", api_key="")
        cfg2 = OllamaEnvConfig(mode=OllamaMode.LOCAL, base_url="http://localhost:11434", api_key="")
        assert cfg1 == cfg2

    def test_default_url_constants_exist(self):
        assert _DEFAULT_LOCAL_URL == "http://localhost:11434"
        assert "ollama" in _DEFAULT_CLOUD_URL.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 3. read_ollama_env()
# ══════════════════════════════════════════════════════════════════════════════


class TestReadOllamaEnv:
    def test_default_mode_is_local(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODE", raising=False)
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.mode == OllamaMode.LOCAL

    def test_mode_cloud_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.mode == OllamaMode.CLOUD

    def test_mode_local_explicit(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.mode == OllamaMode.LOCAL

    def test_mode_case_insensitive_cloud(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "CLOUD")
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.mode == OllamaMode.CLOUD

    def test_mode_case_insensitive_local(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "LOCAL")
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.mode == OllamaMode.LOCAL

    def test_mode_unknown_defaults_to_local(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "docker")
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.mode == OllamaMode.LOCAL

    def test_local_url_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.setenv("OLLAMA_LOCAL_URL", "http://my-ollama:11434")
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.base_url == "http://my-ollama:11434"

    def test_cloud_url_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://custom.ollama.ai")
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.base_url == "https://custom.ollama.ai"

    def test_local_mode_uses_local_url_not_cloud(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.setenv("OLLAMA_LOCAL_URL", "http://local:11434")
        monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://cloud.example.com")
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.base_url == "http://local:11434"

    def test_cloud_mode_uses_cloud_url_not_local(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.setenv("OLLAMA_LOCAL_URL", "http://local:11434")
        monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://cloud.example.com")
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.base_url == "https://cloud.example.com"

    def test_default_local_url(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.base_url == _DEFAULT_LOCAL_URL

    def test_default_cloud_url(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert cfg.base_url == _DEFAULT_CLOUD_URL

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.setenv("OLLAMA_LOCAL_URL", "http://localhost:11434/")
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert not cfg.base_url.endswith("/")

    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "sk-my-api-key")
        monkeypatch.delenv("OLLAMA_MODE", raising=False)
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        cfg = read_ollama_env()
        assert cfg.api_key == "sk-my-api-key"

    def test_api_key_default_empty(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_MODE", raising=False)
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        cfg = read_ollama_env()
        assert cfg.api_key == ""

    def test_returns_ollama_env_config(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODE", raising=False)
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        cfg = read_ollama_env()
        assert isinstance(cfg, OllamaEnvConfig)

    def test_deterministic_for_same_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://ollama.ai")
        monkeypatch.setenv("OLLAMA_API_KEY", "key-abc")
        cfg1 = read_ollama_env()
        cfg2 = read_ollama_env()
        assert cfg1 == cfg2


# ══════════════════════════════════════════════════════════════════════════════
# 4. configure_from_env()
# ══════════════════════════════════════════════════════════════════════════════


class TestConfigureFromEnv:
    def test_local_mode_returns_local_driver_and_caps(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        env, caps, driver = configure_from_env()
        assert env.mode == OllamaMode.LOCAL
        assert caps == OLLAMA_LOCAL_CAPABILITIES
        assert isinstance(driver, ProviderDriver)

    def test_cloud_mode_returns_cloud_driver_and_caps(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        env, caps, driver = configure_from_env()
        assert env.mode == OllamaMode.CLOUD
        assert caps == OLLAMA_CLOUD_CAPABILITIES
        assert isinstance(driver, ProviderDriver)

    def test_local_caps_support_structured_output(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "local")
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        _, caps, _ = configure_from_env()
        assert caps.supports_structured_output is True

    def test_cloud_caps_do_not_support_structured_output(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        _, caps, _ = configure_from_env()
        assert caps.supports_structured_output is False

    def test_cloud_caps_require_api_key(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.delenv("OLLAMA_CLOUD_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        _, caps, _ = configure_from_env()
        assert caps.requires_api_key is True

    def test_returns_tuple_of_three(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODE", raising=False)
        monkeypatch.delenv("OLLAMA_LOCAL_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        result = configure_from_env()
        assert len(result) == 3


# ══════════════════════════════════════════════════════════════════════════════
# 5. _build_ollama_payload()
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildOllamaPayload:
    def _build(self, request, mode=OllamaMode.LOCAL, **config_kwargs):
        config = _make_config(**config_kwargs)
        return _build_ollama_payload(request, config, mode=mode)

    def test_model_in_payload(self):
        req = _make_llm_request(model="mistral")
        payload = self._build(req)
        assert payload["model"] == "mistral"

    def test_user_message_in_messages(self):
        req = _make_llm_request(user_prompt="Hello Ollama")
        payload = self._build(req)
        messages = payload["messages"]
        assert any(m["role"] == "user" and m["content"] == "Hello Ollama" for m in messages)

    def test_system_prompt_present_when_non_empty(self):
        req = _make_llm_request(system_prompt="You are helpful.")
        payload = self._build(req)
        messages = payload["messages"]
        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1]["role"] == "user"

    def test_no_system_message_when_empty(self):
        req = _make_llm_request(system_prompt="")
        payload = self._build(req)
        messages = payload["messages"]
        assert all(m["role"] != "system" for m in messages)
        assert len(messages) == 1

    def test_stream_false_when_not_requested(self):
        req = _make_llm_request(streaming=False)
        payload = self._build(req)
        assert payload["stream"] is False

    def test_stream_true_when_requested(self):
        req = _make_llm_request(streaming=True)
        payload = self._build(req)
        assert payload["stream"] is True

    def test_options_num_predict(self):
        req = _make_llm_request(max_tokens=1024)
        payload = self._build(req)
        assert payload["options"]["num_predict"] == 1024

    def test_options_temperature(self):
        req = _make_llm_request(temperature=0.7)
        payload = self._build(req)
        assert payload["options"]["temperature"] == 0.7

    def test_format_json_present_in_local_with_schema(self):
        req = _make_llm_request(structured_output_schema='{"type":"object"}')
        payload = self._build(req, mode=OllamaMode.LOCAL)
        assert payload.get("format") == "json"

    def test_format_json_absent_in_cloud_with_schema(self):
        req = _make_llm_request(structured_output_schema='{"type":"object"}')
        payload = self._build(req, mode=OllamaMode.CLOUD)
        assert "format" not in payload

    def test_format_json_absent_in_local_without_schema(self):
        req = _make_llm_request(structured_output_schema="")
        payload = self._build(req, mode=OllamaMode.LOCAL)
        assert "format" not in payload

    def test_deterministic_output(self):
        req = _make_llm_request()
        config = _make_config()
        p1 = _build_ollama_payload(req, config, mode=OllamaMode.LOCAL)
        p2 = _build_ollama_payload(req, config, mode=OllamaMode.LOCAL)
        assert p1 == p2

    def test_messages_order_system_then_user(self):
        req = _make_llm_request(system_prompt="System.", user_prompt="User.")
        payload = self._build(req)
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"


# ══════════════════════════════════════════════════════════════════════════════
# 6. _parse_ollama_response()
# ══════════════════════════════════════════════════════════════════════════════


class TestParseOllamaResponse:
    def test_content_extracted(self):
        raw = _make_ollama_raw(content="Generated text")
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.content == "Generated text"

    def test_model_from_response(self):
        raw = _make_ollama_raw(model="mistral")
        req = _make_llm_request(model="llama3.2")
        resp = _parse_ollama_response(raw, req)
        assert resp.model == "mistral"

    def test_model_falls_back_to_request(self):
        raw = _make_ollama_raw()
        raw.pop("model")
        req = _make_llm_request(model="fallback-model")
        resp = _parse_ollama_response(raw, req)
        assert resp.model == "fallback-model"

    def test_input_tokens_from_prompt_eval_count(self):
        raw = _make_ollama_raw(prompt_eval_count=42)
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.input_tokens == 42

    def test_output_tokens_from_eval_count(self):
        raw = _make_ollama_raw(eval_count=100)
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.output_tokens == 100

    def test_total_tokens_sum(self):
        raw = _make_ollama_raw(prompt_eval_count=10, eval_count=20)
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.total_tokens == 30

    def test_finish_reason_stop(self):
        raw = _make_ollama_raw(done_reason="stop")
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.finish_reason == "stop"

    def test_finish_reason_length(self):
        raw = _make_ollama_raw(done_reason="length")
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.finish_reason == "length"

    def test_finish_reason_incomplete_when_not_done(self):
        raw = _make_ollama_raw()
        raw["done"] = False
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.finish_reason == "incomplete"

    def test_provider_is_ollama(self):
        raw = _make_ollama_raw()
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.provider == LLMProvider.OLLAMA

    def test_request_id_propagated(self):
        raw = _make_ollama_raw()
        req = _make_llm_request(request_id="test-req-id")
        resp = _parse_ollama_response(raw, req)
        assert resp.request_id == "test-req-id"

    def test_streaming_used_false_when_not_streaming(self):
        raw = _make_ollama_raw()
        req = _make_llm_request(streaming=False)
        resp = _parse_ollama_response(raw, req)
        assert resp.streaming_used is False

    def test_streaming_used_true_when_streaming(self):
        """Sprint 66: streaming_used reflects whether streaming was requested."""
        raw = _make_ollama_raw()
        req = _make_llm_request(streaming=True)
        resp = _parse_ollama_response(raw, req)
        assert resp.streaming_used is True

    def test_structured_output_used_true_when_schema(self):
        raw = _make_ollama_raw()
        req = _make_llm_request(structured_output_schema='{"type":"object"}')
        resp = _parse_ollama_response(raw, req)
        assert resp.structured_output_used is True

    def test_structured_output_used_false_when_no_schema(self):
        raw = _make_ollama_raw()
        req = _make_llm_request(structured_output_schema="")
        resp = _parse_ollama_response(raw, req)
        assert resp.structured_output_used is False

    def test_metadata_includes_timing_fields(self):
        raw = _make_ollama_raw()
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        meta_keys = [k for k, _ in resp.metadata]
        assert "total_duration" in meta_keys
        assert "eval_duration" in meta_keys

    def test_metadata_is_sorted(self):
        raw = _make_ollama_raw()
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        keys = [k for k, _ in resp.metadata]
        assert keys == sorted(keys)

    def test_missing_token_counts_default_to_zero(self):
        raw = {"model": "llama3.2", "message": {"content": "hi"}, "done": True}
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0

    def test_returns_llm_response(self):
        raw = _make_ollama_raw()
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        assert isinstance(resp, LLMResponse)

    def test_response_is_frozen(self):
        raw = _make_ollama_raw()
        req = _make_llm_request()
        resp = _parse_ollama_response(raw, req)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            resp.content = "changed"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# 7. _call_ollama() — non-streaming HTTP POST
# ══════════════════════════════════════════════════════════════════════════════


class TestCallOllama:
    def _mock_client(self, response_json: dict, status: int = 200):
        """Build a mock httpx.Client context manager."""
        mock_response = MagicMock()
        mock_response.json.return_value = response_json
        if status >= 400:
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                message=f"HTTP {status}",
                request=MagicMock(),
                response=MagicMock(status_code=status, headers=httpx.Headers({})),
            )
        else:
            mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        return mock_client

    def test_post_to_endpoint(self):
        raw = _make_ollama_raw()
        mock_client = self._mock_client(raw)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            result = _call_ollama("http://localhost:11434/api/chat", {"model": "llama3.2"}, 30, "")
        mock_client.post.assert_called_once()
        assert mock_client.post.call_args[0][0] == "http://localhost:11434/api/chat"

    def test_returns_json_dict(self):
        raw = _make_ollama_raw()
        mock_client = self._mock_client(raw)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            result = _call_ollama("http://localhost:11434/api/chat", {"model": "llama3.2"}, 30, "")
        assert result == raw

    def test_auth_header_added_when_api_key(self):
        raw = _make_ollama_raw()
        mock_client = self._mock_client(raw)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            _call_ollama("http://localhost:11434/api/chat", {"model": "x"}, 30, "sk-test")
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"

    def test_no_auth_header_when_no_api_key(self):
        raw = _make_ollama_raw()
        mock_client = self._mock_client(raw)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            _call_ollama("http://localhost:11434/api/chat", {"model": "x"}, 30, "")
        _, kwargs = mock_client.post.call_args
        assert "Authorization" not in kwargs.get("headers", {})

    def test_timeout_passed_to_client(self):
        raw = _make_ollama_raw()
        mock_client = self._mock_client(raw)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client) as MockClient:
            _call_ollama("http://localhost:11434/api/chat", {"model": "x"}, 60, "")
        MockClient.assert_called_once_with(timeout=60.0, follow_redirects=True)

    def test_raises_on_http_error(self):
        mock_client = self._mock_client({}, status=500)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            with pytest.raises(httpx.HTTPStatusError):
                _call_ollama("http://localhost:11434/api/chat", {"model": "x"}, 30, "")

    def test_delegates_to_streaming_when_stream_true(self):
        payload = {"model": "llama3.2", "stream": True}
        with patch("hermes.providers.ollama_driver._call_ollama_streaming") as mock_stream:
            mock_stream.return_value = _make_ollama_raw()
            _call_ollama("http://localhost:11434/api/chat", payload, 30, "")
        mock_stream.assert_called_once_with("http://localhost:11434/api/chat", payload, 30, "")

    def test_does_not_delegate_when_stream_false(self):
        raw = _make_ollama_raw()
        mock_client = self._mock_client(raw)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            with patch("hermes.providers.ollama_driver._call_ollama_streaming") as mock_stream:
                _call_ollama("http://localhost:11434/api/chat", {"model": "x", "stream": False}, 30, "")
        mock_stream.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 8. _call_ollama_streaming() — NDJSON chunk collection
# ══════════════════════════════════════════════════════════════════════════════


class TestCallOllamaStreaming:
    def _make_streaming_mock(self, chunks: list[dict], status: int = 200):
        """Build a mock for httpx.Client streaming context manager."""
        lines = [json.dumps(c) for c in chunks]

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_response
        return mock_client

    def _call(self, chunks, api_key="", endpoint="http://localhost:11434/api/chat", timeout=30):
        mock_client = self._make_streaming_mock(chunks)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            return _call_ollama_streaming(endpoint, {"model": "llama3.2", "stream": True}, timeout, api_key)

    def test_returns_dict(self):
        chunks = [
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " world"}, "done": True, "done_reason": "stop",
             "model": "llama3.2", "prompt_eval_count": 5, "eval_count": 3,
             "total_duration": 100, "load_duration": 10, "eval_duration": 80},
        ]
        result = self._call(chunks)
        assert isinstance(result, dict)

    def test_content_accumulated_from_chunks(self):
        chunks = [
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " world"}, "done": False},
            {"message": {"content": "!"}, "done": True, "done_reason": "stop",
             "model": "llama3.2", "prompt_eval_count": 5, "eval_count": 10,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        result = self._call(chunks)
        assert result["message"]["content"] == "Hello world!"

    def test_done_is_true_in_result(self):
        chunks = [
            {"message": {"content": "Hi"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "model": "llama3.2", "prompt_eval_count": 2, "eval_count": 1,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        result = self._call(chunks)
        assert result["done"] is True

    def test_token_counts_from_final_chunk(self):
        chunks = [
            {"message": {"content": "text"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "model": "llama3.2", "prompt_eval_count": 15, "eval_count": 25,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        result = self._call(chunks)
        assert result["prompt_eval_count"] == 15
        assert result["eval_count"] == 25

    def test_model_from_final_chunk(self):
        chunks = [
            {"message": {"content": "text"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "model": "kimi-k2.7-code", "prompt_eval_count": 0, "eval_count": 0,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        result = self._call(chunks)
        assert result["model"] == "kimi-k2.7-code"

    def test_done_reason_from_final_chunk(self):
        chunks = [
            {"message": {"content": "text"}, "done": True, "done_reason": "length",
             "model": "llama3.2", "prompt_eval_count": 0, "eval_count": 0,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        result = self._call(chunks)
        assert result["done_reason"] == "length"

    def test_auth_header_added_when_api_key(self):
        chunks = [
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "model": "x", "prompt_eval_count": 0, "eval_count": 0,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        mock_client = self._make_streaming_mock(chunks)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            _call_ollama_streaming(
                "http://localhost:11434/api/chat",
                {"model": "x", "stream": True},
                30,
                "sk-secret",
            )
        _, kwargs = mock_client.stream.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-secret"

    def test_no_auth_header_when_no_api_key(self):
        chunks = [
            {"message": {"content": ""}, "done": True, "done_reason": "stop",
             "model": "x", "prompt_eval_count": 0, "eval_count": 0,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        mock_client = self._make_streaming_mock(chunks)
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            _call_ollama_streaming(
                "http://localhost:11434/api/chat",
                {"model": "x", "stream": True},
                30,
                "",
            )
        _, kwargs = mock_client.stream.call_args
        assert "Authorization" not in kwargs.get("headers", {})

    def test_result_message_has_assistant_role(self):
        chunks = [
            {"message": {"content": "hi"}, "done": True, "done_reason": "stop",
             "model": "x", "prompt_eval_count": 0, "eval_count": 0,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        result = self._call(chunks)
        assert result["message"]["role"] == "assistant"

    def test_skips_empty_lines(self):
        """Empty lines in NDJSON stream are ignored."""
        chunks = [
            {"message": {"content": "hello"}, "done": False},
            {"message": {"content": " world"}, "done": True, "done_reason": "stop",
             "model": "x", "prompt_eval_count": 0, "eval_count": 0,
             "total_duration": 0, "load_duration": 0, "eval_duration": 0},
        ]
        lines = ["", json.dumps(chunks[0]), "", json.dumps(chunks[1])]
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.iter_lines.return_value = iter(lines)
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_response
        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            result = _call_ollama_streaming(
                "http://localhost:11434/api/chat",
                {"model": "x", "stream": True},
                30,
                "",
            )
        assert result["message"]["content"] == "hello world"


# ══════════════════════════════════════════════════════════════════════════════
# 9. _call_ollama_with_retry() — retry policy
# ══════════════════════════════════════════════════════════════════════════════


class TestCallOllamaWithRetry:
    def _http_status_error(self, status: int, retry_after: str | None = None):
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        mock_response = MagicMock()
        mock_response.status_code = status
        mock_response.headers = httpx.Headers(headers)
        return httpx.HTTPStatusError(
            message=f"HTTP {status}",
            request=MagicMock(),
            response=mock_response,
        )

    def test_success_on_first_attempt(self):
        raw = _make_ollama_raw()
        with patch(
            "hermes.providers.ollama_driver._call_ollama",
            return_value=raw,
        ) as mock_call:
            result = _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        assert result == raw
        assert mock_call.call_count == 1

    def test_retries_on_429(self):
        raw = _make_ollama_raw()
        calls = [self._http_status_error(429), raw]
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=calls):
            with patch("hermes.providers.ollama_driver.time.sleep"):
                result = _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        assert result == raw

    def test_retries_on_502(self):
        raw = _make_ollama_raw()
        calls = [self._http_status_error(502), raw]
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=calls):
            with patch("hermes.providers.ollama_driver.time.sleep"):
                result = _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        assert result == raw

    def test_retries_on_503(self):
        raw = _make_ollama_raw()
        calls = [self._http_status_error(503), raw]
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=calls):
            with patch("hermes.providers.ollama_driver.time.sleep"):
                result = _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        assert result == raw

    def test_does_not_retry_on_400(self):
        err = self._http_status_error(400)
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=err):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        assert exc_info.value.response.status_code == 400

    def test_does_not_retry_on_401_invalid_api_key(self):
        err = self._http_status_error(401)
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=err):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "bad-key")
        assert exc_info.value.response.status_code == 401

    def test_does_not_retry_on_403(self):
        err = self._http_status_error(403)
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=err):
            with pytest.raises(httpx.HTTPStatusError):
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "")

    def test_does_not_retry_on_404(self):
        err = self._http_status_error(404)
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=err):
            with pytest.raises(httpx.HTTPStatusError):
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "")

    def test_raises_after_exhausting_retries(self):
        err = self._http_status_error(429)
        with patch(
            "hermes.providers.ollama_driver._call_ollama",
            side_effect=[err, err, err, err],
        ):
            with patch("hermes.providers.ollama_driver.time.sleep"):
                with pytest.raises(httpx.HTTPStatusError):
                    _call_ollama_with_retry("http://x/api/chat", {}, 30, "")

    def test_max_three_attempts(self):
        err = self._http_status_error(429)
        with patch(
            "hermes.providers.ollama_driver._call_ollama",
            side_effect=[err, err, err, err, err],
        ) as mock_call:
            with patch("hermes.providers.ollama_driver.time.sleep"):
                with pytest.raises(httpx.HTTPStatusError):
                    _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        # 1 initial + 2 retries = 3 total attempts (delays list has 3 entries)
        assert mock_call.call_count == len(_CLOUD_RETRY_DELAYS) + 1

    def test_respects_retry_after_header(self):
        err = self._http_status_error(429, retry_after="5")
        raw = _make_ollama_raw()
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=[err, raw]):
            with patch("hermes.providers.ollama_driver.time.sleep") as mock_sleep:
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        mock_sleep.assert_called_once_with(5.0)

    def test_uses_exponential_backoff_without_retry_after(self):
        err = self._http_status_error(429)
        raw = _make_ollama_raw()
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=[err, raw]):
            with patch("hermes.providers.ollama_driver.time.sleep") as mock_sleep:
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        # First delay is _CLOUD_RETRY_DELAYS[0]
        mock_sleep.assert_called_once_with(_CLOUD_RETRY_DELAYS[0])

    def test_transport_error_not_retried(self):
        err = httpx.ConnectError("Connection refused")
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=err) as mock_call:
            with pytest.raises(httpx.TransportError):
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "")
        assert mock_call.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 10. make_ollama_driver()
# ══════════════════════════════════════════════════════════════════════════════


class TestMakeOllamaDriver:
    def test_returns_provider_driver(self):
        driver = make_ollama_driver(OllamaMode.LOCAL)
        assert isinstance(driver, ProviderDriver)

    def test_endpoint_path_is_api_chat(self):
        driver = make_ollama_driver(OllamaMode.LOCAL)
        assert driver.endpoint_path == "/api/chat"

    def test_cloud_driver_has_retry(self):
        """CLOUD mode call_provider is the retry wrapper."""
        cloud_driver = make_ollama_driver(OllamaMode.CLOUD)
        assert cloud_driver.call_provider is _call_ollama_with_retry

    def test_local_driver_has_no_retry(self):
        """LOCAL mode call_provider is the direct caller."""
        local_driver = make_ollama_driver(OllamaMode.LOCAL)
        assert local_driver.call_provider is _call_ollama

    def test_local_driver_build_payload_sets_format_json_with_schema(self):
        local_driver = make_ollama_driver(OllamaMode.LOCAL)
        req = _make_llm_request(structured_output_schema='{"type":"object"}')
        config = _make_config()
        payload = local_driver.build_payload(req, config)
        assert payload.get("format") == "json"

    def test_cloud_driver_build_payload_suppresses_format_json(self):
        cloud_driver = make_ollama_driver(OllamaMode.CLOUD)
        req = _make_llm_request(structured_output_schema='{"type":"object"}')
        config = _make_config()
        payload = cloud_driver.build_payload(req, config)
        assert "format" not in payload

    def test_parse_response_is_shared(self):
        local_driver = make_ollama_driver(OllamaMode.LOCAL)
        cloud_driver = make_ollama_driver(OllamaMode.CLOUD)
        assert local_driver.parse_response is cloud_driver.parse_response

    def test_pre_built_local_driver_mode(self):
        """OLLAMA_LOCAL_DRIVER uses LOCAL mode — no retry."""
        assert OLLAMA_LOCAL_DRIVER.call_provider is _call_ollama

    def test_pre_built_cloud_driver_mode(self):
        """OLLAMA_CLOUD_DRIVER uses CLOUD mode — with retry."""
        assert OLLAMA_CLOUD_DRIVER.call_provider is _call_ollama_with_retry


# ══════════════════════════════════════════════════════════════════════════════
# 11. Authentication flow
# ══════════════════════════════════════════════════════════════════════════════


class TestAuthentication:
    """Bearer token is added when api_key is present; absent otherwise."""

    def test_bearer_header_added_non_streaming(self):
        raw = _make_ollama_raw()
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            _call_ollama("http://x/api/chat", {"stream": False}, 30, "my-key")

        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer my-key"

    def test_no_bearer_header_when_key_empty_non_streaming(self):
        raw = _make_ollama_raw()
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("hermes.providers.ollama_driver.httpx.Client", return_value=mock_client):
            _call_ollama("http://x/api/chat", {"stream": False}, 30, "")

        _, kwargs = mock_client.post.call_args
        assert "Authorization" not in kwargs.get("headers", {})

    def test_invalid_api_key_raises_401_not_retried(self):
        """A 401 from an invalid key is not retried (client error)."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.headers = httpx.Headers({})
        err = httpx.HTTPStatusError(
            message="HTTP 401", request=MagicMock(), response=mock_response
        )
        with patch("hermes.providers.ollama_driver._call_ollama", side_effect=err):
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                _call_ollama_with_retry("http://x/api/chat", {}, 30, "invalid-key")
        assert exc_info.value.response.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# 12. Timeout handling
# ══════════════════════════════════════════════════════════════════════════════


class TestTimeoutHandling:
    def test_timeout_passed_as_float_to_httpx(self):
        raw = _make_ollama_raw()
        mock_response = MagicMock()
        mock_response.json.return_value = raw
        mock_response.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch(
            "hermes.providers.ollama_driver.httpx.Client", return_value=mock_client
        ) as MockClient:
            _call_ollama("http://x/api/chat", {"stream": False}, 45, "")

        MockClient.assert_called_once_with(timeout=45.0, follow_redirects=True)

    def test_cloud_driver_uses_longer_timeout_when_configured(self):
        """Caller can pass any timeout; CLOUD mode typically uses 120s."""
        raw = _make_ollama_raw()
        adapter = LlmAdapter()
        caller_calls = []

        def mock_call(endpoint, payload, timeout, api_key):
            caller_calls.append(timeout)
            return raw

        driver = ProviderDriver(
            build_payload=make_ollama_driver(OllamaMode.CLOUD).build_payload,
            call_provider=mock_call,
            parse_response=_parse_ollama_response,
            endpoint_path="/api/chat",
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CLOUD_CAPABILITIES, driver=driver)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-t",
            operation_id="op-t",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "hello"),),
        )
        config = _make_config(timeout_seconds=120, base_url="https://ollama.ai")
        adapter.execute(request, config)

        assert caller_calls[0] == 120


# ══════════════════════════════════════════════════════════════════════════════
# 13. Full end-to-end flow through LlmAdapter (mocked HTTP)
# ══════════════════════════════════════════════════════════════════════════════


class TestEndToEndFlow:
    """Prove the full path: LlmAdapter → ProviderDriver → _call_ollama* works."""

    def _make_adapter_with_mock_caller(self, raw_response, mode=OllamaMode.LOCAL):
        adapter = LlmAdapter()
        base_driver = make_ollama_driver(mode)
        mock_driver = ProviderDriver(
            build_payload=base_driver.build_payload,
            call_provider=lambda *args: raw_response,
            parse_response=base_driver.parse_response,
            endpoint_path=base_driver.endpoint_path,
        )
        caps = OLLAMA_LOCAL_CAPABILITIES if mode == OllamaMode.LOCAL else OLLAMA_CLOUD_CAPABILITIES
        adapter.register_provider(LLMProvider.OLLAMA, caps, driver=mock_driver)
        return adapter

    def _make_request(self, prompt="Write hello world"):
        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        return ExecutionRequest(
            request_id="req-e2e",
            operation_id="op-e2e",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", prompt),),
        )

    def test_local_mode_success_path(self):
        raw = _make_ollama_raw(content="def hello(): return 'Hello'")
        adapter = self._make_adapter_with_mock_caller(raw, OllamaMode.LOCAL)
        request = self._make_request()
        config = _make_config(base_url="http://localhost:11434")

        result = adapter.execute(request, config)

        assert result.success is True
        assert result.llm_response.content == "def hello(): return 'Hello'"
        assert result.llm_response.provider == LLMProvider.OLLAMA

    def test_cloud_mode_success_path(self):
        raw = _make_ollama_raw(content="Generated by cloud", model="kimi-k2.7-code")
        adapter = self._make_adapter_with_mock_caller(raw, OllamaMode.CLOUD)
        request = self._make_request()
        config = _make_config(
            base_url="https://ollama.ai",
            api_key="sk-cloud-key",
        )

        result = adapter.execute(request, config)

        assert result.success is True
        assert result.llm_response.content == "Generated by cloud"

    def test_streaming_mode_payload_has_stream_true(self):
        raw = _make_ollama_raw()
        captured_payloads = []

        def capture_call(endpoint, payload, timeout, api_key):
            captured_payloads.append(payload)
            return raw

        adapter = LlmAdapter()
        base_driver = make_ollama_driver(OllamaMode.LOCAL)
        mock_driver = ProviderDriver(
            build_payload=base_driver.build_payload,
            call_provider=capture_call,
            parse_response=base_driver.parse_response,
            endpoint_path=base_driver.endpoint_path,
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_LOCAL_CAPABILITIES, driver=mock_driver)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-s",
            operation_id="op-s",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "hello"),),
        )
        config = _make_config(streaming=True)
        result = adapter.execute(request, config)

        assert result.success is True
        assert captured_payloads[0]["stream"] is True

    def test_streaming_response_has_streaming_used_true(self):
        raw = _make_ollama_raw()
        adapter = self._make_adapter_with_mock_caller(raw, OllamaMode.LOCAL)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-su",
            operation_id="op-su",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "hello"),),
        )
        config = _make_config(streaming=True)
        result = adapter.execute(request, config)

        assert result.llm_response.streaming_used is True

    def test_non_streaming_response_has_streaming_used_false(self):
        raw = _make_ollama_raw()
        adapter = self._make_adapter_with_mock_caller(raw, OllamaMode.LOCAL)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-ns",
            operation_id="op-ns",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "hello"),),
        )
        config = _make_config(streaming=False)
        result = adapter.execute(request, config)

        assert result.llm_response.streaming_used is False

    def test_local_mode_structured_output_in_payload(self):
        raw = _make_ollama_raw()
        captured_payloads = []

        def capture(endpoint, payload, timeout, api_key):
            captured_payloads.append(payload)
            return raw

        adapter = LlmAdapter()
        driver = ProviderDriver(
            build_payload=make_ollama_driver(OllamaMode.LOCAL).build_payload,
            call_provider=capture,
            parse_response=_parse_ollama_response,
            endpoint_path="/api/chat",
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_LOCAL_CAPABILITIES, driver=driver)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-so",
            operation_id="op-so",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "list names"), ("schema", '{"type":"array"}')),
        )
        config = _make_config(structured_output_schema='{"type":"array"}')
        adapter.execute(request, config)

        assert captured_payloads[0].get("format") == "json"

    def test_http_error_captured_as_failure(self):
        adapter = LlmAdapter()
        base_driver = make_ollama_driver(OllamaMode.LOCAL)

        def failing_call(endpoint, payload, timeout, api_key):
            raise httpx.ConnectError("Connection refused")

        mock_driver = ProviderDriver(
            build_payload=base_driver.build_payload,
            call_provider=failing_call,
            parse_response=base_driver.parse_response,
            endpoint_path=base_driver.endpoint_path,
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_LOCAL_CAPABILITIES, driver=mock_driver)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-err",
            operation_id="op-err",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "hello"),),
        )
        config = _make_config()
        result = adapter.execute(request, config)

        assert result.success is False
        assert "ConnectError" in result.error

    def test_malformed_response_captured_as_failure(self):
        """A response that breaks parse_response is captured, not raised."""
        adapter = LlmAdapter()
        base_driver = make_ollama_driver(OllamaMode.LOCAL)

        # Return a response that breaks _parse_ollama_response
        def bad_parse(raw, req):
            raise ValueError("unexpected format")

        mock_driver = ProviderDriver(
            build_payload=base_driver.build_payload,
            call_provider=lambda *_: {},
            parse_response=bad_parse,
            endpoint_path=base_driver.endpoint_path,
        )
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_LOCAL_CAPABILITIES, driver=mock_driver)

        from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
        request = ExecutionRequest(
            request_id="req-mp",
            operation_id="op-mp",
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=(("prompt", "hello"),),
        )
        config = _make_config()
        result = adapter.execute(request, config)

        assert result.success is False
        assert "response_parse_failed" in result.error


# ══════════════════════════════════════════════════════════════════════════════
# 14. Determinism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_same_request_same_payload_local(self):
        req = _make_llm_request(model="llama3.2", user_prompt="hello", temperature=0.0)
        config = _make_config()
        p1 = _build_ollama_payload(req, config, mode=OllamaMode.LOCAL)
        p2 = _build_ollama_payload(req, config, mode=OllamaMode.LOCAL)
        assert p1 == p2

    def test_same_request_same_payload_cloud(self):
        req = _make_llm_request(model="kimi-k2.7-code", user_prompt="hello", temperature=0.0)
        config = _make_config()
        p1 = _build_ollama_payload(req, config, mode=OllamaMode.CLOUD)
        p2 = _build_ollama_payload(req, config, mode=OllamaMode.CLOUD)
        assert p1 == p2

    def test_same_raw_response_same_llm_response(self):
        raw = _make_ollama_raw()
        req = _make_llm_request()
        r1 = _parse_ollama_response(raw, req)
        r2 = _parse_ollama_response(raw, req)
        assert r1 == r2

    def test_env_config_deterministic_for_same_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODE", "cloud")
        monkeypatch.setenv("OLLAMA_CLOUD_URL", "https://ollama.ai")
        monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
        cfg1 = read_ollama_env()
        cfg2 = read_ollama_env()
        assert cfg1 == cfg2


# ══════════════════════════════════════════════════════════════════════════════
# 15. Retry status codes declaration
# ══════════════════════════════════════════════════════════════════════════════


class TestRetryStatusCodes:
    def test_429_in_retry_set(self):
        assert 429 in _CLOUD_RETRY_STATUS_CODES

    def test_502_in_retry_set(self):
        assert 502 in _CLOUD_RETRY_STATUS_CODES

    def test_503_in_retry_set(self):
        assert 503 in _CLOUD_RETRY_STATUS_CODES

    def test_400_not_in_retry_set(self):
        assert 400 not in _CLOUD_RETRY_STATUS_CODES

    def test_401_not_in_retry_set(self):
        assert 401 not in _CLOUD_RETRY_STATUS_CODES

    def test_403_not_in_retry_set(self):
        assert 403 not in _CLOUD_RETRY_STATUS_CODES

    def test_retry_delays_are_positive(self):
        assert all(d > 0 for d in _CLOUD_RETRY_DELAYS)

    def test_retry_delays_are_increasing(self):
        delays = list(_CLOUD_RETRY_DELAYS)
        assert delays == sorted(delays)
