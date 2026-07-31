"""Tests for DiagnosticsReport generation and character accounting."""
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from hermes.kernel.file_content_reader import MAX_CHARS_PER_FILE, MAX_TOTAL_CHARS
from hermes.models import (
    Context,
    DiagnosticsReport,
    ExecutionPlan,
    ExecutionResult,
    FileContent,
    KnowledgeContext,
    KnowledgeDocument,
    LoadedSkill,
    Project,
    Repository,
    Task,
    Workspace,
    WorkspaceContext,
    WorkspaceFile,
    WorkspaceSnapshot,
)
from hermes.service import HermesService

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file(path: str, content: str, repository: str = "hermes-os") -> WorkspaceFile:
    return WorkspaceFile(
        path=path,
        extension=path.rsplit(".", 1)[-1] if "." in path else "",
        size=len(content),
        content=content,
        repository=repository,
    )


def _snapshot(*files: WorkspaceFile) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(root="/tmp/avanzia", files=list(files))


def _make_doc(title: str, content: str) -> KnowledgeDocument:
    return KnowledgeDocument(id=title, title=title, path=f"/tmp/{title}.md", content=content)


def _make_repo(name: str) -> Repository:
    return Repository(
        name=name,
        path=f"/tmp/{name}",
        exists=True,
        is_git_repo=True,
        branch="main",
        is_clean=True,
        environment=[],
    )


def _mocked_service_with_data(
    *,
    files_before: list[WorkspaceFile] | None = None,
    files_after: list[WorkspaceFile] | None = None,
    docs: list[KnowledgeDocument] | None = None,
    repos: list[Repository] | None = None,
) -> tuple[HermesService, dict]:
    """Build a HermesService with mocked internals and controlled pipeline data."""
    task = Task(id="hermes-service", business="", request="do something")
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    knowledge = KnowledgeContext(project=project, documents=docs or [])
    workspace = WorkspaceContext(
        workspace=Workspace(project_id="AVANZIA", path="/tmp/avanzia"),
        exists=True,
        is_git_repo=True,
        branch="main",
        is_clean=True,
        environment=[],
        repositories=repos or [],
    )
    context = Context(
        task=task,
        project=project,
        knowledge=knowledge,
        workspace=workspace,
        capabilities=[],
    )
    plan = ExecutionPlan(task=task, project=project, context=context, steps=[])
    skills: list[LoadedSkill] = []

    _now = datetime.now()
    executor_result = ExecutionResult(
        task=task,
        project=project,
        completed_steps=[],
        status="awaiting_approval",
        started_at=_now,
        finished_at=_now,
        generated_output=None,
    )

    full_snapshot = _snapshot(*(files_before or []))
    selected_snapshot = _snapshot(*(files_after or files_before or []))

    mock_context_engine = MagicMock()
    mock_planner = MagicMock()
    mock_skill_loader = MagicMock()
    mock_workspace_reader = MagicMock()
    mock_file_selector = MagicMock()
    mock_file_content_reader = MagicMock()
    mock_executor = MagicMock()

    mock_context_engine.build.return_value = context
    mock_planner.create.return_value = plan
    mock_skill_loader.load.return_value = skills
    mock_workspace_reader.read.return_value = full_snapshot
    mock_file_selector.select.return_value = selected_snapshot
    mock_executor.execute.return_value = executor_result

    service = HermesService(
        context_engine=mock_context_engine,
        planner=mock_planner,
        skill_loader=mock_skill_loader,
        workspace_reader=mock_workspace_reader,
        file_selector=mock_file_selector,
        file_content_reader=mock_file_content_reader,
        executor=mock_executor,
    )

    return service, {
        "full_snapshot": full_snapshot,
        "selected_snapshot": selected_snapshot,
        "context": context,
        "mock_file_content_reader": mock_file_content_reader,
    }


# ---------------------------------------------------------------------------
# DiagnosticsReport generation
# ---------------------------------------------------------------------------

def test_diagnostics_project_id():
    service, extras = _mocked_service_with_data()
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics is not None
    assert result.diagnostics.project_id == "AVANZIA"


def test_diagnostics_repositories():
    repos = [_make_repo("hermes-os"), _make_repo("avanzia-website")]
    service, extras = _mocked_service_with_data(repos=repos)
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics.repositories == ["hermes-os", "avanzia-website"]


def test_diagnostics_knowledge_documents():
    docs = [_make_doc("Vision", "v"), _make_doc("Mission", "m")]
    service, extras = _mocked_service_with_data(docs=docs)
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics.knowledge_documents == ["Vision", "Mission"]


def test_diagnostics_files_scanned_counts_full_snapshot():
    files = [_file(f"file_{i}.py", f"content {i}") for i in range(10)]
    service, extras = _mocked_service_with_data(files_before=files, files_after=files[:3])
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics.files_scanned == 10


def test_diagnostics_files_selected_paths():
    files_after = [_file("src/service.py", "x"), _file("src/executor.py", "y")]
    service, extras = _mocked_service_with_data(files_after=files_after)
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics.files_selected == ["src/service.py", "src/executor.py"]


# ---------------------------------------------------------------------------
# Character accounting
# ---------------------------------------------------------------------------

def test_diagnostics_chars_read_from_reader():
    service, extras = _mocked_service_with_data()
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 42_000, 0)

    result = service.generate("do something")

    assert result.diagnostics.chars_read == 42_000


def test_diagnostics_chars_truncated_from_reader():
    service, extras = _mocked_service_with_data()
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 15_000, 5_000)

    result = service.generate("do something")

    assert result.diagnostics.chars_truncated == 5_000


def test_diagnostics_files_read_equals_returned_file_count():
    fc = [
        FileContent(repository="hermes-os", path="src/a.py", content="a = 1"),
        FileContent(repository="hermes-os", path="src/b.py", content="b = 2"),
    ]
    service, extras = _mocked_service_with_data()
    extras["mock_file_content_reader"].read_with_stats.return_value = (fc, 10, 0)

    result = service.generate("do something")

    assert result.diagnostics.files_read == 2


# ---------------------------------------------------------------------------
# Prompt size accounting
# ---------------------------------------------------------------------------

def test_diagnostics_knowledge_chars_sums_injected_docs():
    docs = [_make_doc("Vision", "A" * 100), _make_doc("Mission", "B" * 200)]
    service, extras = _mocked_service_with_data(docs=docs)
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    expected = sum(len(f"## {doc.title}\n\n{doc.content}") for doc in docs)
    assert result.diagnostics.knowledge_chars == expected


def test_diagnostics_file_content_chars_sums_file_content():
    fc = [
        FileContent(repository="hermes-os", path="src/a.py", content="x" * 500),
        FileContent(repository="hermes-os", path="src/b.py", content="y" * 300),
    ]
    service, extras = _mocked_service_with_data()
    extras["mock_file_content_reader"].read_with_stats.return_value = (fc, 800, 0)

    result = service.generate("do something")

    assert result.diagnostics.file_content_chars == 800


def test_diagnostics_prompt_chars_equals_knowledge_plus_file_content():
    docs = [_make_doc("Vision", "A" * 100)]
    fc = [FileContent(repository="hermes-os", path="src/a.py", content="x" * 500)]
    service, extras = _mocked_service_with_data(docs=docs)
    extras["mock_file_content_reader"].read_with_stats.return_value = (fc, 500, 0)

    result = service.generate("do something")

    d = result.diagnostics
    assert d.prompt_chars == d.knowledge_chars + d.file_content_chars


def test_diagnostics_knowledge_capped_at_three_docs():
    docs = [_make_doc(f"Doc {i}", "X" * 100) for i in range(5)]
    service, extras = _mocked_service_with_data(docs=docs)
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    # Only first 3 docs counted in knowledge_chars.
    expected = sum(len(f"## {doc.title}\n\n{doc.content}") for doc in docs[:3])
    assert result.diagnostics.knowledge_chars == expected


# ---------------------------------------------------------------------------
# Selection reporting
# ---------------------------------------------------------------------------

def test_diagnostics_files_selected_is_empty_when_no_files():
    service, extras = _mocked_service_with_data(files_before=[], files_after=[])
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics.files_selected == []


def test_diagnostics_files_selected_subset_of_scanned():
    all_files = [_file(f"file_{i}.py", f"content {i}") for i in range(20)]
    selected = all_files[:5]
    service, extras = _mocked_service_with_data(files_before=all_files, files_after=selected)
    extras["mock_file_content_reader"].read_with_stats.return_value = ([], 0, 0)

    result = service.generate("do something")

    assert result.diagnostics.files_scanned == 20
    assert len(result.diagnostics.files_selected) == 5


# ---------------------------------------------------------------------------
# Integration: real engines (no provider → no LLM call)
# ---------------------------------------------------------------------------

def test_diagnostics_populated_with_real_engines():
    service = HermesService()

    result = service.generate("AVANZIA: review the executor")

    assert result.diagnostics is not None
    assert result.diagnostics.project_id == "AVANZIA"
    assert isinstance(result.diagnostics.files_scanned, int)
    assert isinstance(result.diagnostics.files_read, int)
    assert isinstance(result.diagnostics.chars_read, int)
    assert result.diagnostics.files_read <= result.diagnostics.files_scanned
