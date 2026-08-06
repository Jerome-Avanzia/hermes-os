"""Ollama provider driver for the LLM Adapter.

Architecture position:
  Execution Gateway → LLM Adapter → Ollama (this module)

This module contains everything Ollama-specific:
  - Payload construction for the /api/chat endpoint
  - HTTP call via httpx
  - Response parsing from the /api/chat response shape

Registering Ollama with the LLM Adapter::

    from hermes.adapters.llm_adapter import LlmAdapter
    from hermes.providers.ollama_driver import OLLAMA_CAPABILITIES, OLLAMA_DRIVER
    from hermes.models.llm_adapter import LLMProvider

    adapter = LlmAdapter()
    adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=OLLAMA_DRIVER)

Nothing in this module knows about the LlmAdapter internals, the
Execution Gateway, the Conductor, or any other kernel component.
The three driver functions and the two constants are the complete
public surface of this module.

Network calls introduced:
  - _call_ollama() makes a POST to {base_url}/api/chat via httpx.

Filesystem writes introduced:
  - None.
"""

from __future__ import annotations

import logging

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


# ── Ollama /api/chat payload builder ──────────────────────────────────────────


def _build_ollama_payload(
    request: LLMRequest,
    config: AdapterConfiguration,
) -> dict:
    """Translate a normalized LLMRequest into an Ollama /api/chat payload.

    Ollama chat format:
      POST /api/chat
      {"model": "...", "messages": [...], "stream": false, "options": {...}}

    format: "json" is added when a structured output schema is present.
    """
    messages: list[dict] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.user_prompt})

    payload: dict = {
        "model": request.model,
        "messages": messages,
        "stream": False,            # streaming disabled; metadata only
        "options": {
            "num_predict": request.max_tokens,
            "temperature": request.temperature,
        },
    }

    if request.structured_output_schema:
        payload["format"] = "json"

    return payload


# ── Ollama /api/chat response parser ──────────────────────────────────────────


def _parse_ollama_response(raw: dict, llm_request: LLMRequest) -> LLMResponse:
    """Translate a raw Ollama /api/chat response into a normalized LLMResponse.

    Ollama response shape:
      {"model": "...", "message": {"role": "assistant", "content": "..."},
       "done": true, "done_reason": "stop",
       "prompt_eval_count": N, "eval_count": M}
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
        streaming_used=False,
        structured_output_used=bool(llm_request.structured_output_schema),
        finish_reason=finish_reason,
        metadata=metadata,
    )


# ── Ollama HTTP caller ─────────────────────────────────────────────────────────


def _call_ollama(endpoint: str, payload: dict, timeout: int, api_key: str) -> dict:
    """Make the POST request to the Ollama /api/chat endpoint.

    Uses httpx (already a project dependency). api_key is unused for Ollama
    (local provider) but accepted for interface uniformity across all drivers.

    Raises:
        httpx.HTTPError: on network or HTTP-level failure (caller catches this)
    """
    logger.info(
        "ollama_driver: calling endpoint=%s model=%s",
        endpoint,
        payload.get("model"),
    )
    with httpx.Client(timeout=float(timeout)) as client:
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        return response.json()


# ── Ollama capability declaration ─────────────────────────────────────────────

#: Declared capabilities for Ollama (local inference, no API key required).
OLLAMA_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.OLLAMA,
    supports_streaming=True,
    supports_structured_output=True,   # via format="json"
    supports_system_prompt=True,
    supports_tool_use=False,
    max_context_tokens=128_000,
    default_model="llama3.2",
    requires_api_key=False,
)

#: ProviderDriver that bundles all Ollama-specific functions.
#: Register with LlmAdapter via:
#:   adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=OLLAMA_DRIVER)
OLLAMA_DRIVER = ProviderDriver(
    build_payload=_build_ollama_payload,
    call_provider=_call_ollama,
    parse_response=_parse_ollama_response,
    endpoint_path="/api/chat",
)


__all__ = [
    "OLLAMA_CAPABILITIES",
    "OLLAMA_DRIVER",
    "_build_ollama_payload",
    "_call_ollama",
    "_parse_ollama_response",
]
