from datetime import datetime
from unittest.mock import MagicMock, patch

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


# -- Executive Decision Loop (Sprint 29) ------------------------------------


def _decision_service(tmp_path):
    """Build a service with real stores and a real business directory."""
    import shutil
    from pathlib import Path

    from hermes.kernel.decision_engine import Recommendation, DimensionScore
    from hermes.kernel.ceo_loop import CEOReviewResult
    from hermes.kernel.job_store import JobStore
    from hermes.kernel.operation_store import OperationStore
    from hermes.models import ExecutiveBrief

    service, mocks = _mocked_service()
    op_store = OperationStore(workspaces_root=tmp_path)
    job_store = JobStore(workspaces_root=tmp_path)
    service.operation_store = op_store
    service.job_store = job_store

    # Set up a temp business dir with Decisions.md
    business_dir = tmp_path / "businesses" / "TEST"
    business_dir.mkdir(parents=True)
    src = Path("businesses/AVANZIA/Decisions.md")
    shutil.copy2(src, business_dir / "Decisions.md")

    # Mock workspace_engine.resolve_business_dir to return our temp dir
    mocks["context_engine"].workspace_engine.resolve_business_dir.return_value = business_dir

    # Build a mock CEOReviewResult with one recommendation
    from hermes.kernel.business_data_loader import BusinessData
    from hermes.kernel.decision_engine import EngineResult

    rec = Recommendation(
        recommendation_id="rec_bot_001",
        business_id="TEST",
        title="Address sales bottleneck",
        context="Sales pipeline is constrained",
        rationale="Improving sales will increase revenue by 30%.",
        source_type="bottleneck",
        source_id="BOT-001",
        dimension_scores=[],
        priority_score=4.2,
        confidence="high",
        suggested_action="decide",
    )

    brief = ExecutiveBrief(
        brief_id="brief_TEST_2026-08-01",
        business_id="TEST",
        reporting_period="daily",
        generated_at="2026-08-01T00:00:00+00:00",
        summary="Test brief",
        priorities=[],
        recommendations=[],
        risks=[],
        status="draft",
    )

    review = CEOReviewResult(
        review_id="review_2026-08-01T00:00:00+00:00",
        business_id="TEST",
        data=BusinessData(),
        engine_result=EngineResult(
            business_id="TEST",
            recommendations=[rec],
        ),
        brief=brief,
        steps=[],
        execution_status="completed",
    )

    return service, mocks, review, business_dir


def test_act_on_recommendation_approve_creates_decision(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        result = service.act_on_recommendation("AVANZIA", "rec_bot_001", "approve")

    dec = result["decision"]
    assert dec["id"] == "DEC-007"
    assert dec["status"] == "approved"
    assert dec["recommendation_id"] == "rec_bot_001"
    assert dec["review_id"] == "review_2026-08-01T00:00:00+00:00"
    assert dec["brief_id"] == "brief_TEST_2026-08-01"
    assert result["operation"] is None


def test_act_on_recommendation_reject_creates_closed_decision(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        result = service.act_on_recommendation("AVANZIA", "rec_bot_001", "reject")

    assert result["decision"]["status"] == "closed"


def test_act_on_recommendation_postpone_creates_proposed_decision(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        result = service.act_on_recommendation("AVANZIA", "rec_bot_001", "postpone")

    assert result["decision"]["status"] == "proposed"


def test_act_on_recommendation_approve_with_operation(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        result = service.act_on_recommendation(
            "AVANZIA", "rec_bot_001", "approve", create_operation=True,
        )

    assert result["operation"] is not None
    assert result["operation"]["id"].startswith("OP-")
    assert "[DEC-007]" in result["operation"]["request"]


def test_act_on_recommendation_unknown_raises(tmp_path):
    from hermes.service import RecommendationNotFoundError

    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        with pytest.raises(RecommendationNotFoundError):
            service.act_on_recommendation("AVANZIA", "nonexistent", "approve")


def test_act_on_recommendation_invalid_action_raises(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        with pytest.raises(ValueError, match="Invalid action"):
            service.act_on_recommendation("AVANZIA", "rec_bot_001", "invalid")


def test_act_on_recommendation_appends_to_decisions_md(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        service.act_on_recommendation("AVANZIA", "rec_bot_001", "approve")

    text = (business_dir / "Decisions.md").read_text()
    assert "DEC-007" in text
    assert "Address sales" in text


def test_act_on_recommendation_decision_has_traceability(tmp_path):
    service, mocks, review, business_dir = _decision_service(tmp_path)

    with patch.object(
        service, "_run_ceo_review", return_value=review
    ):
        result = service.act_on_recommendation("AVANZIA", "rec_bot_001", "approve")

    dec = result["decision"]
    assert dec["recommendation_id"] is not None
    assert dec["review_id"] is not None
    assert dec["brief_id"] is not None


# -- Sprint 30: Operation Completion -----------------------------------------


def _operation_service(tmp_path):
    """Build a service with operation stores and a business dir with Operations.md."""
    import shutil
    from pathlib import Path
    from hermes.kernel.operation_store import OperationStore
    from hermes.models import Operation

    service, mocks = _mocked_service()
    op_store = OperationStore(workspaces_root=tmp_path)
    service.operation_store = op_store

    # Set up a temp business dir with Operations.md
    business_dir = tmp_path / "businesses" / "TEST"
    business_dir.mkdir(parents=True)
    src = Path("businesses/AVANZIA/Operations.md")
    shutil.copy2(src, business_dir / "Operations.md")

    mocks["context_engine"].workspace_engine.resolve_business_dir.return_value = business_dir

    # Create an executing operation
    from datetime import datetime, timezone
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    op = Operation(
        id="OP-20260803-001",
        workspace_id="AVANZIA",
        request="Launch campaign",
        status="executing",
        created_at=now,
        updated_at=now,
        decision_id="DEC-007",
        recommendation_id="rec_bot_001",
        review_id="review_2026-08-01",
    )
    op_store.save(op)

    return service, mocks, business_dir, op


def test_complete_operation_transitions_and_writes_md(tmp_path):
    service, mocks, business_dir, op = _operation_service(tmp_path)

    result = service.complete_operation("AVANZIA", "OP-20260803-001", "Delivered on time", "success")

    assert result["operation"]["status"] == "completed"
    assert result["operation"]["outcome"] == "Delivered on time"
    assert result["operation"]["outcome_classification"] == "success"
    assert result["bk_operation_id"] == "OPS-001"

    # Check Operations.md was updated
    text = (business_dir / "Operations.md").read_text()
    assert "OPS-001" in text
    assert "Completed" in text


def test_fail_operation_transitions_and_writes_md(tmp_path):
    service, mocks, business_dir, op = _operation_service(tmp_path)

    result = service.fail_operation("AVANZIA", "OP-20260803-001", "Blocked by vendor")

    assert result["operation"]["status"] == "failed"
    assert result["operation"]["outcome"] == "Blocked by vendor"
    assert result["operation"]["outcome_classification"] == "failure"

    text = (business_dir / "Operations.md").read_text()
    assert "OPS-001" in text
    assert "Failed" in text


def test_complete_operation_invalid_state_raises(tmp_path):
    """Completing a non-executing operation should raise InvalidTransitionError."""
    from hermes.models.operation import InvalidTransitionError

    service, mocks, business_dir, op = _operation_service(tmp_path)

    # First complete it
    service.complete_operation("AVANZIA", "OP-20260803-001", "Done")

    # Trying to complete again should fail
    with pytest.raises(InvalidTransitionError):
        service.complete_operation("AVANZIA", "OP-20260803-001", "Again")


def test_complete_operation_preserves_traceability(tmp_path):
    service, mocks, business_dir, op = _operation_service(tmp_path)

    result = service.complete_operation("AVANZIA", "OP-20260803-001", "Done")

    assert result["operation"]["decision_id"] == "DEC-007"
    assert result["operation"]["recommendation_id"] == "rec_bot_001"
    assert result["operation"]["review_id"] == "review_2026-08-01"


def test_act_on_recommendation_operation_has_traceability(tmp_path):
    """Operations created via act_on_recommendation include traceability fields."""
    service, mocks, review, business_dir = _decision_service(tmp_path)
    from hermes.kernel.operation_store import OperationStore
    service.operation_store = OperationStore(workspaces_root=tmp_path)
    # Create the operations directory
    (tmp_path / "AVANZIA" / "operations").mkdir(parents=True, exist_ok=True)

    with patch.object(service, "_run_ceo_review", return_value=review):
        result = service.act_on_recommendation(
            "AVANZIA", "rec_bot_001", "approve", create_operation=True,
        )

    op = result["operation"]
    assert op is not None
    assert op["decision_id"] == "DEC-007"
    assert op["recommendation_id"] == "rec_bot_001"
    assert op["review_id"] == "review_2026-08-01T00:00:00+00:00"


def test_serialize_review_includes_lessons(tmp_path):
    """_serialize_review should include lessons from business data."""
    service, mocks, review, business_dir = _decision_service(tmp_path)
    from hermes.models import Lesson
    review.data.lessons = [
        Lesson(
            lesson_id="LES-001",
            business_id="TEST",
            title="Test lesson",
            summary="Test",
            source="review",
            recommendation="Do this",
            date="2026-08",
        )
    ]

    serialized = service._serialize_review(review)
    assert "lessons" in serialized
    assert len(serialized["lessons"]) == 1
    assert serialized["lessons"][0]["id"] == "LES-001"


# -- Sprint 31: Capability Runtime -------------------------------------------


def test_list_capabilities_delegates_to_registry():
    service, mocks = _mocked_service()
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models import Capability

    reg = MagicMock(spec=CapabilityRegistry)
    reg.list.return_value = [
        Capability(
            id="python", name="Python", version="1.0.0",
            provides=["python dev"], keywords=["python"],
            description="Dev", status="active", skill_id="python",
        ),
    ]
    mocks["context_engine"].capability_engine.registry = reg

    result = service.list_capabilities("AVANZIA")
    assert len(result) == 1
    assert result[0]["id"] == "python"
    assert result[0]["status"] == "active"
    reg.list.assert_called_once()


def test_get_capability_delegates_to_registry():
    service, mocks = _mocked_service()
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models import Capability

    reg = MagicMock(spec=CapabilityRegistry)
    reg.get.return_value = Capability(
        id="copywriting", name="Copywriting", version="1.0.0",
        provides=["marketing copy"], keywords=["copy"],
        description="Drafts copy", status="active", skill_id="copywriting",
        inputs=["brief"], outputs=["draft"], owner="Marketing",
        depends_on=["brand-strategy"],
    )
    mocks["context_engine"].capability_engine.registry = reg

    result = service.get_capability("AVANZIA", "copywriting")
    assert result is not None
    assert result["id"] == "copywriting"
    assert result["inputs"] == ["brief"]
    assert result["outputs"] == ["draft"]
    assert result["owner"] == "Marketing"
    assert result["depends_on"] == ["brand-strategy"]


def test_get_capability_not_found_returns_none():
    service, mocks = _mocked_service()
    from hermes.kernel.capability_registry import CapabilityRegistry

    reg = MagicMock(spec=CapabilityRegistry)
    reg.get.return_value = None
    mocks["context_engine"].capability_engine.registry = reg

    assert service.get_capability("AVANZIA", "nonexistent") is None


# -- Sprint 32: SOP Runtime ---------------------------------------------------


def test_list_sops_delegates_to_registry():
    service, mocks = _mocked_service()
    from hermes.kernel.sop_registry import SOPRegistry
    from hermes.models.sop import SOP

    reg = MagicMock(spec=SOPRegistry)
    reg.list.return_value = [
        SOP(
            id="copywriting/content-review", title="Content Review Process",
            skill_id="copywriting", filename="content-review.md",
            content="# Content Review Process\n\nDetails.",
            description="Standard procedure.", version="1.0.0",
            status="active", owner="Marketing", category="Marketing",
        ),
    ]
    service.sop_registry = reg

    result = service.list_sops("AVANZIA")
    assert len(result) == 1
    assert result[0]["id"] == "copywriting/content-review"
    assert result[0]["title"] == "Content Review Process"
    assert result[0]["category"] == "Marketing"
    # Summary should NOT include content
    assert "content" not in result[0]
    reg.list.assert_called_once()


def test_get_sop_delegates_to_registry():
    service, mocks = _mocked_service()
    from hermes.kernel.sop_registry import SOPRegistry
    from hermes.models.sop import SOP

    reg = MagicMock(spec=SOPRegistry)
    reg.get.return_value = SOP(
        id="copywriting/content-review", title="Content Review Process",
        skill_id="copywriting", filename="content-review.md",
        content="# Content Review Process\n\nDetails.",
        description="Standard procedure.", version="1.0.0",
        status="active", owner="Marketing", category="Marketing",
    )
    service.sop_registry = reg

    result = service.get_sop("AVANZIA", "copywriting/content-review")
    assert result is not None
    assert result["id"] == "copywriting/content-review"
    assert result["content"] == "# Content Review Process\n\nDetails."
    assert result["category"] == "Marketing"
    assert result["filename"] == "content-review.md"


def test_get_sop_not_found_returns_none():
    service, mocks = _mocked_service()
    from hermes.kernel.sop_registry import SOPRegistry

    reg = MagicMock(spec=SOPRegistry)
    reg.get.return_value = None
    service.sop_registry = reg

    assert service.get_sop("AVANZIA", "nonexistent/sop") is None


def test_capability_serialization_includes_sop_refs():
    service, mocks = _mocked_service()
    from hermes.kernel.capability_registry import CapabilityRegistry
    from hermes.models import Capability

    reg = MagicMock(spec=CapabilityRegistry)
    reg.get.return_value = Capability(
        id="copywriting", name="Copywriting", version="1.0.0",
        provides=["marketing copy"], keywords=["copy"],
        description="Drafts copy", status="active", skill_id="copywriting",
        sop_refs=["copywriting/content-review"],
    )
    mocks["context_engine"].capability_engine.registry = reg

    result = service.get_capability("AVANZIA", "copywriting")
    assert result["sop_refs"] == ["copywriting/content-review"]
