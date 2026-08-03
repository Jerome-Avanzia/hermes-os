"""GitHubRuntime — translates GitHub API data into CodeRepository models.

Wraps GitHubProvider and converts raw GitHub JSON into provider-independent
CodeRepository dataclass instances.

Amendment 1: Exposes runtime status — never hides missing config.
Amendment 2: Delegates HTTP to GitHubProvider.
Amendment 6: Health endpoint via provider.health().
"""

from __future__ import annotations

import logging

from hermes.models.code_repository import CodeRepository
from hermes.runtime.github_provider import GitHubProvider, HealthStatus

logger = logging.getLogger(__name__)


class GitHubRuntime:
    """High-level runtime that produces CodeRepository objects from GitHub."""

    def __init__(self, provider: GitHubProvider) -> None:
        self._provider = provider

    @property
    def configured(self) -> bool:
        return self._provider.configured

    def health(self) -> HealthStatus:
        """Return health status (Amendment 6)."""
        return self._provider.health()

    def list_repositories(self) -> list[CodeRepository]:
        """Fetch all org repositories and convert to CodeRepository."""
        if not self.configured:
            return []
        raw = self._provider.list_org_repos()
        return [self._to_model(r) for r in raw]

    def get_repository(self, repo_id: str) -> CodeRepository | None:
        """Fetch a single repository by name/slug."""
        if not self.configured:
            return None
        raw = self._provider.get_repo(repo_id)
        if raw is None:
            return None
        return self._to_model(raw)

    @staticmethod
    def _to_model(raw: dict) -> CodeRepository:
        return CodeRepository(
            id=raw.get("name", ""),
            name=raw.get("full_name", raw.get("name", "")),
            provider="github",
            url=raw.get("html_url", ""),
            description=raw.get("description") or "",
            default_branch=raw.get("default_branch", "main"),
            language=raw.get("language") or "",
            visibility=raw.get("visibility", "private"),
            archived=raw.get("archived", False),
            open_issues_count=raw.get("open_issues_count", 0),
            open_prs_count=0,  # requires separate API call
            last_push_at=raw.get("pushed_at") or "",
            topics=raw.get("topics", []),
        )
