"""Tests for GitHubProvider, GitHubRuntime, and CodeRepository model."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from hermes.models.code_repository import CodeRepository
from hermes.runtime.github_provider import GitHubProvider, HealthStatus, RateLimitInfo
from hermes.runtime.github_runtime import GitHubRuntime


# -- CodeRepository model tests ------------------------------------------------


def test_code_repository_defaults():
    repo = CodeRepository(id="test", name="test-repo", provider="github")
    assert repo.id == "test"
    assert repo.provider == "github"
    assert repo.default_branch == "main"
    assert repo.visibility == "private"
    assert repo.archived is False
    assert repo.topics == []
    assert repo.open_issues_count == 0
    assert repo.open_prs_count == 0


def test_code_repository_all_fields():
    repo = CodeRepository(
        id="hermes-os", name="avanzia/hermes-os", provider="github",
        url="https://github.com/avanzia/hermes-os",
        description="AI-first OS",
        default_branch="develop",
        language="Python",
        visibility="public",
        archived=False,
        open_issues_count=5,
        open_prs_count=2,
        last_push_at="2026-08-01T12:00:00Z",
        topics=["ai", "os"],
    )
    assert repo.language == "Python"
    assert repo.topics == ["ai", "os"]
    assert repo.last_push_at == "2026-08-01T12:00:00Z"


# -- GitHubProvider tests -----------------------------------------------------


def test_provider_not_configured_when_empty():
    provider = GitHubProvider(org="", token="")
    assert not provider.configured


def test_provider_not_configured_without_token():
    provider = GitHubProvider(org="avanzia", token="")
    assert not provider.configured


def test_provider_configured_with_both():
    provider = GitHubProvider(org="avanzia", token="ghp_test")
    assert provider.configured
    assert provider.org == "avanzia"


def test_provider_health_unconfigured():
    provider = GitHubProvider(org="", token="")
    health = provider.health()
    assert not health.configured
    assert not health.authenticated
    assert not health.reachable


def test_provider_list_repos_unconfigured():
    provider = GitHubProvider(org="", token="")
    assert provider.list_org_repos() == []


def test_provider_get_repo_unconfigured():
    provider = GitHubProvider(org="", token="")
    assert provider.get_repo("anything") is None


def test_provider_health_success():
    provider = GitHubProvider(org="avanzia", token="ghp_test")
    mock_data = {
        "rate": {"limit": 5000, "remaining": 4999, "reset": 1234567890},
    }
    with patch.object(provider, "_get", return_value=(mock_data, {})):
        health = provider.health()
    assert health.configured
    assert health.authenticated
    assert health.reachable
    assert health.rate_limit.limit == 5000
    assert health.rate_limit.remaining == 4999


def test_provider_list_repos_returns_data():
    provider = GitHubProvider(org="avanzia", token="ghp_test")
    repos = [{"name": "hermes-os"}, {"name": "website"}]
    with patch.object(provider, "_get", return_value=(repos, {})):
        result = provider.list_org_repos()
    assert len(result) == 2
    assert result[0]["name"] == "hermes-os"


def test_provider_get_repo_returns_data():
    provider = GitHubProvider(org="avanzia", token="ghp_test")
    repo_data = {"name": "hermes-os", "full_name": "avanzia/hermes-os"}
    with patch.object(provider, "_get", return_value=(repo_data, {})):
        result = provider.get_repo("hermes-os")
    assert result["name"] == "hermes-os"


def test_provider_get_repo_not_found():
    from urllib.error import HTTPError
    provider = GitHubProvider(org="avanzia", token="ghp_test")
    exc = HTTPError("url", 404, "Not Found", {}, None)
    with patch.object(provider, "_get", side_effect=exc):
        result = provider.get_repo("nonexistent")
    assert result is None


# -- GitHubRuntime tests -------------------------------------------------------


def test_runtime_unconfigured():
    provider = GitHubProvider(org="", token="")
    runtime = GitHubRuntime(provider)
    assert not runtime.configured
    assert runtime.list_repositories() == []
    assert runtime.get_repository("anything") is None


def test_runtime_list_repositories():
    provider = MagicMock(spec=GitHubProvider)
    provider.configured = True
    provider.list_org_repos.return_value = [
        {
            "name": "hermes-os",
            "full_name": "avanzia/hermes-os",
            "html_url": "https://github.com/avanzia/hermes-os",
            "description": "AI OS",
            "default_branch": "main",
            "language": "Python",
            "visibility": "private",
            "archived": False,
            "open_issues_count": 3,
            "pushed_at": "2026-08-01T12:00:00Z",
            "topics": ["ai"],
        },
    ]
    runtime = GitHubRuntime(provider)
    repos = runtime.list_repositories()
    assert len(repos) == 1
    repo = repos[0]
    assert isinstance(repo, CodeRepository)
    assert repo.id == "hermes-os"
    assert repo.provider == "github"
    assert repo.language == "Python"
    assert repo.description == "AI OS"


def test_runtime_get_repository():
    provider = MagicMock(spec=GitHubProvider)
    provider.configured = True
    provider.get_repo.return_value = {
        "name": "hermes-os",
        "full_name": "avanzia/hermes-os",
        "html_url": "https://github.com/avanzia/hermes-os",
        "description": "",
        "default_branch": "main",
        "language": "Python",
        "visibility": "private",
        "archived": False,
        "open_issues_count": 0,
        "pushed_at": None,
        "topics": [],
    }
    runtime = GitHubRuntime(provider)
    repo = runtime.get_repository("hermes-os")
    assert repo is not None
    assert repo.id == "hermes-os"


def test_runtime_get_repository_not_found():
    provider = MagicMock(spec=GitHubProvider)
    provider.configured = True
    provider.get_repo.return_value = None
    runtime = GitHubRuntime(provider)
    assert runtime.get_repository("nope") is None


def test_runtime_health_delegates():
    provider = MagicMock(spec=GitHubProvider)
    expected = HealthStatus(configured=True, authenticated=True, reachable=True, org="avanzia")
    provider.health.return_value = expected
    runtime = GitHubRuntime(provider)
    assert runtime.health() is expected


def test_runtime_to_model_handles_none_fields():
    """Ensure _to_model handles None in optional fields."""
    raw = {
        "name": "test",
        "full_name": "org/test",
        "html_url": "",
        "description": None,
        "default_branch": "main",
        "language": None,
        "visibility": "private",
        "archived": False,
        "open_issues_count": 0,
        "pushed_at": None,
        "topics": [],
    }
    repo = GitHubRuntime._to_model(raw)
    assert repo.description == ""
    assert repo.language == ""
    assert repo.last_push_at == ""
