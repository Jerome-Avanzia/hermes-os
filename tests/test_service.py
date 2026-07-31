from datetime import datetime
from unittest.mock import MagicMock

from hermes.models import (
    Context,
    ExecutionPlan,
    ExecutionResult,
    KnowledgeContext,
    LoadedSkill,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
    WorkspaceSnapshot,
)
from hermes.providers.ai_provider import AIProvider
from hermes.service import HermesService


def _fixture_context(task: Task) -> Context:
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    return Context(
        task=task,
        project=project,
        knowledge=KnowledgeContext(project=project, documents=[]),
        workspace=WorkspaceContext(
            workspace=Workspace(project_id="AVANZIA", path="/tmp/avanzia"),
            exists=True,
            is_git_repo=False,
            branch=None,
            is_clean=None,
            environment=[],
        ),
        capabilities=[],
    )


def _mocked_service():
    mock_context_engine = MagicMock()
    mock_planner = MagicMock()
    mock_skill_loader = MagicMock()
    mock_workspace_reader = MagicMock()
    mock_file_selector = MagicMock()
    mock_file_content_reader = MagicMock()
    mock_executor = MagicMock()

    task = Task(id="hermes-service", business="", request="do something")
    context = _fixture_context(task)
    plan = ExecutionPlan(task=task, project=context.project, context=context, steps=[])
    skills = [LoadedSkill(id="python", name="Python", version="1.0.0", path="skills/python")]
    workspace_snapshot = WorkspaceSnapshot(root="/tmp/avanzia", files=[])
    file_contents = []
    _now = datetime.now()
    executor_result = ExecutionResult(
        task=task,
        project=context.project,
        completed_steps=[],
        status="awaiting_approval",
        started_at=_now,
        finished_at=_now,
        generated_output=None,
    )

    mock_context_engine.build.return_value = context
    mock_planner.create.return_value = plan
    mock_skill_loader.load.return_value = skills
    mock_workspace_reader.read.return_value = workspace_snapshot
    mock_file_selector.select.return_value = workspace_snapshot
    mock_file_content_reader.read_with_stats.return_value = (file_contents, 0, 0)
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
        "context_engine": mock_context_engine,
        "planner": mock_planner,
        "skill_loader": mock_skill_loader,
        "workspace_reader": mock_workspace_reader,
        "file_selector": mock_file_selector,
        "file_content_reader": mock_file_content_reader,
        "executor": mock_executor,
        "context": context,
        "plan": plan,
        "skills": skills,
        "workspace_snapshot": workspace_snapshot,
        "file_contents": file_contents,
        "executor_result": executor_result,
    }


def test_generate_coordinates_engines_in_order_and_returns_executor_result():
    service, mocks = _mocked_service()

    result = service.generate("do something")

    # Service wraps executor result with diagnostics; check key fields rather than identity.
    assert isinstance(result, ExecutionResult)
    assert result.status == mocks["executor_result"].status
    assert result.completed_steps == mocks["executor_result"].completed_steps

    mocks["context_engine"].build.assert_called_once()
    built_task = mocks["context_engine"].build.call_args[0][0]
    assert isinstance(built_task, Task)
    assert built_task.request == "do something"

    mocks["planner"].create.assert_called_once_with(mocks["context"])
    mocks["skill_loader"].load.assert_called_once_with(mocks["plan"])
    mocks["workspace_reader"].read.assert_called_once_with(mocks["context"].workspace)
    mocks["file_content_reader"].read_with_stats.assert_called_once_with(mocks["workspace_snapshot"])
    mocks["executor"].execute.assert_called_once_with(
        mocks["plan"], mocks["skills"], mocks["workspace_snapshot"],
        provider=None, file_contents=mocks["file_contents"],
    )


def test_generate_passes_provider_through_to_executor():
    service, mocks = _mocked_service()
    fake_provider = MagicMock(spec=AIProvider)

    service.generate("do something", provider=fake_provider)

    mocks["executor"].execute.assert_called_once_with(
        mocks["plan"], mocks["skills"], mocks["workspace_snapshot"],
        provider=fake_provider, file_contents=mocks["file_contents"],
    )


def test_generate_does_not_duplicate_kernel_logic():
    """Executor drives status/steps; service only appends diagnostics."""
    service, mocks = _mocked_service()

    result = service.generate("do something")

    assert result.status == mocks["executor_result"].status
    assert result.completed_steps == mocks["executor_result"].completed_steps
    mocks["executor"].execute.assert_called_once()


def test_generate_attaches_diagnostics_to_result():
    service, mocks = _mocked_service()

    result = service.generate("do something")

    assert result.diagnostics is not None
    assert result.diagnostics.project_id == mocks["context"].project.id


def test_generate_passes_project_as_business_field():
    service, mocks = _mocked_service()

    service.generate("Implement streaming responses", project="AVANZIA")

    built_task = mocks["context_engine"].build.call_args[0][0]
    assert built_task.business == "AVANZIA"
    assert built_task.request == "Implement streaming responses"


def test_generate_with_real_engines_against_avanzia():
    service = HermesService()

    result = service.generate("AVANZIA: refactor the Python backend")

    assert isinstance(result, ExecutionResult)
    assert result.status == "awaiting_approval"
    assert result.completed_steps == ["Python"]
    assert result.generated_output is None
    assert result.diagnostics is not None
    assert result.diagnostics.project_id == "AVANZIA"
