from datetime import datetime
from unittest.mock import MagicMock

import pytest

from hermes.models import (
    Context,
    ExecutionPlan,
    ExecutionResult,
    KnowledgeContext,
    LoadedSkill,
    Profile,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
    WorkspaceSnapshot,
)
from hermes.providers.ai_provider import AIProvider
from hermes.providers.ollama_provider import ChatMessage
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


# -- Conversation path (stream_chat / chat) --------------------------------


def _conversation_context() -> Context:
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    return Context(
        task=Task(id="conversation", business="AVANZIA", request=""),
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
        profile=Profile(id="default", name="Hermes", system_prompt="You are Hermes."),
    )


def test_stream_chat_delegates_to_context_engine_and_conductor():
    mock_context_engine = MagicMock()
    mock_conductor = MagicMock()
    context = _conversation_context()
    mock_context_engine.build_conversation.return_value = context
    mock_conductor.stream_chat_with_context.return_value = iter(["Hello", " world"])

    service = HermesService(context_engine=mock_context_engine, conductor=mock_conductor)
    messages = [ChatMessage(role="user", content="Hi")]
    tokens = list(service.stream_chat(messages, workspace_id="AVANZIA"))

    assert tokens == ["Hello", " world"]
    mock_context_engine.build_conversation.assert_called_once_with("AVANZIA", None, query="Hi")
    mock_conductor.stream_chat_with_context.assert_called_once_with(messages, context)


def test_stream_chat_passes_profile_id():
    mock_context_engine = MagicMock()
    mock_conductor = MagicMock()
    context = _conversation_context()
    mock_context_engine.build_conversation.return_value = context
    mock_conductor.stream_chat_with_context.return_value = iter(["OK"])

    service = HermesService(context_engine=mock_context_engine, conductor=mock_conductor)
    messages = [ChatMessage(role="user", content="Hi")]
    list(service.stream_chat(messages, workspace_id="AVANZIA", profile_id="developer"))

    mock_context_engine.build_conversation.assert_called_once_with("AVANZIA", "developer", query="Hi")


def test_chat_returns_joined_tokens():
    mock_context_engine = MagicMock()
    mock_conductor = MagicMock()
    context = _conversation_context()
    mock_context_engine.build_conversation.return_value = context
    mock_conductor.stream_chat_with_context.return_value = iter(["Hello", " world"])

    service = HermesService(context_engine=mock_context_engine, conductor=mock_conductor)
    messages = [ChatMessage(role="user", content="Hi")]
    result = service.chat(messages, workspace_id="AVANZIA")

    assert result == "Hello world"


def test_stream_chat_passes_last_user_message_as_query():
    mock_context_engine = MagicMock()
    mock_conductor = MagicMock()
    context = _conversation_context()
    mock_context_engine.build_conversation.return_value = context
    mock_conductor.stream_chat_with_context.return_value = iter(["OK"])

    service = HermesService(context_engine=mock_context_engine, conductor=mock_conductor)
    messages = [
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi there"),
        ChatMessage(role="user", content="What is the brand personality?"),
    ]
    list(service.stream_chat(messages, workspace_id="AVANZIA"))

    mock_context_engine.build_conversation.assert_called_once_with(
        "AVANZIA", None, query="What is the brand personality?"
    )


def test_stream_chat_raises_without_conductor():
    service = HermesService()
    messages = [ChatMessage(role="user", content="Hi")]

    with pytest.raises(RuntimeError, match="Conductor"):
        list(service.stream_chat(messages, workspace_id="AVANZIA"))


# -- Operation & Job integration (Sprint 13) --------------------------------


def _mocked_service_with_stores(tmp_path):
    from hermes.kernel.job_store import JobStore
    from hermes.kernel.operation_store import OperationStore

    service, mocks = _mocked_service()
    op_store = OperationStore(workspaces_root=tmp_path)
    job_store = JobStore(workspaces_root=tmp_path)
    service.operation_store = op_store
    service.job_store = job_store
    return service, mocks, op_store, job_store


def test_generate_with_stores_creates_operation(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    service.generate("do something")
    ops = op_store.list("AVANZIA")
    assert len(ops) == 1
    assert ops[0].status == "completed"
    assert ops[0].request == "do something"


def test_generate_with_stores_creates_job(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    service.generate("do something")
    jobs = job_store.list("AVANZIA")
    assert len(jobs) == 1
    assert jobs[0].operation_id == op_store.list("AVANZIA")[0].id


def test_generate_without_stores_works_unchanged():
    service, mocks = _mocked_service()
    result = service.generate("do something")
    assert isinstance(result, ExecutionResult)


def test_generate_returns_execution_result_unchanged(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    result = service.generate("do something")
    assert isinstance(result, ExecutionResult)
    assert result.status == mocks["executor_result"].status


def test_job_captures_execution_result_fields(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    service.generate("do something")
    job = job_store.list("AVANZIA")[0]
    er = mocks["executor_result"]
    assert job.status == er.status
    assert job.completed_steps == er.completed_steps


def test_generate_with_stores_handles_execution_failure(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    mocks["executor"].execute.side_effect = RuntimeError("Boom")

    with pytest.raises(RuntimeError, match="Boom"):
        service.generate("do something")

    ops = op_store.list("AVANZIA")
    assert len(ops) == 1
    assert ops[0].status == "failed"
    assert job_store.list("AVANZIA") == []


# -- list/get delegation (Sprint 13) ----------------------------------------


def test_list_operations_delegates_to_store(tmp_path):
    from hermes.kernel.operation_store import OperationStore

    service = HermesService(operation_store=OperationStore(workspaces_root=tmp_path))
    assert service.list_operations("AVANZIA") == []


def test_list_operations_without_store_returns_empty():
    service = HermesService()
    assert service.list_operations("AVANZIA") == []


def test_get_operation_without_store_returns_none():
    service = HermesService()
    assert service.get_operation("AVANZIA", "OP-NONEXISTENT") is None


def test_list_jobs_without_store_returns_empty():
    service = HermesService()
    assert service.list_jobs("AVANZIA") == []


def test_get_job_without_store_returns_none():
    service = HermesService()
    assert service.get_job("AVANZIA", "JOB-NONEXISTENT") is None


# -- Serialization (Sprint 14) ----------------------------------------------


def test_serialize_operation_excludes_extra_fields(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    service.generate("do something")
    ops = service.list_operations("AVANZIA")
    assert len(ops) == 1
    assert "extra_fields" not in ops[0]
    assert "id" in ops[0]
    assert "status" in ops[0]


def test_serialize_job_list_excludes_generated_output(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    service.generate("do something")
    jobs = service.list_jobs("AVANZIA")
    assert len(jobs) == 1
    assert "generated_output" not in jobs[0]


def test_serialize_job_detail_includes_generated_output(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    service.generate("do something")
    jobs = job_store.list("AVANZIA")
    detail = service.get_job("AVANZIA", jobs[0].id)
    assert "generated_output" in detail


# -- Conversation-to-Operation Bridge (Sprint 18) ---------------------------


def test_create_operation_from_chat_creates_operation(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    result = service.create_operation_from_chat("AVANZIA", "Generate homepage copy")
    ops = op_store.list("AVANZIA")
    assert len(ops) == 1
    assert ops[0].status == "created"
    assert ops[0].request == "Generate homepage copy"


def test_create_operation_from_chat_returns_serialized_dict(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    result = service.create_operation_from_chat("AVANZIA", "Generate homepage copy")
    assert isinstance(result, dict)
    assert "id" in result
    assert result["workspace_id"] == "AVANZIA"
    assert result["request"] == "Generate homepage copy"
    assert result["status"] == "created"
    assert "created_at" in result
    assert "updated_at" in result
    assert "extra_fields" not in result


def test_create_operation_from_chat_persists_to_store(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    result = service.create_operation_from_chat("AVANZIA", "Test task")
    loaded = op_store.load("AVANZIA", result["id"])
    assert loaded.request == "Test task"
    assert loaded.status == "created"


def test_create_operation_from_chat_without_stores_raises():
    service, mocks = _mocked_service()
    with pytest.raises(RuntimeError, match="OperationStore"):
        service.create_operation_from_chat("AVANZIA", "Test task")


def test_create_operation_from_chat_generates_unique_id(tmp_path):
    service, mocks, op_store, job_store = _mocked_service_with_stores(tmp_path)
    r1 = service.create_operation_from_chat("AVANZIA", "Task 1")
    r2 = service.create_operation_from_chat("AVANZIA", "Task 2")
    assert r1["id"] != r2["id"]
    assert r1["id"].startswith("OP-")
    assert r2["id"].startswith("OP-")
