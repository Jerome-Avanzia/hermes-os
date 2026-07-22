from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.models import (
    Context,
    KnowledgeContext,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
    WorkspaceSnapshot,
)
from hermes.providers.claude_provider import ClaudeConfigurationError, ClaudeProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


def _build_context(request: str) -> Context:
    task = Task(id="t", business="AVANZIA", request=request)
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    knowledge = KnowledgeContext(project=project, documents=[])
    workspace = WorkspaceContext(
        workspace=Workspace(project_id="AVANZIA", path="/tmp/avanzia"),
        exists=True,
        is_git_repo=True,
        branch="main",
        is_clean=True,
        environment=[],
    )
    capabilities = CapabilityEngine(skills_root=SKILLS_ROOT).match(task)

    return Context(
        task=task,
        project=project,
        knowledge=knowledge,
        workspace=workspace,
        capabilities=capabilities,
    )


def test_claude_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ClaudeConfigurationError):
        ClaudeProvider()


def test_claude_provider_generate_uses_mocked_client_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    context = _build_context("Refactor the Python backend")
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)
    workspace = WorkspaceSnapshot(root="/tmp/avanzia", files=[])

    text_block = MagicMock()
    text_block.text = "Generated proposal text"
    mock_response = MagicMock()
    mock_response.content = [text_block]

    with patch("hermes.providers.claude_provider.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        provider = ClaudeProvider()
        result = provider.generate(
            task=context.task,
            context=context,
            plan=plan,
            skills=skills,
            workspace=workspace,
        )

        assert result == "Generated proposal text"
        mock_client.messages.create.assert_called_once()

        _, call_kwargs = mock_client.messages.create.call_args
        prompt = call_kwargs["messages"][0]["content"]
        assert "Refactor the Python backend" in prompt
        assert "AVANZIA" in prompt
        assert "Python" in prompt
