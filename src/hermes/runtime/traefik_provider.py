"""TraefikProvider — reads routing metadata from the Traefik API.

Implements the InfrastructureProvider interface (Amendment 1).
Uses urllib for HTTP, consistent with the existing GitHubProvider pattern.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hermes.runtime.infrastructure_provider import InfrastructureProvider, ProviderHealth

logger = logging.getLogger(__name__)


class TraefikProvider(InfrastructureProvider):
    """Read-only provider for the Traefik HTTP API."""

    def __init__(self, api_url: str = "") -> None:
        self._api_url = api_url.rstrip("/") if api_url else ""

    @property
    def name(self) -> str:
        return "traefik"

    @property
    def configured(self) -> bool:
        return bool(self._api_url)

    def health(self) -> ProviderHealth:
        status = ProviderHealth(provider_name=self.name, configured=self.configured)
        if not self.configured:
            return status
        try:
            data = self._get("/api/overview")
            status.reachable = True
            http_data = data.get("http", {})
            status.detail = {
                "routers_count": http_data.get("routers", {}).get("total", 0),
                "services_count": http_data.get("services", {}).get("total", 0),
            }
        except Exception as exc:
            logger.debug("Traefik health check failed: %s", exc)
            status.reachable = False
        return status

    def collect(self) -> list[dict[str, Any]]:
        """Return Traefik HTTP routers with their rule and service bindings."""
        if not self.configured:
            return []
        try:
            return self._get("/api/http/routers")
        except Exception as exc:
            logger.warning("Traefik collect failed: %s", exc)
            return []

    def _get(self, path: str) -> Any:
        """Make a GET request to the Traefik API."""
        url = f"{self._api_url}{path}"
        req = Request(url)
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
