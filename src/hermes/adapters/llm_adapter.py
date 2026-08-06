"""LLM Adapter — provider-agnostic execution adapter for LLM operations.

Architecture position:
  Execution Gateway → LLM Adapter → Provider (Ollama, Anthropic, etc.)

The LLM Adapter is NOT the LLM. It is a provider adapter:
  - It translates Hermes ExecutionRequests into normalized LLMRequests.
  - It dispatches through the registered ProviderDriver for each provider.
  - It translates provider responses back into Hermes AdapterExecutionResults.

The adapter is provider-agnostic. It knows only:
  1. The ProviderDriver interface (three callables + endpoint_path).
  2. The provider registry (capabilities and drivers registered at startup).

Provider-specific code lives exclusively in provider modules:
  hermes.providers.ollama_driver     → Ollama /api/chat implementation
  hermes.providers.anthropic_driver  → Anthropic capabilities (driver: future)
  hermes.providers.openai_driver     → OpenAI capabilities (driver: future)
  hermes.providers.gemini_driver     → Gemini capabilities (driver: future)
  hermes.providers.openrouter_driver → OpenRouter capabilities (driver: future)

Adding a new provider requires no changes to this file::

    # In the new provider module:
    PROVIDER_DRIVER = ProviderDriver(
        build_payload=...,
        call_provider=...,
        parse_response=...,
        endpoint_path=...,
    )

    # At startup:
    adapter.register_provider(LLMProvider.OPENAI, OPENAI_CAPABILITIES, driver=OPENAI_DRIVER)

Architecture invariants preserved:
  - The adapter never performs routing.
  - The adapter never performs planning or orchestration.
  - The adapter never bypasses the Execution Gateway.
  - The adapter never knows about Jobs, Swarms, or Workflow.
  - The adapter never selects providers — it dispatches to the registered driver.
  - The adapter never branches on provider names.
  - The adapter never imports provider modules.

  Permanent architecture:
    Execution Gateway
          ↓
    LLM Adapter   (this file — ProviderDriver interface + registry)
          ↓
    Provider      (ollama_driver, anthropic_driver, etc.)

Sources of non-determinism introduced (explicit):
  - LLM provider responses are non-deterministic (temperature > 0.0).
  - At temperature=0.0, most providers produce deterministic output.
  - Network latency and provider availability are non-deterministic.
  - The adapter translation (build_llm_request, build_payload) is deterministic.

Network calls introduced:
  - None. Network calls live in provider driver modules (e.g. _call_ollama).

Filesystem writes introduced:
  - None.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.llm_adapter import (
    AdapterConfiguration,
    AdapterExecutionResult,
    AdapterValidationResult,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderCapabilities,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# ProviderDriver — the only provider-specific interface the adapter knows
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ProviderDriver:
    """Provider-specific execution driver.

    The complete interface between the LLM Adapter and a provider.
    The adapter dispatches through a registered ProviderDriver — it never
    inspects provider names or calls provider APIs directly.

    Adding a new provider means implementing a ProviderDriver in a dedicated
    provider module and registering it via adapter.register_provider().
    No changes to LlmAdapter or the Execution Gateway are required.

    build_payload    → translate LLMRequest + AdapterConfiguration → provider JSON dict
    call_provider    → make the HTTP request; return raw provider JSON dict
                       signature: (endpoint: str, payload: dict, timeout: int, api_key: str) → dict
    parse_response   → translate raw provider JSON → normalized LLMResponse
                       signature: (raw: dict, llm_request: LLMRequest) → LLMResponse
    endpoint_path    → provider-specific API path appended to base_url
                       e.g. "/api/chat" for Ollama, "/v1/chat/completions" for OpenAI
    """

    build_payload: Callable[[LLMRequest, AdapterConfiguration], dict]
    call_provider: Callable[[str, dict, int, str], dict]
    parse_response: Callable[[dict, LLMRequest], LLMResponse]
    endpoint_path: str


# ══════════════════════════════════════════════════════════════════════════════
# LlmAdapter — the provider-agnostic dispatch layer
# ══════════════════════════════════════════════════════════════════════════════


class LlmAdapter:
    """Provider-agnostic execution adapter for LLM operations.

    Maintains two parallel registries:
      _capabilities: LLMProvider → ProviderCapabilities (what the provider can do)
      _drivers:      LLMProvider → ProviderDriver       (how to call the provider)

    A provider may be registered with capabilities only (no driver) — this
    enables capability discovery without enabling execution. Execution requires
    a ProviderDriver to be registered via register_provider().

    Registration model (consistent with SkillRegistry and ExecutionGateway):
      First registration for a provider wins. Subsequent registrations of the
      same LLMProvider are silently ignored. This ensures deterministic
      dispatch regardless of registration order.

    The adapter never imports provider modules. All provider-specific logic
    arrives through ProviderDriver instances registered at startup.

    Usage::

        from hermes.adapters.llm_adapter import LlmAdapter
        from hermes.providers.ollama_driver import OLLAMA_CAPABILITIES, OLLAMA_DRIVER
        from hermes.models.llm_adapter import LLMProvider

        adapter = LlmAdapter()
        adapter.register_provider(LLMProvider.OLLAMA, OLLAMA_CAPABILITIES, driver=OLLAMA_DRIVER)

        config = AdapterConfiguration(
            provider=LLMProvider.OLLAMA,
            model="llama3.2",
            base_url="http://localhost:11434",
            api_key="",
        )
        result = adapter.execute(request, config)
        # result.success → True if Ollama responded
        # result.llm_response.content → generated text
    """

    def __init__(self) -> None:
        self._capabilities: dict[LLMProvider, ProviderCapabilities] = {}
        self._drivers: dict[LLMProvider, ProviderDriver] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register_provider(
        self,
        provider: LLMProvider,
        capabilities: ProviderCapabilities,
        driver: ProviderDriver | None = None,
    ) -> bool:
        """Register a provider with optional execution driver.

        First registration for a LLMProvider wins. Subsequent registrations
        of the same provider are silently ignored.

        Args:
            provider:     The LLMProvider to register.
            capabilities: Declared capabilities of this provider.
            driver:       Optional ProviderDriver enabling execution. If None,
                          capability discovery works but execute() will fail.

        Returns:
            True if registered; False if this provider was already registered.
        """
        if provider in self._capabilities:
            logger.debug(
                "LlmAdapter: provider %r already registered — ignoring duplicate",
                provider.value,
            )
            return False

        self._capabilities[provider] = capabilities
        if driver is not None:
            self._drivers[provider] = driver

        logger.info(
            "LlmAdapter: registered provider %r (driver=%s)",
            provider.value,
            driver is not None,
        )
        return True

    # ── Capability discovery ──────────────────────────────────────────────────

    def discover_capabilities(
        self,
        provider: LLMProvider,
    ) -> ProviderCapabilities | None:
        """Return the registered ProviderCapabilities for a provider, or None.

        Args:
            provider: The LLMProvider to look up.

        Returns:
            ProviderCapabilities if registered; None otherwise.
        """
        return self._capabilities.get(provider)

    def list_providers(self) -> tuple[LLMProvider, ...]:
        """Return all registered providers in deterministic order.

        Ordered by provider value (lexicographic) for full determinism
        regardless of registration order.

        Returns:
            Tuple of registered LLMProviders, sorted by value.
        """
        return tuple(
            sorted(self._capabilities, key=lambda p: p.value)
        )

    def has_driver(self, provider: LLMProvider) -> bool:
        """Return True if the provider has a registered ProviderDriver."""
        return provider in self._drivers

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(
        self,
        request: ExecutionRequest,
        config: AdapterConfiguration,
    ) -> AdapterValidationResult:
        """Validate an ExecutionRequest at the adapter level.

        ADAPTER-LEVEL validation only. The Gateway has already validated
        request_id, operation_id, and action_id. This checks adapter
        preconditions:

          1. adapter_type must be ExecutionAdapter.LLM
          2. config.model must be non-empty
          3. config.base_url must be non-empty
          4. config.max_tokens must be > 0
          5. config.timeout_seconds must be > 0
          6. config.provider must be registered in this adapter

        Args:
            request: The ExecutionRequest received from the Gateway.
            config:  The AdapterConfiguration for this invocation.

        Returns:
            AdapterValidationResult with valid=True if all checks pass.
        """
        errors: list[str] = []

        if request.adapter_type != ExecutionAdapter.LLM:
            errors.append(
                f"adapter_type must be LLM, got {request.adapter_type.value!r}"
            )

        if not config.model.strip():
            errors.append("config.model must not be empty")

        if not config.base_url.strip():
            errors.append("config.base_url must not be empty")

        if config.max_tokens <= 0:
            errors.append(
                f"config.max_tokens must be > 0, got {config.max_tokens}"
            )

        if config.timeout_seconds <= 0:
            errors.append(
                f"config.timeout_seconds must be > 0, got {config.timeout_seconds}"
            )

        if config.provider not in self._capabilities:
            errors.append(
                f"provider {config.provider.value!r} is not registered"
            )

        return AdapterValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Request translation ───────────────────────────────────────────────────

    def build_llm_request(
        self,
        request: ExecutionRequest,
        config: AdapterConfiguration,
    ) -> LLMRequest:
        """Translate an ExecutionRequest into a normalized LLMRequest.

        Provider-agnostic: the LLMRequest is the same structure regardless
        of which provider will be called. The ProviderDriver translates it
        further into provider-specific JSON.

        Payload key extraction (all values must be strings per ExecutionRequest):
          "prompt"        → user_prompt (required for meaningful generation)
          "system_prompt" → system_prompt (optional; empty string = none)
          "schema"        → structured_output_schema override (optional)

        Remaining payload keys are passed through as sorted metadata.

        Args:
            request: The ExecutionRequest from the Execution Gateway.
            config:  The AdapterConfiguration for this invocation.

        Returns:
            LLMRequest ready for ProviderDriver.build_payload().
        """
        payload_dict = dict(request.payload)

        user_prompt = payload_dict.pop("prompt", "")
        system_prompt = payload_dict.pop("system_prompt", "")
        schema_override = payload_dict.pop("schema", "")

        structured_output_schema = (
            schema_override if schema_override
            else config.structured_output_schema
        )

        metadata = tuple(sorted(payload_dict.items()))

        return LLMRequest(
            request_id=request.request_id,
            provider=config.provider,
            model=config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            streaming=config.streaming,
            structured_output_schema=structured_output_schema,
            metadata=metadata,
        )

    # ── Execution ─────────────────────────────────────────────────────────────

    def execute(
        self,
        request: ExecutionRequest,
        config: AdapterConfiguration,
    ) -> AdapterExecutionResult:
        """Execute an LLM request through the registered provider driver.

        Execution steps:
          1. Validate request and configuration at the adapter level.
             → Returns success=False if validation fails.
          2. Build the normalized LLMRequest.
          3. Resolve the ProviderDriver for config.provider.
             → Returns success=False if no driver is registered.
          4. Translate LLMRequest → provider JSON payload via driver.build_payload().
          5. Determine the provider endpoint: config.base_url + driver.endpoint_path.
          6. Call the provider via driver.call_provider().
             → Returns success=False on any HTTP or network failure.
          7. Parse the raw response via driver.parse_response() → LLMResponse.
          8. Return AdapterExecutionResult with success=True.

        The adapter never raises exceptions — all failures are captured in
        AdapterExecutionResult(success=False, error=...).

        Non-determinism: the provider response is non-deterministic (LLMs are
        stochastic at temperature > 0.0). At temperature=0.0 most providers
        produce deterministic output. The translation steps (1–4) are always
        deterministic given the same inputs.

        Network calls: step 6 delegates to driver.call_provider() — the
        network call lives in the provider module, not here.

        Args:
            request: The ExecutionRequest from the Execution Gateway.
            config:  The AdapterConfiguration specifying provider and model.

        Returns:
            AdapterExecutionResult capturing the full execution outcome.
        """
        adapter_meta_base: dict[str, str] = {
            "provider": config.provider.value,
            "model": config.model,
            "action": request.action_id,
        }

        # ── 1. Validate ────────────────────────────────────────────────────
        validation = self.validate(request, config)
        if not validation.valid:
            logger.warning(
                "LlmAdapter: validation failed for request_id=%r: %r",
                request.request_id,
                validation.errors,
            )
            return AdapterExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                provider=config.provider,
                llm_request=None,
                llm_response=None,
                success=False,
                error="; ".join(validation.errors),
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 2. Build normalized request ────────────────────────────────────
        llm_request = self.build_llm_request(request, config)

        # ── 3. Resolve driver ──────────────────────────────────────────────
        driver = self._drivers.get(config.provider)
        if driver is None:
            error_msg = (
                f"no_driver_for_{config.provider.value} — provider is registered "
                f"with capabilities but no ProviderDriver; add a driver to enable "
                f"execution"
            )
            logger.warning(
                "LlmAdapter: no driver for provider %r (request_id=%r)",
                config.provider.value,
                request.request_id,
            )
            return AdapterExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                provider=config.provider,
                llm_request=llm_request,
                llm_response=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 4. Build provider payload ──────────────────────────────────────
        payload = driver.build_payload(llm_request, config)

        # ── 5. Determine endpoint ──────────────────────────────────────────
        endpoint = config.base_url.rstrip("/") + driver.endpoint_path

        # ── 6. Call provider ───────────────────────────────────────────────
        try:
            raw = driver.call_provider(
                endpoint, payload, config.timeout_seconds, config.api_key,
            )
        except Exception as exc:
            error_msg = f"provider_call_failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "LlmAdapter: provider call failed for request_id=%r: %s",
                request.request_id,
                error_msg,
            )
            return AdapterExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                provider=config.provider,
                llm_request=llm_request,
                llm_response=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 7. Parse response ──────────────────────────────────────────────
        try:
            llm_response = driver.parse_response(raw, llm_request)
        except Exception as exc:
            error_msg = f"response_parse_failed: {type(exc).__name__}: {exc}"
            logger.warning(
                "LlmAdapter: response parsing failed for request_id=%r: %s",
                request.request_id,
                error_msg,
            )
            return AdapterExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                provider=config.provider,
                llm_request=llm_request,
                llm_response=None,
                success=False,
                error=error_msg,
                adapter_metadata=tuple(sorted(adapter_meta_base.items())),
            )

        # ── 8. Assemble result ─────────────────────────────────────────────
        logger.info(
            "LlmAdapter: execution complete request_id=%r provider=%r "
            "model=%r tokens=%d finish_reason=%r",
            request.request_id,
            config.provider.value,
            llm_response.model,
            llm_response.total_tokens,
            llm_response.finish_reason,
        )

        adapter_meta = dict(adapter_meta_base)
        adapter_meta["finish_reason"] = llm_response.finish_reason
        adapter_meta["total_tokens"] = str(llm_response.total_tokens)

        return AdapterExecutionResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            provider=config.provider,
            llm_request=llm_request,
            llm_response=llm_response,
            success=True,
            error=None,
            adapter_metadata=tuple(sorted(adapter_meta.items())),
        )
