"""Tests for Phase 6 — EngineeringPlanner.

Coverage:
  - plan(): valid two-operation JSON → EngineeringPlan with N=2
  - plan(): single-operation plan (N=1) is valid
  - plan(): ambiguous confidence → success=True, plan.confidence="ambiguous", candidates populated
  - plan(): gateway dispatch failure → success=False
  - plan(): LLM adapter failure → success=False
  - plan(): malformed JSON → success=False, error starts "json_parse_error"
  - plan(): missing required fields → success=False
  - plan(): invalid intent value → success=False
  - plan(): depends_on absent in JSON → parsed as empty tuple
  - plan(): cycle in depends_on → success=False, error starts "plan_cycle_detected"
  - plan(): operations sorted by list order in plan.operations
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from hermes.adapters.llm_adapter import LlmAdapter
from hermes.kernel.engineering_planner import EngineeringPlanner
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.models.engineering_workflow import FounderGoal
from hermes.models.repository_intelligence import RepositoryFile, RepositorySnapshot
from hermes.models.execution_gateway import (
    AdapterRegistration,
    ExecutionAdapter,
    ExecutionStatus,
)
from hermes.models.llm_adapter import (
    AdapterConfiguration,
    AdapterExecutionResult,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_llm_config() -> AdapterConfiguration:
    return AdapterConfiguration(
        provider=LLMProvider.OLLAMA,
        model="test-model",
        base_url="http://localhost:11434",
        api_key="",
        max_tokens=4096,
        timeout_seconds=30,
        temperature=0.0,
    )


def _make_snapshot(
    *,
    snapshot_id: str = "snap-abc123",
    file_count: int = 3,
    files: tuple | None = None,
) -> RepositorySnapshot:
    if files is None:
        files = (
            RepositoryFile(path="src/hello.py", extension=".py", size_bytes=100, is_directory=False),
            RepositoryFile(path="src/utils.py", extension=".py", size_bytes=200, is_directory=False),
            RepositoryFile(path="tests/test_hello.py", extension=".py", size_bytes=150, is_directory=False),
        )
    return RepositorySnapshot(
        snapshot_id=snapshot_id,
        repository_path="my-project",
        scanned_at="2026-08-08T00:00:00+00:00",
        primary_language="python",
        languages=(),
        build_system=None,
        entry_points=(),
        test_locations=(),
        config_files=(),
        documentation=(),
        files=files,
        file_count=file_count,
        directory_count=1,
        total_size_bytes=450,
        git_present=True,
        metadata=(),
    )


def _make_goal(goal_id: str = "test-goal") -> FounderGoal:
    return FounderGoal(
        goal_id=goal_id,
        description="Add a hello function to the module",
        workspace_path="/workspace",
        repository_path="my-project",
        output_path="",
    )


def _make_gateway() -> ExecutionGateway:
    gw = ExecutionGateway()
    gw.register(AdapterRegistration(
        adapter=ExecutionAdapter.LLM,
        adapter_id="llm-test",
        available=True,
        description="Mock LLM",
    ))
    return gw


def _make_llm_success(request_id: str, operation_id: str, content: str) -> AdapterExecutionResult:
    llm_request = LLMRequest(
        request_id=request_id,
        provider=LLMProvider.OLLAMA,
        model="test-model",
        system_prompt="",
        user_prompt="test",
        max_tokens=4096,
        temperature=0.0,
        streaming=False,
        structured_output_schema="",
        metadata=(),
    )
    llm_response = LLMResponse(
        request_id=request_id,
        provider=LLMProvider.OLLAMA,
        model="test-model",
        content=content,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        streaming_used=False,
        structured_output_used=False,
        finish_reason="stop",
        metadata=(),
    )
    return AdapterExecutionResult(
        request_id=request_id,
        operation_id=operation_id,
        provider=LLMProvider.OLLAMA,
        llm_request=llm_request,
        llm_response=llm_response,
        success=True,
        error=None,
        adapter_metadata=(("action", "generate"),),
    )


def _make_llm_failure(request_id: str, operation_id: str, error: str) -> AdapterExecutionResult:
    return AdapterExecutionResult(
        request_id=request_id,
        operation_id=operation_id,
        provider=LLMProvider.OLLAMA,
        llm_request=None,
        llm_response=None,
        success=False,
        error=error,
        adapter_metadata=(),
    )


def _make_planner(mock_llm: MagicMock | None = None) -> tuple[EngineeringPlanner, MagicMock]:
    gw = _make_gateway()
    if mock_llm is None:
        mock_llm = MagicMock(spec=LlmAdapter)
    op_engine = OperationEngine()
    planner = EngineeringPlanner(
        gateway=gw,
        llm_adapter=mock_llm,
        llm_config=_make_llm_config(),
        operation_engine=op_engine,
    )
    return planner, mock_llm


_TWO_OP_PLAN = json.dumps({
    "confidence": "high",
    "basis": "Two files need changes",
    "operations": [
        {
            "operation_id": "op-0",
            "target": "src/hello.py",
            "intent": "create",
            "goal": "Create hello module with greet function",
            "depends_on": [],
        },
        {
            "operation_id": "op-1",
            "target": "src/__init__.py",
            "intent": "modify",
            "goal": "Export greet from hello module",
            "depends_on": ["op-0"],
        },
    ],
    "candidates": [],
})

_ONE_OP_PLAN = json.dumps({
    "confidence": "high",
    "basis": "Single file change",
    "operations": [
        {
            "operation_id": "op-0",
            "target": "src/hello.py",
            "intent": "create",
            "goal": "Create hello module",
            "depends_on": [],
        },
    ],
    "candidates": [],
})

_AMBIGUOUS_PLAN = json.dumps({
    "confidence": "ambiguous",
    "basis": "Multiple files are plausible",
    "operations": [],
    "candidates": ["src/api.py", "src/handlers.py"],
})


# ── Test fixtures ─────────────────────────────────────────────────────────────


class TestPlannerTwoOperations:
    def test_two_op_plan_success(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is True
        assert result.plan is not None
        assert len(result.plan.operations) == 2

    def test_two_op_plan_operation_ids(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        op_ids = [op.operation_id for op in result.plan.operations]
        assert "op-0" in op_ids
        assert "op-1" in op_ids

    def test_two_op_plan_intents(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        intents = {op.operation_id: op.intent for op in result.plan.operations}
        assert intents["op-0"] == "create"
        assert intents["op-1"] == "modify"

    def test_two_op_plan_depends_on(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        op1 = next(op for op in result.plan.operations if op.operation_id == "op-1")
        assert op1.depends_on == ("op-0",)

    def test_two_op_plan_list_order_preserved(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.plan.operations[0].operation_id == "op-0"
        assert result.plan.operations[1].operation_id == "op-1"

    def test_two_op_plan_confidence(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.plan.confidence == "high"

    def test_two_op_plan_goal_id(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _TWO_OP_PLAN
        )
        goal = _make_goal("my-goal-id")
        result = planner.plan(goal, _make_snapshot())
        assert result.plan.goal_id == "my-goal-id"
        assert result.plan.plan_id == "plan-my-goal-id"


class TestPlannerSingleOperation:
    def test_single_op_plan_success(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _ONE_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is True
        assert len(result.plan.operations) == 1

    def test_single_op_plan_no_dependencies(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _ONE_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        op = result.plan.operations[0]
        assert op.depends_on == ()


class TestPlannerAmbiguousConfidence:
    def test_ambiguous_plan_success_true(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _AMBIGUOUS_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is True
        assert result.plan is not None

    def test_ambiguous_plan_confidence(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _AMBIGUOUS_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.plan.confidence == "ambiguous"

    def test_ambiguous_plan_candidates_populated(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _AMBIGUOUS_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert "src/api.py" in result.plan.candidates
        assert "src/handlers.py" in result.plan.candidates

    def test_ambiguous_plan_operations_empty(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _AMBIGUOUS_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.plan.operations == ()


class TestPlannerGatewayFailure:
    def test_unregistered_llm_gateway_failure(self):
        gw = ExecutionGateway()  # no LLM registered
        mock_llm = MagicMock(spec=LlmAdapter)
        planner = EngineeringPlanner(
            gateway=gw,
            llm_adapter=mock_llm,
            llm_config=_make_llm_config(),
            operation_engine=OperationEngine(),
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False
        assert result.error is not None
        assert "gateway_dispatch_failed" in result.error

    def test_unavailable_adapter_gateway_failure(self):
        gw = ExecutionGateway()
        gw.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-unavailable",
            available=False,
            description="unavailable",
        ))
        mock_llm = MagicMock(spec=LlmAdapter)
        planner = EngineeringPlanner(
            gateway=gw,
            llm_adapter=mock_llm,
            llm_config=_make_llm_config(),
            operation_engine=OperationEngine(),
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False
        assert "gateway_dispatch_failed" in (result.error or "")

    def test_gateway_failure_does_not_call_llm(self):
        gw = ExecutionGateway()  # no adapters
        mock_llm = MagicMock(spec=LlmAdapter)
        planner = EngineeringPlanner(
            gateway=gw,
            llm_adapter=mock_llm,
            llm_config=_make_llm_config(),
            operation_engine=OperationEngine(),
        )
        planner.plan(_make_goal(), _make_snapshot())
        mock_llm.execute.assert_not_called()


class TestPlannerLLMAdapterFailure:
    def test_llm_failure_returns_false(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id, "connection_refused"
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False
        assert result.plan is None

    def test_llm_failure_propagates_error(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id, "my_specific_error"
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert "my_specific_error" in (result.error or "")


class TestPlannerMalformedJSON:
    def test_malformed_json_failure(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, "not valid json {"
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False

    def test_malformed_json_error_prefix(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, "not valid json {"
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.error is not None
        assert result.error.startswith("json_parse_error")

    def test_empty_response_failure(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, ""
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False


class TestPlannerMissingFields:
    def test_missing_confidence_failure(self):
        data = json.dumps({"operations": [], "candidates": []})
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False

    def test_missing_operations_failure(self):
        data = json.dumps({"confidence": "high", "basis": "x", "candidates": []})
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False

    def test_missing_operation_id_failure(self):
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [{"target": "f.py", "intent": "create", "goal": "g"}],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False

    def test_missing_target_failure(self):
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [{"operation_id": "op-0", "intent": "create", "goal": "g"}],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False

    def test_missing_goal_field_failure(self):
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [{"operation_id": "op-0", "target": "f.py", "intent": "create"}],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False


class TestPlannerInvalidIntent:
    def test_invalid_intent_failure(self):
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [{
                "operation_id": "op-0",
                "target": "f.py",
                "intent": "delete",  # invalid
                "goal": "do something",
                "depends_on": [],
            }],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False
        assert result.error is not None
        assert "intent" in result.error.lower()

    def test_invalid_confidence_failure(self):
        data = json.dumps({
            "confidence": "maybe",  # invalid
            "basis": "x",
            "operations": [],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False


class TestPlannerDependsOnDefaults:
    def test_depends_on_absent_parsed_as_empty(self):
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [{
                "operation_id": "op-0",
                "target": "f.py",
                "intent": "create",
                "goal": "Create file",
                # depends_on absent — defensive parse should default to []
            }],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is True
        assert result.plan.operations[0].depends_on == ()


class TestPlannerCycleDetection:
    def test_cycle_returns_failure(self):
        # op-0 depends on op-1, op-1 depends on op-0 → cycle
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [
                {
                    "operation_id": "op-0",
                    "target": "a.py",
                    "intent": "create",
                    "goal": "Create A",
                    "depends_on": ["op-1"],
                },
                {
                    "operation_id": "op-1",
                    "target": "b.py",
                    "intent": "create",
                    "goal": "Create B",
                    "depends_on": ["op-0"],
                },
            ],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False

    def test_cycle_error_prefix(self):
        data = json.dumps({
            "confidence": "high",
            "basis": "x",
            "operations": [
                {
                    "operation_id": "op-0",
                    "target": "a.py",
                    "intent": "create",
                    "goal": "Create A",
                    "depends_on": ["op-1"],
                },
                {
                    "operation_id": "op-1",
                    "target": "b.py",
                    "intent": "create",
                    "goal": "Create B",
                    "depends_on": ["op-0"],
                },
            ],
            "candidates": [],
        })
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, data
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.error is not None
        assert result.error.startswith("plan_cycle_detected")


class TestPlannerNeverRaises:
    def test_llm_exception_captured(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = RuntimeError("unexpected crash")
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.success is False
        assert result.error is not None


class TestPlannerSnapshotMetadata:
    def test_success_sets_planning_context(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _ONE_OP_PLAN
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.planning_metadata.planning_context == "repository_snapshot_v1"

    def test_success_sets_snapshot_entries(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _ONE_OP_PLAN
        )
        snap = _make_snapshot(file_count=7)
        result = planner.plan(_make_goal(), snap)
        assert result.planning_metadata.planning_snapshot_entries == "7"

    def test_success_sets_snapshot_id(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_success(
            req.request_id, req.operation_id, _ONE_OP_PLAN
        )
        snap = _make_snapshot(snapshot_id="snap-xyz999")
        result = planner.plan(_make_goal(), snap)
        assert result.planning_metadata.planning_snapshot_id == "snap-xyz999"

    def test_failure_leaves_planning_metadata_empty(self):
        planner, mock_llm = _make_planner()
        mock_llm.execute.side_effect = lambda req, cfg: _make_llm_failure(
            req.request_id, req.operation_id, "connection_refused"
        )
        result = planner.plan(_make_goal(), _make_snapshot())
        assert result.planning_metadata.planning_context == ""
        assert result.planning_metadata.planning_snapshot_entries == ""
        assert result.planning_metadata.planning_snapshot_id == ""

    def test_planning_prompt_contains_file_listing(self):
        """The LLM receives a system prompt that includes the repository file listing."""
        planner, mock_llm = _make_planner()
        captured_requests = []
        def capture(req, cfg):
            captured_requests.append(req)
            return _make_llm_success(req.request_id, req.operation_id, _ONE_OP_PLAN)
        mock_llm.execute.side_effect = capture
        snap = _make_snapshot(files=(
            RepositoryFile("src/routing/dispatcher.py", ".py", 100, False),
            RepositoryFile("src/utils/helpers.py", ".py", 200, False),
        ), file_count=2)
        planner.plan(_make_goal(), snap)
        assert len(captured_requests) == 1
        system_prompt = dict(captured_requests[0].payload)["system_prompt"]
        assert "src/routing/dispatcher.py" in system_prompt
        assert "src/utils/helpers.py" in system_prompt
