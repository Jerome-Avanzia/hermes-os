"""OllamaProvider — HTTP abstraction over the Ollama REST API.

Local LLM provider. Requires only a URL, no API key.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hermes.runtime.llm_provider import LlmProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LlmProvider):
    """Low-level HTTP client for the Ollama REST API."""

    def __init__(self, api_url: str) -> None:
        self._api_url = api_url.rstrip("/") if api_url else ""

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def display_name(self) -> str:
        return "Ollama"

    @property
    def provider_type(self) -> str:
        return "local"

    @property
    def configured(self) -> bool:
        return bool(self._api_url)

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "configured": self.configured,
            "authenticated": False,
            "reachable": False,
        }
        if not self.configured:
            return result
        try:
            self._get("/api/tags")
            result["reachable"] = True
            result["authenticated"] = True  # Ollama has no auth
        except URLError:
            result["reachable"] = False
        return result

    def list_models(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self._get("/api/tags")
            models = response.get("models", []) if isinstance(response, dict) else []
            return models if isinstance(models, list) else []
        except (HTTPError, URLError) as exc:
            logger.warning("Ollama list_models failed: %s", exc)
            return []

    def _get(self, path: str) -> Any:
        url = f"{self._api_url}{path}"
        req = Request(url)
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
