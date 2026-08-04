"""NocodbProvider — HTTP abstraction over the NocoDB REST API (v2).

Implements DataProvider (Amendment 1).
Handles authentication and JSON parsing.
No business logic — just translates NocoDB JSON into dicts.

Amendment 2: Returns raw dicts; the NocodbRuntime handles Hermes ID mapping.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hermes.runtime.data_provider import DataProvider

logger = logging.getLogger(__name__)


class NocodbProvider(DataProvider):
    """Low-level HTTP client for the NocoDB REST API v2."""

    def __init__(self, api_url: str, api_token: str) -> None:
        self._api_url = api_url.rstrip("/") if api_url else ""
        self._api_token = api_token

    @property
    def name(self) -> str:
        return "nocodb"

    @property
    def configured(self) -> bool:
        return bool(self._api_url and self._api_token)

    def health(self) -> dict[str, Any]:
        """Check connectivity and authentication.

        Returns a raw dict with health fields.
        The NocodbRuntime wraps this into NocodbHealthStatus.
        """
        result: dict[str, Any] = {
            "configured": self.configured,
            "authenticated": False,
            "reachable": False,
        }
        if not self.configured:
            return result

        try:
            bases = self._get("/api/v2/meta/bases")
            result["reachable"] = True
            result["authenticated"] = True
            base_list = bases.get("list", []) if isinstance(bases, dict) else []
            result["database_count"] = len(base_list) if isinstance(base_list, list) else 0
        except HTTPError as exc:
            result["reachable"] = True
            if exc.code in (401, 403):
                result["authenticated"] = False
            else:
                logger.warning("NocoDB health check HTTP error: %s", exc)
        except URLError:
            result["reachable"] = False

        return result

    def list_bases(self) -> list[dict[str, Any]]:
        """List all bases (databases) from NocoDB."""
        if not self.configured:
            return []
        try:
            response = self._get("/api/v2/meta/bases")
            data = response.get("list", []) if isinstance(response, dict) else []
            return data if isinstance(data, list) else []
        except (HTTPError, URLError) as exc:
            logger.warning("NocoDB list_bases failed: %s", exc)
            return []

    def get_base(self, base_id: str) -> dict[str, Any] | None:
        """Get a single base by its native NocoDB ID."""
        if not self.configured:
            return None
        try:
            return self._get(f"/api/v2/meta/bases/{base_id}")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            logger.warning("NocoDB get_base failed: %s", exc)
            return None

    def list_tables(self, base_id: str) -> list[dict[str, Any]]:
        """List all tables for a given base."""
        if not self.configured:
            return []
        try:
            response = self._get(f"/api/v2/meta/bases/{base_id}/tables")
            data = response.get("list", []) if isinstance(response, dict) else []
            return data if isinstance(data, list) else []
        except (HTTPError, URLError) as exc:
            logger.warning("NocoDB list_tables failed: %s", exc)
            return []

    def get_table(self, table_id: str) -> dict[str, Any] | None:
        """Get a single table by its native NocoDB ID."""
        if not self.configured:
            return None
        try:
            return self._get(f"/api/v2/meta/tables/{table_id}")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            logger.warning("NocoDB get_table failed: %s", exc)
            return None

    def _get(self, path: str) -> Any:
        """Make an authenticated GET request to the NocoDB API."""
        url = f"{self._api_url}{path}"
        req = Request(url)
        req.add_header("xc-token", self._api_token)
        req.add_header("Accept", "application/json")

        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
