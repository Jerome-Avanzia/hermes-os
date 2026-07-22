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
    mock_executor = MagicMock()

    task = Task(id="hermes-service", business="", request="do something")
    context = _fixture_context(task)
    plan = ExecutionPlan(task=task, project=context.project, context=context, steps=[])
    skills = [LoadedSkill(id="python", name="Python", version="1.0.0", path="skills/python")]
    workspace_snapshot = WorkspaceSnapshot(root="/tmp/avanzia", files=[])
    expected_result = MagicMock(spec=ExecutionResult)

    mock_context_engine.build.return_value = context
    mock_planner.create.return_value = plan
    mock_skill_loader.load.return_value = skills
    mock_workspace_reader.read.return_value = workspace_snapshot
    mock_executor.execute.return_value = expected_result

    service = HermesService(
        context_engine=mock_context_engine,
        planner=mock_planner,
        skill_loader=mock_skill_loader,
        workspace_reader=mock_workspace_reader,
        executor=mock_executor,
    )

    return service, {
        "context_engine": mock_context_engine,
        "planner": mock_planner,
        "skill_loader": mock_skill_loader,
        "workspace_reader": mock_workspace_reader,
        "executor": mock_executor,
        "context": context,
        "plan": plan,
        "skills": skills,
        "workspace_snapshot": workspace_snapshot,
        "expected_result": expected_result,
    }


def test_generate_coordinates_engines_in_order_and_returns_executor_result():
    service, mocks = _mocked_service()

    result = service.generate("do something")

    assert result is mocks["expected_result"]

    mocks["context_engine"].build.assert_called_once()
    built_task = mocks["context_engine"].build.call_args[0][0]
    assert isinstance(built_task, Task)
    assert built_task.request == "do something"

    mocks["planner"].create.assert_called_once_with(mocks["context"])
    mocks["skill_loader"].load.assert_called_once_with(mocks["plan"])
    mocks["workspace_reader"].read.assert_called_once_with(mocks["context"].workspace)
    mocks["executor"].execute.assert_called_once_with(
        mocks["plan"], mocks["skills"], mocks["workspace_snapshot"], provider=None
    )


def test_generate_passes_provider_through_to_executor():
    service, mocks = _mocked_service()
    fake_provider = MagicMock(spec=AIProvider)

    service.generate("do something", provider=fake_provider)

    mocks["executor"].execute.assert_called_once_with(
        mocks["plan"], mocks["skills"], mocks["workspace_snapshot"], provider=fake_provider
    )


def test_generate_does_not_duplicate_kernel_logic():
    """The service must not compute results itself -- only forward the
    Executor's return value untouched."""
    service, mocks = _mocked_service()

    result = service.generate("do something")

    assert result is mocks["expected_result"]
    mocks["executor"].execute.assert_called_once()


def test_generate_with_real_engines_against_avanzia():
    service = HermesService()

    result = service.generate("AVANZIA: refactor the Python backend")

    assert isinstance(result, ExecutionResult)
    assert result.status == "awaiting_approval"
    assert result.completed_steps == ["Python"]
    assert result.generated_output is None
