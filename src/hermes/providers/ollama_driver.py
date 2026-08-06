"""Ollama provider driver for the LLM Adapter.

Architecture position:
  Execution Gateway → LLM Adapter → Ollama (this module)

One driver, two modes. Ollama Cloud uses the same /api/chat API as local
Ollama. Transport and model identity are independent concerns:

  Transport (LOCAL vs CLOUD) → declared via OllamaMode at driver construction
  Authentication             → api_key in AdapterConfiguration (independent)
  Model identity             → plain model name, no suffix encoding

OllamaMode drives three behavioral differences:
  LOCAL → includes format:"json" when structured_output_schema is present
  CLOUD → omits format:"json" (Ollama Cloud does not support structured output)
  CLOUD → uses exponential-backoff retry for transient failures (429/502/503)
  BOTH  → add Authorization: Bearer header when api_key is non-empty
  BOTH  → use HTTP streaming when config.streaming=True (NDJSON collection)

Registering with the LLM Adapter::

    from hermes.adapters.llm_adapter import LlmAdapter
    from hermes.providers.ollama_driver import (
        OLLAMA_LOCAL_CAPABILITIES, OLLAMA_LOCAL_DRIVER,
        OLLAMA_CLOUD_CAPABILITIES, OLLAMA_CLOUD_DRIVER,
    )
    from hermes.models.llm_adapter import LLMProvider

    # Local deployment
    adapter.register_provider(
        LLMProvider.OLLAMA, OLLAMA_LOCAL_CAPABILITIES, driver=OLLAMA_LOCAL_DRIVER
    )

    # Cloud deployment (replaces local registration — first-wins)
    adapter.register_provider(
        LLMProvider.OLLAMA, OLLAMA_CLOUD_CAPABILITIES, driver=OLLAMA_CLOUD_DRIVER
    )

Environment variables (read by read_ollama_env() — infrastructure only):
    OLLAMA_MODE      → "local" or "cloud" (case-insensitive; default: "local")
    OLLAMA_LOCAL_URL → local endpoint     (default: http://localhost:11434)
    OLLAMA_CLOUD_URL → cloud endpoint     (default: https://ollama.com)
    OLLAMA_API_KEY   → Bearer token for authentication (default: "")

    Model selection belongs to the ModelRouter — not these environment variables.

Network calls introduced:
  - _call_ollama() makes a POST to {base_url}/api/chat via httpx (non-streaming).
  - _call_ollama_streaming() makes a streaming POST via httpx.Client.stream()
    and collects NDJSON chunks into a complete response dict.

Filesystem writes introduced:
  - None.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import time
from dataclasses import dataclass

import httpx

from hermes.adapters.llm_adapter import ProviderDriver
from hermes.models.llm_adapter import (
    AdapterConfiguration,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)


# ── Default endpoints ──────────────────────────────────────────────────────────

_DEFAULT_LOCAL_URL: str = "http://localhost:11434"
_DEFAULT_CLOUD_URL: str = "https://ollama.com"


# ── OllamaMode ─────────────────────────────────────────────────────────────────


class OllamaMode(enum.Enum):
    """Explicit transport declaration for the Ollama driver.

    Transport and authentication are independent concerns:
      - OllamaMode declares WHERE requests are sent (local hardware vs cloud)
      - api_key in AdapterConfiguration declares HOW to authenticate

    LOCAL → targets a local Ollama instance (http://localhost:11434 by default)
            Supports structured output (format:"json"), vision, tool calling.
            api_key is empty for unauthenticated local instances; may be
            non-empty if the local instance has authentication enabled.

    CLOUD → targets Ollama Cloud (https://ollama.com by default)
            Structured output not supported (format:"json" is suppressed).
            Vision not supported. Tool calling supported.
            api_key is expected; absence will result in a 401 from the API.
    """

    LOCAL = "local"
    CLOUD = "cloud"


# ── OllamaEnvConfig ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OllamaEnvConfig:
    """Environment-derived Ollama configuration.

    Produced by read_ollama_env(). Carries the three infrastructure values
    needed to configure an AdapterConfiguration for Ollama:

      mode     → OllamaMode.LOCAL or OllamaMode.CLOUD
      base_url → resolved endpoint URL (trailing slash stripped)
      api_key  → Bearer token, or "" for unauthenticated local instances

    Immutable after construction. Model selection is NOT included — model
    selection belongs to the ModelRouter, not infrastructure configuration.
    """

    mode: OllamaMode
    base_url: str
    api_key: str


def read_ollama_env() -> OllamaEnvConfig:
    """Read Ollama configuration from environment variables.

    Environment variables (Sprint 66):
        OLLAMA_MODE      → "local" or "cloud" (case-insensitive; default: "local")
        OLLAMA_LOCAL_URL → base URL for LOCAL mode (default: http://localhost:11434)
        OLLAMA_CLOUD_URL → base URL for CLOUD mode (default: https://ollama.com)
        OLLAMA_API_KEY   → Bearer token for Authorization header (default: "")

    Returns:
        OllamaEnvConfig with mode, base_url, and api_key populated from env.
        Trailing slashes are stripped from URLs.
    """
    mode_str = os.environ.get("OLLAMA_MODE", "local").strip().lower()
    mode = OllamaMode.CLOUD if mode_str == "cloud" else OllamaMode.LOCAL

    if mode == OllamaMode.LOCAL:
        base_url = os.environ.get("OLLAMA_LOCAL_URL", _DEFAULT_LOCAL_URL).rstrip("/")
    else:
        base_url = os.environ.get("OLLAMA_CLOUD_URL", _DEFAULT_CLOUD_URL).rstrip("/")

    api_key = os.environ.get("OLLAMA_API_KEY", "")

    return OllamaEnvConfig(mode=mode, base_url=base_url, api_key=api_key)


def configure_from_env() -> tuple[OllamaEnvConfig, ProviderCapabilities, ProviderDriver]:
    """Read env vars and return a complete Ollama configuration tuple.

    Convenience function for bootstrap/startup code. Combines read_ollama_env()
    with the appropriate capabilities and driver for the detected mode.

    Usage::

        env, caps, driver = configure_from_env()
        adapter.register_provider(LLMProvider.OLLAMA, caps, driver=driver)
        config = AdapterConfiguration(
            provider=LLMProvider.OLLAMA,
            model="kimi-k2.7-code",
            base_url=env.base_url,
            api_key=env.api_key,
            timeout_seconds=120,
        )

    Returns:
        Tuple of (OllamaEnvConfig, ProviderCapabilities, ProviderDriver).
        The ProviderDriver and ProviderCapabilities match the detected mode.
    """
    env = read_ollama_env()
    if env.mode == OllamaMode.CLOUD:
        return env, OLLAMA_CLOUD_CAPABILITIES, OLLAMA_CLOUD_DRIVER
    return env, OLLAMA_LOCAL_CAPABILITIES, OLLAMA_LOCAL_DRIVER


# ── Ollama /api/chat payload builder ──────────────────────────────────────────


def _build_ollama_payload(
    request: LLMRequest,
    config: AdapterConfiguration,
    *,
    mode: OllamaMode,
) -> dict:
    """Translate a normalized LLMRequest into an Ollama /api/chat payload.

    Ollama chat format:
      POST /api/chat
      {"model": "...", "messages": [...], "stream": bool, "options": {...}}

    Streaming:
      "stream" is set to True when request.streaming is True. When True,
      _call_ollama() delegates to _call_ollama_streaming() which collects
      NDJSON chunks and returns a complete response dict — transparent to
      the caller.

    Structured output:
      format:"json" is added when a structured output schema is present AND
      mode is LOCAL. Ollama Cloud does not support structured output — the
      field is suppressed in CLOUD mode to avoid API errors.

    Args:
        request: The normalized LLMRequest.
        config:  The AdapterConfiguration for this invocation.
        mode:    OllamaMode.LOCAL or OllamaMode.CLOUD — controls structured
                 output field inclusion. Keyword-only.
    """
    messages: list[dict] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.user_prompt})

    payload: dict = {
        "model": request.model,
        "messages": messages,
        "stream": request.streaming,
        "options": {
            "num_predict": request.max_tokens,
            "temperature": request.temperature,
        },
    }

    if request.structured_output_schema and mode == OllamaMode.LOCAL:
        # Ollama Cloud does not support format:"json" — suppress in CLOUD mode
        payload["format"] = "json"

    return payload


# ── Ollama /api/chat response parser ──────────────────────────────────────────


def _parse_ollama_response(raw: dict, llm_request: LLMRequest) -> LLMResponse:
    """Translate a raw Ollama /api/chat response into a normalized LLMResponse.

    The response shape is identical for local and cloud Ollama, and is also
    identical for streaming (collected) and non-streaming responses since
    _call_ollama_streaming() reconstructs a complete dict from NDJSON chunks:

      {"model": "...", "message": {"role": "assistant", "content": "..."},
       "done": true, "done_reason": "stop",
       "prompt_eval_count": N, "eval_count": M}

    streaming_used reflects whether HTTP streaming was requested (and used).
    When llm_request.streaming=True, _call_ollama_streaming() was used
    internally and streaming_used is reported as True.
    """
    message = raw.get("message", {})
    content = message.get("content", "")

    input_tokens = int(raw.get("prompt_eval_count", 0))
    output_tokens = int(raw.get("eval_count", 0))

    finish_reason = raw.get("done_reason", "stop") if raw.get("done") else "incomplete"

    meta_items: list[tuple[str, str]] = []
    for key in ("total_duration", "load_duration", "eval_duration"):
        if key in raw:
            meta_items.append((key, str(raw[key])))
    metadata = tuple(sorted(meta_items))

    return LLMResponse(
        request_id=llm_request.request_id,
        provider=LLMProvider.OLLAMA,
        model=raw.get("model", llm_request.model),
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        streaming_used=llm_request.streaming,
        structured_output_used=bool(llm_request.structured_output_schema),
        finish_reason=finish_reason,
        metadata=metadata,
    )


# ── Ollama HTTP callers ────────────────────────────────────────────────────────


def _call_ollama_streaming(
    endpoint: str,
    payload: dict,
    timeout: int,
    api_key: str,
) -> dict:
    """Use HTTP streaming to collect Ollama NDJSON chunks into a complete dict.

    Ollama streaming returns newline-delimited JSON (NDJSON) where each line
    is a partial response chunk. The final chunk has done=True and carries
    token counts and timing metadata.

    Collects all content from each chunk and assembles a response dict with
    the same shape as the non-streaming Ollama response, so _parse_ollama_response
    can process both paths identically.

    Adds an Authorization: Bearer header when api_key is non-empty.

    Raises:
        httpx.HTTPError: on network or HTTP-level failure (caller catches this)
    """
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info(
        "ollama_driver: streaming endpoint=%s model=%s auth=%s",
        endpoint,
        payload.get("model"),
        "yes" if api_key else "no",
    )

    content_parts: list[str] = []
    final_chunk: dict = {}

    with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
        with client.stream("POST", endpoint, json=payload, headers=headers) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                msg_content = chunk.get("message", {}).get("content", "")
                if msg_content:
                    content_parts.append(msg_content)
                if chunk.get("done"):
                    final_chunk = chunk
                    break

    # Reconstruct a complete response dict matching the non-streaming shape.
    # _parse_ollama_response processes both paths identically from this point.
    return {
        "model": final_chunk.get("model", payload.get("model", "")),
        "message": {
            "role": "assistant",
            "content": "".join(content_parts),
        },
        "done": True,
        "done_reason": final_chunk.get("done_reason", "stop"),
        "prompt_eval_count": final_chunk.get("prompt_eval_count", 0),
        "eval_count": final_chunk.get("eval_count", 0),
        "total_duration": final_chunk.get("total_duration", 0),
        "load_duration": final_chunk.get("load_duration", 0),
        "eval_duration": final_chunk.get("eval_duration", 0),
    }


def _call_ollama(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
    """Make the POST request to the Ollama /api/chat endpoint.

    Detects streaming: if payload["stream"] is True, delegates to
    _call_ollama_streaming() which collects NDJSON chunks and returns a
    complete response dict with the same shape as a non-streaming response.

    Adds an Authorization: Bearer header when api_key is non-empty.
    Authentication and transport are independent: a local instance may
    require a key; a cloud request always requires one.

    Uses httpx (already a project dependency).

    Raises:
        httpx.HTTPError: on network or HTTP-level failure (caller catches this)
    """
    if payload.get("stream") is True:
        return _call_ollama_streaming(endpoint, payload, timeout, api_key)

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info(
        "ollama_driver: calling endpoint=%s model=%s auth=%s",
        endpoint,
        payload.get("model"),
        "yes" if api_key else "no",
    )

    with httpx.Client(timeout=float(timeout), follow_redirects=True) as client:
        response = client.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


# Retry policy for Cloud mode:
#   Retry on: 429 (rate limit), 502 (bad gateway), 503 (service unavailable)
#   No retry:  400, 401, 403, 404 (client errors — retry cannot help)
#   Attempts:  3 (initial + 2 retries)
#   Backoff:   exponential — 1s, 2s, 4s
#   429:       respect Retry-After header if present; fall back to 4s

_CLOUD_RETRY_STATUS_CODES = frozenset({429, 502, 503})
_CLOUD_RETRY_DELAYS = (1.0, 2.0, 4.0)


def _call_ollama_with_retry(
    endpoint: str,
    payload: dict,
    timeout: int,
    api_key: str,
) -> dict:
    """Make the POST request with exponential-backoff retry for Cloud transient failures.

    Used by OLLAMA_CLOUD_DRIVER only. The retry loop handles the transient
    HTTP conditions that cloud infrastructure introduces (rate limits, gateway
    errors). Local infrastructure failures are immediate and non-retryable.

    Delegates to _call_ollama() for each attempt. When payload["stream"] is
    True, _call_ollama() will use _call_ollama_streaming() — retry applies
    to the complete streaming collection, not to individual chunks.

    Raises:
        httpx.HTTPError: if all retry attempts are exhausted (caller catches this)
    """
    last_exc: Exception | None = None

    for attempt, delay in enumerate((*_CLOUD_RETRY_DELAYS, None)):
        try:
            return _call_ollama(endpoint, payload, timeout, api_key)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _CLOUD_RETRY_STATUS_CODES:
                raise  # client error — do not retry
            last_exc = exc
            if delay is None:
                break  # exhausted
            retry_after = exc.response.headers.get("Retry-After")
            sleep_secs = float(retry_after) if retry_after else delay
            logger.warning(
                "ollama_driver: cloud request failed (status=%d attempt=%d) "
                "retrying in %.1fs",
                exc.response.status_code,
                attempt + 1,
                sleep_secs,
            )
            time.sleep(sleep_secs)
        except httpx.TransportError:
            raise  # network-level errors are not retried

    raise last_exc  # type: ignore[misc]


# ── Driver factory ─────────────────────────────────────────────────────────────


def make_ollama_driver(mode: OllamaMode) -> ProviderDriver:
    """Construct a ProviderDriver with mode-specific behavior.

    The mode is captured in the build_payload closure. call_provider uses
    the base HTTP caller for LOCAL and the retry-wrapped caller for CLOUD.

    Streaming is handled transparently: _call_ollama() detects payload["stream"]
    and delegates to _call_ollama_streaming() for both LOCAL and CLOUD modes.

    Args:
        mode: OllamaMode.LOCAL or OllamaMode.CLOUD

    Returns:
        ProviderDriver ready for registration with LlmAdapter.
    """
    def _build(request: LLMRequest, config: AdapterConfiguration) -> dict:
        return _build_ollama_payload(request, config, mode=mode)

    call_provider = (
        _call_ollama_with_retry if mode == OllamaMode.CLOUD else _call_ollama
    )

    return ProviderDriver(
        build_payload=_build,
        call_provider=call_provider,
        parse_response=_parse_ollama_response,
        endpoint_path="/api/chat",
    )


# ── Capability declarations ────────────────────────────────────────────────────

#: Capabilities for a local Ollama instance.
#: Structured output, vision, and tool calling are all supported locally.
OLLAMA_LOCAL_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.OLLAMA,
    supports_streaming=True,
    supports_structured_output=True,
    supports_system_prompt=True,
    supports_tool_use=True,
    max_context_tokens=128_000,
    default_model="llama3.2",
    requires_api_key=False,
)

#: Capabilities for Ollama Cloud.
#: Structured output and vision are not supported on Ollama Cloud.
#: An API key is required.
OLLAMA_CLOUD_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.OLLAMA,
    supports_streaming=True,
    supports_structured_output=False,   # Ollama Cloud limitation
    supports_system_prompt=True,
    supports_tool_use=True,
    max_context_tokens=128_000,
    default_model="kimi-k2.7-code",
    requires_api_key=True,
)

#: ProviderDriver for local Ollama. Includes format:"json" when schema present.
OLLAMA_LOCAL_DRIVER = make_ollama_driver(OllamaMode.LOCAL)

#: ProviderDriver for Ollama Cloud. Suppresses format:"json". Uses retry.
OLLAMA_CLOUD_DRIVER = make_ollama_driver(OllamaMode.CLOUD)


__all__ = [
    "OLLAMA_CLOUD_CAPABILITIES",
    "OLLAMA_CLOUD_DRIVER",
    "OLLAMA_LOCAL_CAPABILITIES",
    "OLLAMA_LOCAL_DRIVER",
    "OllamaEnvConfig",
    "OllamaMode",
    "_DEFAULT_CLOUD_URL",
    "_DEFAULT_LOCAL_URL",
    "_build_ollama_payload",
    "_call_ollama",
    "_call_ollama_streaming",
    "_call_ollama_with_retry",
    "_parse_ollama_response",
    "configure_from_env",
    "make_ollama_driver",
    "read_ollama_env",
]
