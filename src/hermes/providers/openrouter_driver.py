"""OpenRouter provider driver for the LLM Adapter.

Architecture position:
  Execution Gateway → LLM Adapter → OpenRouter (this module)

Sprint 61 status:
  Capabilities declared. ProviderDriver not yet implemented.
  Calling execute() with LLMProvider.OPENROUTER returns
  AdapterExecutionResult(success=False) — no exception raised.

To enable execution in a future sprint, implement and register a driver::

    OPENROUTER_DRIVER = ProviderDriver(
        build_payload=_build_openrouter_payload,    # OpenAI-compatible format
        call_provider=_call_openrouter,             # Authorization: Bearer header
        parse_response=_parse_openrouter_response,  # choices[0].message.content
        endpoint_path="/api/v1/chat/completions",
    )

    adapter.register_provider(
        LLMProvider.OPENROUTER,
        OPENROUTER_CAPABILITIES,
        driver=OPENROUTER_DRIVER,
    )
"""

from __future__ import annotations

from hermes.models.llm_adapter import LLMProvider, ProviderCapabilities


#: Declared capabilities for OpenRouter.
#: API key required. Routes to multiple upstream providers.
OPENROUTER_CAPABILITIES = ProviderCapabilities(
    provider=LLMProvider.OPENROUTER,
    supports_streaming=True,
    supports_structured_output=False,
    supports_system_prompt=True,
    supports_tool_use=False,
    max_context_tokens=128_000,
    default_model="openai/gpt-4o",
    requires_api_key=True,
)


__all__ = ["OPENROUTER_CAPABILITIES"]
