from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.models import (
    Context,
    FileContent,
    KnowledgeContext,
    KnowledgeDocument,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
    WorkspaceSnapshot,
)
from hermes.models import WorkspaceFile
from hermes.providers.claude_provider import (
    ClaudeConfigurationError,
    ClaudeProvider,
    _build_file_contents_section,
    _build_workspace_file_summary,
)

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


def _make_doc(title: str, content: str) -> KnowledgeDocument:
    return KnowledgeDocument(id=title, title=title, path=f"/tmp/{title}.md", content=content)


def _build_minimal_prompt(
    request: str = "Test task",
    docs: list[KnowledgeDocument] | None = None,
    file_contents: list[FileContent] | None = None,
) -> str:
    task = Task(id="t", business="AVANZIA", request=request)
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    knowledge = KnowledgeContext(project=project, documents=docs or [])
    workspace_ctx = WorkspaceContext(
        workspace=Workspace(project_id="AVANZIA", path="/tmp/avanzia"),
        exists=True,
        is_git_repo=True,
        branch="main",
        is_clean=True,
        environment=[],
    )
    capabilities = CapabilityEngine(skills_root=SKILLS_ROOT).match(task)
    context = Context(
        task=task,
        project=project,
        knowledge=knowledge,
        workspace=workspace_ctx,
        capabilities=capabilities,
    )
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)
    workspace = WorkspaceSnapshot(root="/tmp/avanzia", files=[])
    return ClaudeProvider._build_prompt(task, context, plan, skills, workspace, file_contents)


def _prompt_with_docs(docs: list[KnowledgeDocument]) -> str:
    return _build_minimal_prompt(docs=docs)


def test_knowledge_document_content_is_injected_into_prompt(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    docs = [
        _make_doc("Vision", "# Vision\n\nThis is the vision content."),
        _make_doc("Mission", "# Mission\n\nThis is the mission content."),
    ]
    prompt = _prompt_with_docs(docs)

    assert "This is the vision content." in prompt
    assert "This is the mission content." in prompt
    assert "## Vision" in prompt
    assert "## Mission" in prompt


def test_knowledge_injection_limited_to_three_documents(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    docs = [_make_doc(f"Doc {i}", f"# Doc {i}\n\nContent of doc {i}.") for i in range(5)]
    prompt = _prompt_with_docs(docs)

    assert "Content of doc 0." in prompt
    assert "Content of doc 1." in prompt
    assert "Content of doc 2." in prompt
    assert "Content of doc 3." not in prompt
    assert "Content of doc 4." not in prompt


def _make_file(path: str, repository: str = "") -> WorkspaceFile:
    return WorkspaceFile(path=path, extension=".py", size=10, content="x", repository=repository)


def test_workspace_files_grouped_by_repository_in_prompt():
    files = [
        _make_file("src/main.py", repository="hermes-os"),
        _make_file("src/executor.py", repository="hermes-os"),
        _make_file("app/page.tsx", repository="avanzia-website"),
    ]
    summary = _build_workspace_file_summary(files)

    assert "[hermes-os]" in summary
    assert "[avanzia-website]" in summary
    assert "src/main.py" in summary
    assert "src/executor.py" in summary
    assert "app/page.tsx" in summary


def test_workspace_files_without_repository_use_flat_format():
    files = [
        _make_file("src/main.py"),
        _make_file("README.md"),
    ]
    summary = _build_workspace_file_summary(files)

    assert "[" not in summary
    assert "- src/main.py" in summary
    assert "- README.md" in summary


def test_empty_workspace_files_returns_dash():
    assert _build_workspace_file_summary([]) == "-"


# --- Prompt section structure ---

def test_prompt_contains_knowledge_section_header():
    prompt = _build_minimal_prompt()
    assert "=== Knowledge ===" in prompt


def test_prompt_contains_repository_files_section_header():
    prompt = _build_minimal_prompt()
    assert "=== Repository Files ===" in prompt


def test_prompt_contains_user_request_section_header():
    prompt = _build_minimal_prompt(request="Deploy the API server")
    assert "=== User Request ===" in prompt


def test_user_request_appears_in_user_request_section():
    prompt = _build_minimal_prompt(request="Deploy the API server")
    user_request_pos = prompt.index("=== User Request ===")
    request_pos = prompt.index("Deploy the API server")
    assert request_pos > user_request_pos


def test_knowledge_section_precedes_repository_files_section():
    prompt = _build_minimal_prompt()
    assert prompt.index("=== Knowledge ===") < prompt.index("=== Repository Files ===")


def test_repository_files_section_precedes_user_request_section():
    prompt = _build_minimal_prompt()
    assert prompt.index("=== Repository Files ===") < prompt.index("=== User Request ===")


# --- File content injection ---

def test_file_contents_rendered_with_repository_and_path():
    fc = FileContent(repository="hermes-os", path="src/hermes/service.py", content="class HermesService: pass")
    section = _build_file_contents_section([fc])
    assert "Repository: hermes-os" in section
    assert "File: src/hermes/service.py" in section
    assert "class HermesService: pass" in section


def test_file_contents_rendered_with_code_fence_language():
    fc = FileContent(repository="hermes-os", path="src/hermes/service.py", content="x = 1")
    section = _build_file_contents_section([fc])
    assert "```python" in section


def test_file_contents_without_repository_omit_repository_line():
    fc = FileContent(repository="", path="README.md", content="# Readme")
    section = _build_file_contents_section([fc])
    assert "Repository:" not in section
    assert "File: README.md" in section


def test_empty_file_contents_returns_dash():
    assert _build_file_contents_section([]) == "-"


def test_file_contents_injected_into_prompt():
    fc = FileContent(repository="hermes-os", path="src/hermes/service.py", content="class HermesService: pass")
    prompt = _build_minimal_prompt(file_contents=[fc])
    assert "class HermesService: pass" in prompt
    assert "=== Repository Files ===" in prompt
