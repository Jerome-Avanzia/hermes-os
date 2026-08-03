"""N8nProvider — HTTP abstraction over the n8n REST API.

Handles authentication and JSON parsing.
No business logic — just translates n8n JSON into dicts.

Amendment 1: Returns raw dicts; the N8nRuntime handles Hermes ID mapping.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class N8nProvider:
    """Low-level HTTP client for the n8n REST API."""

    def __init__(self, api_url: str, api_key: str) -> None:
        self._api_url = api_url.rstrip("/") if api_url else ""
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_url and self._api_key)

    def health(self) -> dict[str, Any]:
        """Check connectivity and authentication.

        Returns a raw dict with health fields.
        The N8nRuntime wraps this into N8nHealthStatus.
        """
        result: dict[str, Any] = {
            "configured": self.configured,
            "authenticated": False,
            "reachable": False,
        }
        if not self.configured:
            return result

        try:
            workflows = self._get("/api/v1/workflows")
            result["reachable"] = True
            result["authenticated"] = True
            data = workflows.get("data", []) if isinstance(workflows, dict) else workflows
            if isinstance(data, list):
                result["workflow_count"] = len(data)
                result["active_count"] = sum(1 for w in data if w.get("active"))
            else:
                result["workflow_count"] = 0
                result["active_count"] = 0
        except HTTPError as exc:
            result["reachable"] = True
            if exc.code == 401:
                result["authenticated"] = False
            else:
                logger.warning("n8n health check HTTP error: %s", exc)
        except URLError:
            result["reachable"] = False

        return result

    def list_workflows(self) -> list[dict[str, Any]]:
        """List all workflows from the n8n API."""
        if not self.configured:
            return []
        try:
            response = self._get("/api/v1/workflows")
            data = response.get("data", []) if isinstance(response, dict) else response
            return data if isinstance(data, list) else []
        except (HTTPError, URLError) as exc:
            logger.warning("n8n list_workflows failed: %s", exc)
            return []

    def get_workflow(self, provider_id: str) -> dict[str, Any] | None:
        """Get a single workflow by its native n8n ID."""
        if not self.configured:
            return None
        try:
            return self._get(f"/api/v1/workflows/{provider_id}")
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def list_executions(
        self,
        workflow_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List recent executions for a workflow."""
        if not self.configured:
            return []
        try:
            response = self._get(
                f"/api/v1/executions?workflowId={workflow_id}&limit={limit}"
            )
            data = response.get("data", []) if isinstance(response, dict) else response
            return data if isinstance(data, list) else []
        except (HTTPError, URLError) as exc:
            logger.warning("n8n list_executions failed: %s", exc)
            return []

    def _get(self, path: str) -> Any:
        """Make an authenticated GET request to the n8n API."""
        url = f"{self._api_url}{path}"
        req = Request(url)
        req.add_header("X-N8N-API-KEY", self._api_key)
        req.add_header("Accept", "application/json")

        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
