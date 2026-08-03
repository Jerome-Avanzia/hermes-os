"""GitHubProvider — HTTP abstraction over the GitHub REST API.

Handles authentication, pagination, and rate-limit extraction.
No business logic — just translates GitHub JSON into dicts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import json

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"


@dataclass(slots=True)
class RateLimitInfo:
    limit: int = 0
    remaining: int = 0
    reset: int = 0


@dataclass(slots=True)
class HealthStatus:
    configured: bool = False
    authenticated: bool = False
    reachable: bool = False
    rate_limit: RateLimitInfo | None = None
    org: str = ""


class GitHubProvider:
    """Low-level HTTP client for the GitHub REST API."""

    def __init__(self, org: str, token: str) -> None:
        self._org = org
        self._token = token

    @property
    def org(self) -> str:
        return self._org

    @property
    def configured(self) -> bool:
        return bool(self._org and self._token)

    def health(self) -> HealthStatus:
        """Check connectivity, authentication, and rate limits."""
        status = HealthStatus(
            configured=self.configured,
            org=self._org,
        )
        if not self.configured:
            return status

        try:
            data, headers = self._get("/rate_limit")
            status.reachable = True
            status.authenticated = True
            rate = data.get("rate", {})
            status.rate_limit = RateLimitInfo(
                limit=rate.get("limit", 0),
                remaining=rate.get("remaining", 0),
                reset=rate.get("reset", 0),
            )
        except HTTPError as exc:
            status.reachable = True
            if exc.code == 401:
                status.authenticated = False
            else:
                logger.warning("GitHub health check HTTP error: %s", exc)
        except URLError:
            status.reachable = False

        return status

    def list_org_repos(self) -> list[dict[str, Any]]:
        """List all repositories in the configured organization."""
        if not self.configured:
            return []
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            data, _ = self._get(
                f"/orgs/{self._org}/repos",
                params={"per_page": "100", "page": str(page)},
            )
            if not data:
                break
            repos.extend(data)
            if len(data) < 100:
                break
            page += 1
        return repos

    def get_repo(self, repo_name: str) -> dict[str, Any] | None:
        """Get a single repository by name."""
        if not self.configured:
            return None
        try:
            data, _ = self._get(f"/repos/{self._org}/{repo_name}")
            return data
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def get_open_prs_count(self, repo_name: str) -> int:
        """Get count of open pull requests for a repository."""
        if not self.configured:
            return 0
        try:
            data, _ = self._get(
                f"/repos/{self._org}/{repo_name}/pulls",
                params={"state": "open", "per_page": "1"},
            )
            return len(data) if data else 0
        except HTTPError:
            return 0

    def _get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        """Make an authenticated GET request to the GitHub API."""
        url = f"{_API_BASE}{path}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{query}"

        req = Request(url)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")

        with urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return body, headers
