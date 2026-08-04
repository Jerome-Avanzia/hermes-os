"""LlmRuntime — aggregates multiple LLM providers into a unified view.

Wraps a list of LlmProvider instances and converts raw API responses into
provider-independent LlmProviderInfo and LlmModel objects.

Amendment 2: Providers carry a priority field.
Amendment 3: Model capabilities via ModelCapabilities value object.
Amendment 4: LlmHealthStatus includes per-provider summary list.
Amendment 5: Model IDs are immutable (provider--model-slug).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from hermes.models.llm_model import (
    LlmModel,
    ModelCapabilities,
    compute_model_attention,
)
from hermes.models.llm_provider_info import (
    LlmProviderInfo,
    compute_provider_health,
)
from hermes.runtime.llm_provider import LlmProvider

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProviderSummary:
    """Amendment 4: Lightweight per-provider health summary."""

    id: str
    name: str
    health_state: str = "unconfigured"


@dataclass(slots=True)
class LlmHealthStatus:
    """Health status for the LLM integration.

    Amendment 4: Includes per-provider summary list.
    """

    configured: bool = False
    provider_count: int = 0
    healthy_count: int = 0
    model_count: int = 0
    default_provider: str = ""
    default_model: str = ""
    providers: list[ProviderSummary] = field(default_factory=list)
    last_sync: str = ""
    refresh_duration_ms: int = 0


class LlmRuntime:
    """High-level runtime that aggregates multiple LLM providers."""

    def __init__(
        self,
        providers: list[LlmProvider],
        default_provider: str = "",
        default_model: str = "",
    ) -> None:
        self._providers = providers
        self._default_provider = default_provider
        self._default_model = default_model

    @property
    def configured(self) -> bool:
        return any(p.configured for p in self._providers)

    def health(self) -> LlmHealthStatus:
        """Return aggregated health status with per-provider summaries."""
        start = time.monotonic()

        provider_infos = self.list_providers()
        all_models = self.list_models()

        duration_ms = int((time.monotonic() - start) * 1000)

        provider_summaries = [
            ProviderSummary(id=p.id, name=p.name, health_state=p.health_state)
            for p in provider_infos
        ]

        return LlmHealthStatus(
            configured=self.configured,
            provider_count=len(provider_infos),
            healthy_count=sum(1 for p in provider_infos if p.health_state == "healthy"),
            model_count=len(all_models),
            default_provider=self._default_provider,
            default_model=self._default_model,
            providers=provider_summaries,
            last_sync=datetime.now(timezone.utc).isoformat(),
            refresh_duration_ms=duration_ms,
        )

    def list_providers(self) -> list[LlmProviderInfo]:
        """Return provider info for all configured providers."""
        result: list[LlmProviderInfo] = []
        for idx, provider in enumerate(self._providers):
            if not provider.configured:
                continue
            info = self._provider_to_info(provider, priority=idx)
            result.append(info)
        return result

    def get_provider(self, provider_id: str) -> LlmProviderInfo | None:
        """Get a single provider by Hermes ID."""
        for info in self.list_providers():
            if info.id == provider_id:
                return info
        return None

    def list_models(self) -> list[LlmModel]:
        """Return all models across all configured providers."""
        result: list[LlmModel] = []
        for provider in self._providers:
            if not provider.configured:
                continue
            health = self._check_health(provider)
            reachable = health.get("reachable", False)
            raw_models = provider.list_models()
            for raw in raw_models:
                model = self._raw_to_model(raw, provider, reachable)
                result.append(model)
        return result

    def get_model(self, model_id: str) -> LlmModel | None:
        """Get a single model by its immutable Hermes ID."""
        for model in self.list_models():
            if model.id == model_id:
                return model
        return None

    def list_models_for_provider(self, provider: LlmProvider) -> list[LlmModel]:
        """Return models for a specific provider."""
        if not provider.configured:
            return []
        health = self._check_health(provider)
        reachable = health.get("reachable", False)
        raw_models = provider.list_models()
        return [self._raw_to_model(raw, provider, reachable) for raw in raw_models]

    def _check_health(self, provider: LlmProvider) -> dict:
        """Check provider health, catching exceptions."""
        try:
            return provider.health()
        except Exception:
            return {"configured": provider.configured, "authenticated": False, "reachable": False}

    def _provider_to_info(self, provider: LlmProvider, priority: int = 0) -> LlmProviderInfo:
        """Convert a provider into an LlmProviderInfo."""
        health = self._check_health(provider)
        configured = health.get("configured", False)
        authenticated = health.get("authenticated", False)
        reachable = health.get("reachable", False)

        raw_models = provider.list_models()
        model_count = len(raw_models)
        default_model = ""
        if raw_models:
            first = raw_models[0]
            default_model = (
                first.get("name", "")
                or first.get("id", "")
                or first.get("model", "")
            )

        return LlmProviderInfo(
            id=provider.name,
            name=provider.display_name,
            provider_type=provider.provider_type,
            configured=configured,
            authenticated=authenticated,
            reachable=reachable,
            default_model=default_model,
            model_count=model_count,
            health_state=compute_provider_health(configured, authenticated, reachable),
            priority=priority,
        )

    def _raw_to_model(
        self,
        raw: dict,
        provider: LlmProvider,
        provider_reachable: bool,
    ) -> LlmModel:
        """Convert a raw model dict into an LlmModel.

        Amendment 5: IDs are immutable — derived from provider name + model ID.
        """
        # Extract model identifier — varies by provider
        raw_id = str(raw.get("id", "") or raw.get("model", "") or raw.get("name", ""))
        raw_name = str(raw.get("name", "") or raw.get("id", "") or "")

        model_slug = _slugify(raw_id) if raw_id else "unnamed"
        hermes_id = f"{provider.name}--{model_slug}"

        # Display name
        display_name = raw_name or raw_id

        # Family inference
        family = _infer_family(raw_id)

        # Context window
        context_window = _extract_context_window(raw)

        # Capabilities (Amendment 3)
        capabilities = _infer_capabilities(raw_id, raw, provider.name)

        # Status
        status = "available" if provider_reachable else "unavailable"

        # Attention state
        attention_state = compute_model_attention(status, provider_reachable)

        return LlmModel(
            id=hermes_id,
            name=display_name,
            provider_id=provider.name,
            provider=provider.display_name,
            family=family,
            context_window=context_window,
            capabilities=capabilities,
            status=status,
            attention_state=attention_state,
        )


def _slugify(name: str) -> str:
    """Convert a model name/ID to a stable slug.

    'claude-sonnet-4-20250514' → 'claude-sonnet-4-20250514'
    'gpt-4o' → 'gpt-4o'
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9._-]+", "-", slug)
    slug = slug.strip("-")
    return slug or "unnamed"


def _infer_family(model_id: str) -> str:
    """Infer model family from model ID."""
    lower = model_id.lower()
    if "claude" in lower:
        return "claude"
    if "gpt" in lower:
        return "gpt"
    if "o1" in lower or "o3" in lower or "o4" in lower:
        return "gpt"
    if "llama" in lower:
        return "llama"
    if "mistral" in lower or "mixtral" in lower:
        return "mistral"
    if "gemini" in lower or "gemma" in lower:
        return "gemini"
    if "phi" in lower:
        return "phi"
    if "qwen" in lower:
        return "qwen"
    if "deepseek" in lower:
        return "deepseek"
    if "codestral" in lower or "codellama" in lower:
        return "code"
    return ""


def _extract_context_window(raw: dict) -> int:
    """Extract context window from raw model data."""
    # OpenAI / OpenRouter style
    ctx = raw.get("context_length") or raw.get("context_window")
    if isinstance(ctx, int) and ctx > 0:
        return ctx

    # Gemini style
    input_limit = raw.get("inputTokenLimit")
    if isinstance(input_limit, int) and input_limit > 0:
        return input_limit

    # Ollama detail style
    details = raw.get("details", {})
    if isinstance(details, dict):
        ctx = details.get("parameter_size", "")
        # Not a context window — skip

    return 0


def _infer_capabilities(model_id: str, raw: dict, provider_name: str) -> ModelCapabilities:
    """Infer model capabilities from ID and metadata.

    Amendment 3: Returns ModelCapabilities value object.
    """
    lower = model_id.lower()

    streaming = True  # Most models support streaming
    tools = False
    reasoning = False
    vision = False

    # Claude models
    if "claude" in lower:
        tools = True
        vision = True
        if "opus" in lower or "sonnet" in lower:
            reasoning = True

    # GPT models
    elif "gpt-4" in lower:
        tools = True
        vision = "o" in lower or "turbo" in lower or "vision" in lower or "4o" in lower
        reasoning = False
    elif "gpt-3" in lower:
        tools = True

    # OpenAI reasoning models
    elif lower.startswith("o1") or lower.startswith("o3") or lower.startswith("o4"):
        tools = True
        reasoning = True
        vision = True

    # Gemini models
    elif "gemini" in lower:
        tools = True
        vision = True
        reasoning = "thinking" in lower or "2.5" in lower

        # Check supported generation methods
        methods = raw.get("supportedGenerationMethods", [])
        if isinstance(methods, list):
            tools = "generateContent" in methods

    # Llama models
    elif "llama" in lower:
        tools = "3" in lower
        vision = "vision" in lower

    # Mistral models
    elif "mistral" in lower or "mixtral" in lower:
        tools = True

    return ModelCapabilities(
        streaming=streaming,
        tools=tools,
        reasoning=reasoning,
        vision=vision,
    )
