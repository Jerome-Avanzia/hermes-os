"""OpenAI provider driver for the LLM Adapter.

Architecture position:
  Execution Gateway → LLM Adapter → OpenAI (this module)

Sprint 61 status:
  Capabilities declared. ProviderDriver not yet implemented.
  Calling execute() with LLMProvider.OPENAI returns
  AdapterExecutionResult(success=False) — no exception raised.

To enable execution in a future sprint, implement and register a driver::

    OPENAI_DRIVER = ProviderDriver(
        build_payload=_build_openai_payload,    # /v1/chat/completions format
        call_provider=_call_openai,             # Authorization: Bearer header
        parse_response=_parse_openai_response,  # choices[0].message.content
        endpoint_path="/v1/chat/completions",
    )

    adapter.register_provider(
        LLMProvider.OPENAI,
        OPENAI_CAPABILITIES,
        driver=OPENAI_DRIVER,
    )
"""

from __future__ import annotations

from hermes.models.llm_adapter import LLMProvider, ProviderCapabilities


#: Declared capabilities for the OpenAI API.
#: API key required. Supports tool use and structured output.
OPENAI_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.OPENAI,
    supports_streaming=True,
    supports_structured_output=True,
    supports_system_prompt=True,
    supports_tool_use=True,
    max_context_tokens=128_000,
    default_model="gpt-4o",
    requires_api_key=True,
)


__all__ = ["OPENAI_CAPABILITIES"]
