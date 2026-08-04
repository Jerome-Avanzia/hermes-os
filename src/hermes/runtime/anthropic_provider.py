"""AnthropicProvider — HTTP abstraction over the Anthropic REST API.

Cloud LLM provider. Requires an API key.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hermes.runtime.llm_provider import LlmProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(LlmProvider):
    """Low-level HTTP client for the Anthropic API."""

    API_URL = "https://api.anthropic.com"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Anthropic"

    @property
    def provider_type(self) -> str:
        return "cloud"

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "configured": self.configured,
            "authenticated": False,
            "reachable": False,
        }
        if not self.configured:
            return result
        try:
            self._get("/v1/models")
            result["reachable"] = True
            result["authenticated"] = True
        except HTTPError as exc:
            result["reachable"] = True
            if exc.code in (401, 403):
                result["authenticated"] = False
            else:
                logger.warning("Anthropic health check HTTP error: %s", exc)
        except URLError:
            result["reachable"] = False
        return result

    def list_models(self) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self._get("/v1/models")
            data = response.get("data", []) if isinstance(response, dict) else []
            return data if isinstance(data, list) else []
        except (HTTPError, URLError) as exc:
            logger.warning("Anthropic list_models failed: %s", exc)
            return []

    def _get(self, path: str) -> Any:
        url = f"{self.API_URL}{path}"
        req = Request(url)
        req.add_header("x-api-key", self._api_key)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
