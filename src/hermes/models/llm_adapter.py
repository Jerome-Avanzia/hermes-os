"""LLM Adapter — typed contracts for the LLM provider adapter.

Architecture position:
  Execution Gateway → LLM Adapter → Provider (Ollama, Anthropic, etc.)

The LLM Adapter is NOT the LLM. The adapter is a provider adapter:
  it translates Hermes execution contracts into provider requests and
  translates provider responses back into Hermes execution results.

The adapter is provider-agnostic. Provider-specific translation logic
lives in registered ProviderDrivers, not in the adapter itself.

  Why provider-agnostic?
    Adding any new provider (OpenAI, Gemini, Anthropic, OpenRouter) requires
    only registering a ProviderDriver — three functions and an endpoint path.
    No changes to the adapter, no changes to the Execution Gateway, no changes
    to any engine. The adapter never branches on provider names; it dispatches
    through the registry.

  Where provider-specific code lives:
    ProviderDriver.build_payload    → translates LLMRequest → provider JSON
    ProviderDriver.call_provider    → makes the HTTP request to the provider
    ProviderDriver.parse_response   → translates provider JSON → LLMResponse
    ProviderDriver.endpoint_path    → provider-specific API path

Sprint 61 scope:
  Ollama is the only fully implemented provider. Anthropic, OpenAI, Gemini,
  and OpenRouter are registered with capabilities but no ProviderDriver.
  Calling execute() with an unimplemented provider returns
  AdapterExecutionResult(success=False) immediately — no exception raised.
  Adding a provider in future sprints requires registering a ProviderDriver
  only. No adapter redesign.

Separation from existing providers/:
  hermes.providers.*  → legacy AIProvider contract (kernel integration, Sprint ≤40)
  hermes.adapters.llm_adapter → new Gateway-aligned adapter (Sprint 61+)

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── LLMProvider ────────────────────────────────────────────────────────────────


class LLMProvider(enum.Enum):
    """Enumeration of known LLM providers.

    Adding a new provider requires:
      1. Adding an entry here.
      2. Registering a ProviderDriver in LlmAdapter.
    No other changes.

    OLLAMA      → local inference via Ollama (implemented, Sprint 61)
    ANTHROPIC   → Anthropic Claude API (capabilities registered, Sprint 61)
    OPENAI      → OpenAI API (capabilities registered, Sprint 61)
    GEMINI      → Google Gemini API (capabilities registered, Sprint 61)
    OPENROUTER  → OpenRouter (capabilities registered, Sprint 61)
    """

    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"


# ── ProviderCapabilities ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Immutable declaration of what a registered provider supports.

    Used by the adapter for capability discovery and validation.
    Does not contain credentials or runtime configuration — those live
    in AdapterConfiguration.

    provider                 → the provider this declaration covers
    supports_streaming       → provider supports token-by-token streaming
    supports_structured_output → provider supports JSON schema enforcement
    supports_system_prompt   → provider accepts a separate system message
    supports_tool_use        → provider supports function/tool calling
    max_context_tokens       → maximum context window in tokens
    default_model            → suggested default model identifier
    requires_api_key         → whether an API key is required

    Immutable after construction.
    """

    provider: LLMProvider
    supports_streaming: bool
    supports_structured_output: bool
    supports_system_prompt: bool
    supports_tool_use: bool
    max_context_tokens: int
    default_model: str
    requires_api_key: bool


# ── AdapterConfiguration ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdapterConfiguration:
    """Immutable runtime configuration for a single adapter invocation.

    Carries everything the adapter needs to make a provider call:
    which provider, which model, where to reach it, and how to behave.

    Does NOT carry credentials beyond api_key — no session state,
    no connection pool, no mutable runtime context.

    provider                  → which provider to call
    model                     → model identifier (provider-specific format)
    base_url                  → provider endpoint base (e.g. "http://localhost:11434")
    api_key                   → authentication token; empty string for local providers
    timeout_seconds           → HTTP timeout; default 30
    max_tokens                → maximum completion tokens; default 2048
    temperature               → sampling temperature; 0.0 = deterministic
    streaming                 → whether to request streaming (metadata only in Sprint 61)
    structured_output_schema  → JSON schema string for structured output; empty = none

    Immutable after construction.
    """

    provider: LLMProvider
    model: str
    base_url: str
    api_key: str                      # empty string for providers that need no key
    timeout_seconds: int = 30
    max_tokens: int = 2048
    temperature: float = 0.0          # 0.0 = deterministic
    streaming: bool = False
    structured_output_schema: str = ""  # empty = no structured output


# ── LLMRequest ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Immutable normalized Hermes-side representation of an LLM request.

    Produced by LlmAdapter.build_llm_request() from an ExecutionRequest and
    an AdapterConfiguration. Represents the canonical request before any
    provider-specific translation occurs.

    The ProviderDriver receives an LLMRequest and translates it into a
    provider-specific JSON payload. The LLMRequest itself is provider-agnostic.

    request_id               → from ExecutionRequest.request_id
    provider                 → from AdapterConfiguration.provider
    model                    → from AdapterConfiguration.model
    system_prompt            → system-level instructions (from payload or empty)
    user_prompt              → user-level content (from payload["prompt"])
    max_tokens               → from AdapterConfiguration.max_tokens
    temperature              → from AdapterConfiguration.temperature
    streaming                → from AdapterConfiguration.streaming
    structured_output_schema → from AdapterConfiguration.structured_output_schema
                               or payload["schema"] if provided
    metadata                 → sorted tuple of additional key-value pairs
                               from the ExecutionRequest payload

    Immutable after construction.
    """

    request_id: str
    provider: LLMProvider
    model: str
    system_prompt: str
    user_prompt: str
    max_tokens: int
    temperature: float
    streaming: bool
    structured_output_schema: str
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── LLMResponse ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Immutable normalized Hermes-side representation of an LLM response.

    Produced by ProviderDriver.parse_response() after the provider returns.
    Represents the canonical response regardless of which provider was used.

    The adapter translates this into an AdapterExecutionResult for return
    to the Execution Gateway.

    request_id              → from the originating LLMRequest
    provider                → which provider produced this response
    model                   → model that actually responded (may differ from requested)
    content                 → generated text content
    input_tokens            → tokens consumed by the prompt
    output_tokens           → tokens generated in the response
    total_tokens            → input_tokens + output_tokens
    streaming_used          → whether streaming was active during generation
    structured_output_used  → whether structured output schema was applied
    finish_reason           → "stop", "length", "error", or provider-specific string
    metadata                → sorted tuple of additional provider-specific key-value pairs

    Immutable after construction.
    """

    request_id: str
    provider: LLMProvider
    model: str
    content: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    streaming_used: bool
    structured_output_used: bool
    finish_reason: str
    metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── AdapterValidationResult ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdapterValidationResult:
    """Immutable outcome of adapter-level validation.

    This is ADAPTER-LEVEL validation only:
      - adapter_type must be ExecutionAdapter.LLM
      - model must be non-empty
      - max_tokens must be > 0
      - timeout_seconds must be > 0
      - provider must be registered in the adapter

    The adapter does NOT re-validate:
      - request_id / operation_id / action_id → Gateway already validated these
      - capability correctness → SkillValidator is the authority
      - operation correctness  → OperationEngine is the authority

    valid=True  → all adapter preconditions met; errors is empty
    valid=False → one or more failures; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[str, ...]


# ── AdapterExecutionResult ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdapterExecutionResult:
    """Immutable outcome of a complete LLM adapter execution cycle.

    Produced by LlmAdapter.execute() and returned to the Execution Gateway.
    Contains the complete record of translation, execution, and response.

    Lifecycle states:
      success=True  → llm_request populated, llm_response populated
      success=False → error describes failure reason
        - llm_request=None  → failed before translation (validation failure)
        - llm_request set   → failed after translation (provider/HTTP failure)
        - llm_response=None → always None when success=False

    request_id       → from the originating ExecutionRequest
    operation_id     → from the originating ExecutionRequest
    provider         → which provider was targeted
    llm_request      → the normalized request sent to the provider (None if pre-translation failure)
    llm_response     → the normalized response from the provider (None on failure)
    success          → True if provider responded successfully
    error            → error description if success=False; None otherwise
    adapter_metadata → sorted tuple of adapter-level key-value pairs (provider, model, action)

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    provider: LLMProvider
    llm_request: LLMRequest | None          # None if failed before translation
    llm_response: LLMResponse | None        # None on any failure
    success: bool
    error: str | None
    adapter_metadata: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "AdapterConfiguration",
    "AdapterExecutionResult",
    "AdapterValidationResult",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "ProviderCapabilities",
]
