"""Anthropic provider driver for the LLM Adapter.

Architecture position:
  Execution Gateway → LLM Adapter → Anthropic (this module)

Sprint 61 status:
  Capabilities declared. ProviderDriver not yet implemented.
  Calling execute() with LLMProvider.ANTHROPIC returns
  AdapterExecutionResult(success=False) — no exception raised.

To enable execution in a future sprint, implement and register a driver::

    ANTHROPIC_DRIVER = ProviderDriver(
        build_payload=_build_anthropic_payload,    # Messages API format
        call_provider=_call_anthropic,             # Authorization: Bearer header
        parse_response=_parse_anthropic_response,  # raw["content"][0]["text"]
        endpoint_path="/v1/messages",
    )

    adapter.register_provider(
        LLMProvider.ANTHROPIC,
        ANTHROPIC_CAPABILITIES,
        driver=ANTHROPIC_DRIVER,
    )
"""

from __future__ import annotations

from hermes.models.llm_adapter import LLMProvider, ProviderCapabilities


#: Declared capabilities for the Anthropic Claude API.
#: API key required. Supports tool use and structured output.
ANTHROPIC_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.ANTHROPIC,
    supports_streaming=True,
    supports_structured_output=True,
    supports_system_prompt=True,
    supports_tool_use=True,
    max_context_tokens=200_000,
    default_model="claude-opus-4-6",
    requires_api_key=True,
)


__all__ = ["ANTHROPIC_CAPABILITIES"]
