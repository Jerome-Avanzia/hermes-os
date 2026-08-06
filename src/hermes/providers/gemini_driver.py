"""Google Gemini provider driver for the LLM Adapter.

Architecture position:
  Execution Gateway → LLM Adapter → Gemini (this module)

Sprint 61 status:
  Capabilities declared. ProviderDriver not yet implemented.
  Calling execute() with LLMProvider.GEMINI returns
  AdapterExecutionResult(success=False) — no exception raised.

To enable execution in a future sprint, implement and register a driver::

    GEMINI_DRIVER = ProviderDriver(
        build_payload=_build_gemini_payload,    # generateContent API format
        call_provider=_call_gemini,             # ?key={api_key} query param
        parse_response=_parse_gemini_response,  # candidates[0].content.parts[0].text
        endpoint_path="/v1beta/models/{model}:generateContent",
    )

    adapter.register_provider(
        LLMProvider.GEMINI,
        GEMINI_CAPABILITIES,
        driver=GEMINI_DRIVER,
    )
"""

from __future__ import annotations

from hermes.models.llm_adapter import LLMProvider, ProviderCapabilities


#: Declared capabilities for the Google Gemini API.
#: API key required. Largest declared context window (1M tokens).
GEMINI_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.GEMINI,
    supports_streaming=True,
    supports_structured_output=True,
    supports_system_prompt=True,
    supports_tool_use=True,
    max_context_tokens=1_000_000,
    default_model="gemini-2.0-flash",
    requires_api_key=True,
)


__all__ = ["GEMINI_CAPABILITIES"]
