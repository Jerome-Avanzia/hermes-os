"""LlmProvider — abstract interface for AI/LLM providers.

All LLM providers implement this generic interface.
Future providers plug into the same LlmRuntime without changing Hermes.
"""

from __future__ import annotations

import abc
from typing import Any


class LlmProvider(abc.ABC):
    """Abstract base class for AI/LLM providers.

    Each provider translates a native API (Ollama, OpenAI, Anthropic, etc.)
    into lists of raw dicts that the LlmRuntime converts into
    provider-independent LlmProviderInfo and LlmModel objects.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Short provider name, e.g. 'ollama', 'openai', 'anthropic'."""

    @property
    @abc.abstractmethod
    def display_name(self) -> str:
        """Human-readable provider name, e.g. 'Ollama', 'OpenAI'."""

    @property
    @abc.abstractmethod
    def provider_type(self) -> str:
        """Provider type: 'local' or 'cloud'."""

    @property
    @abc.abstractmethod
    def configured(self) -> bool:
        """Whether this provider has the configuration it needs."""

    @abc.abstractmethod
    def health(self) -> dict[str, Any]:
        """Check connectivity and return health status as a raw dict."""

    @abc.abstractmethod
    def list_models(self) -> list[dict[str, Any]]:
        """List all available models from the provider."""
