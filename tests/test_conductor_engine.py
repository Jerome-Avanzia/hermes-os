"""Tests for the Conductor Engine — Sprint 58.

The Conductor Engine (hermes.kernel.conductor.Conductor) is distinct from the
legacy hermes.conductor.Conductor (profile-based chat wrapper). This file tests
the Sprint 58 deterministic orchestration layer only.

Covers:
  - All 6 typed contracts (construction, frozen, equality)
  - ConductorRequest field defaults and required fields
  - Conductor.validate_request() — all error paths
  - Conductor.orchestrate() — full pipeline
  - Engine delegation sequence (each engine called in order)
  - Conditional stages (job/operation/swarm gates)
  - Swarm formation rule (_swarm_required)
  - Auto-swarm construction (_build_auto_swarm)
  - Immutability of all contracts
  - Determinism (same request → same result)
  - Edge cases (no operations, no job, minimal request)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes.kernel.conductor import Conductor
from hermes.models.conductor import (
    ConductorAudit,
    ConductorDecision,
    ConductorRequest,
    ConductorResult,
    ConductorStage,
    ConductorValidationResult,
)
from hermes.models.founder_workflow import ApprovalStatus
from hermes.models.job import JobDefinition, JobPriority, JobStatus
from hermes.models.operation import (
    OperationDefinition,
    OperationDependency,
    OperationPriority,
    OperationStatus,
    OperationType,
)
from hermes.models.routing_decision import RoutingPolicy
from hermes.models.swarm import (
    SwarmDefinition,
    SwarmMember,
    SwarmStrategy,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_context_manager() -> MagicMock:
    cm = MagicMock()
    pkg = MagicMock()
    pkg.knowledge = []
    pkg.capabilities = []
    cm.assemble.return_value = pkg
    return cm


def _mock_prompt_compression() -> MagicMock:
    pc = MagicMock()
    pkg = MagicMock()
    pkg.sections = []
    pkg.estimated_chars = 100
    pc.compress.return_value = pkg
    return pc


def _mock_model_router() -> MagicMock:
    mr = MagicMock()
    decision = MagicMock()
    decision.routed = True
    decision.selected_model_id = "test-model"
    mr.route.return_value = decision
    return mr


def _mock_workflow_engine() -> MagicMock:
    we = MagicMock()
    wf = MagicMock()
    wf.current_stage.value = "ready_for_runtime"
    wf.next_action.value = "route_to_runtime"
    we.build.return_value = wf
    return we


def _mock_job_engine() -> MagicMock:
    je = MagicMock()
    jr = MagicMock()
    jr.status.value = "ready"
    jr.resolved_capabilities = ("cap-a",)
    jr.unresolved_capabilities = ()
    je.plan.return_value = jr
    return je


def _mock_operation_engine() -> MagicMock:
    oe = MagicMock()
    oe.plan_all.return_value = ()
    return oe


def _mock_swarm_engine() -> MagicMock:
    se = MagicMock()
    sr = MagicMock()
    sr.conflict_detected = False
    se.plan.return_value = sr
    swarm_def = MagicMock()
    swarm_def.members = []
    se.build_swarm.return_value = swarm_def
    return se


def _make_conductor(
    context_manager=None,
    prompt_compression=None,
    model_router=None,
    workflow_engine=None,
    job_engine=None,
    operation_engine=None,
    swarm_engine=None,
) -> Conductor:
    return Conductor(
        context_manager=context_manager or _mock_context_manager(),
        prompt_compression=prompt_compression or _mock_prompt_compression(),
        model_router=model_router or _mock_model_router(),
        workflow_engine=workflow_engine or _mock_workflow_engine(),
        job_engine=job_engine or _mock_job_engine(),
        operation_engine=operation_engine or _mock_operation_engine(),
        swarm_engine=swarm_engine or _mock_swarm_engine(),
    )


def _minimal_request(**kwargs) -> ConductorRequest:
    defaults = dict(request_id="req-001", goal="Test goal", workspace_id="ws-001")
    defaults.update(kwargs)
    return ConductorRequest(**defaults)


def _make_job_definition() -> JobDefinition:
    return JobDefinition(
        id="job-001",
        mission_id="mission-001",
        goal="Test job",
        priority=JobPriority.NORMAL,
        status=JobStatus.DEFINED,
        capability_requirements=(),
        operation_refs=(),
        depends_on=(),
    )


def _make_operation(
    op_id: str,
    capability_id: str = "cap-a",
    sequence_index: int = 0,
    depends_on: list[OperationDependency] | None = None,
) -> OperationDefinition:
    return OperationDefinition(
        id=op_id,
        job_id="job-001",
        goal=f"Goal for {op_id}",
        operation_type=OperationType.LLM,
        priority=OperationPriority.NORMAL,
        status=OperationStatus.DEFINED,
        sequence_index=sequence_index,
        capability_id=capability_id,
        depends_on=tuple(sorted(depends_on or [], key=lambda d: d.operation_id)),
        execution_ref=None,
    )


def _make_swarm_definition() -> SwarmDefinition:
    return SwarmDefinition(
        id="swarm-001",
        job_id="job-001",
        strategy=SwarmStrategy.ACCUMULATE,
        members=(SwarmMember(operation_id="op-a", role="producer", sequence_index=0),),
        dependencies=(),
        context_keys=(),
    )


# ── TestConductorStage ────────────────────────────────────────────────────────


class TestConductorStage:
    def test_all_values(self) -> None:
        values = {s.value for s in ConductorStage}
        assert values == {
            "context", "prompt", "routing", "workflow",
            "job_planning", "operation_planning", "swarm_planning",
            "complete", "failed",
        }

    def test_exactly_nine_stages(self) -> None:
        assert len(ConductorStage) == 9

    def test_terminal_complete(self) -> None:
        assert ConductorStage.COMPLETE.value == "complete"

    def test_terminal_failed(self) -> None:
        assert ConductorStage.FAILED.value == "failed"

    def test_from_value(self) -> None:
        assert ConductorStage("context") is ConductorStage.CONTEXT
        assert ConductorStage("swarm_planning") is ConductorStage.SWARM_PLANNING

    def test_all_stage_values_unique(self) -> None:
        values = [s.value for s in ConductorStage]
        assert len(values) == len(set(values))


# ── TestConductorDecision ─────────────────────────────────────────────────────


class TestConductorDecision:
    def test_construction(self) -> None:
        d = ConductorDecision(
            stage=ConductorStage.CONTEXT,
            outcome="context_assembled knowledge_count=3 capability_count=2",
        )
        assert d.stage is ConductorStage.CONTEXT
        assert "context_assembled" in d.outcome

    def test_frozen(self) -> None:
        d = ConductorDecision(stage=ConductorStage.PROMPT, outcome="ok")
        with pytest.raises(AttributeError):
            d.outcome = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ConductorDecision(stage=ConductorStage.ROUTING, outcome="routing_succeeded")
        b = ConductorDecision(stage=ConductorStage.ROUTING, outcome="routing_succeeded")
        assert a == b

    def test_inequality_stage(self) -> None:
        a = ConductorDecision(stage=ConductorStage.CONTEXT, outcome="ok")
        b = ConductorDecision(stage=ConductorStage.PROMPT, outcome="ok")
        assert a != b

    def test_inequality_outcome(self) -> None:
        a = ConductorDecision(stage=ConductorStage.CONTEXT, outcome="ok")
        b = ConductorDecision(stage=ConductorStage.CONTEXT, outcome="different")
        assert a != b


# ── TestConductorAudit ────────────────────────────────────────────────────────


class TestConductorAudit:
    def test_construction(self) -> None:
        d = ConductorDecision(stage=ConductorStage.CONTEXT, outcome="ok")
        audit = ConductorAudit(
            stages_completed=(ConductorStage.CONTEXT,),
            decisions=(d,),
            swarm_required=False,
            job_count=1,
            operation_count=3,
        )
        assert audit.stages_completed == (ConductorStage.CONTEXT,)
        assert len(audit.decisions) == 1
        assert audit.swarm_required is False
        assert audit.job_count == 1
        assert audit.operation_count == 3

    def test_frozen(self) -> None:
        audit = ConductorAudit(
            stages_completed=(), decisions=(), swarm_required=False,
            job_count=0, operation_count=0,
        )
        with pytest.raises(AttributeError):
            audit.job_count = 999  # type: ignore[misc]

    def test_empty_audit(self) -> None:
        audit = ConductorAudit(
            stages_completed=(), decisions=(), swarm_required=False,
            job_count=0, operation_count=0,
        )
        assert audit.stages_completed == ()
        assert audit.decisions == ()

    def test_equality(self) -> None:
        a = ConductorAudit(
            stages_completed=(), decisions=(), swarm_required=False,
            job_count=0, operation_count=0,
        )
        b = ConductorAudit(
            stages_completed=(), decisions=(), swarm_required=False,
            job_count=0, operation_count=0,
        )
        assert a == b


# ── TestConductorValidationResult ─────────────────────────────────────────────


class TestConductorValidationResult:
    def test_valid(self) -> None:
        r = ConductorValidationResult(valid=True, errors=())
        assert r.valid is True
        assert r.errors == ()

    def test_invalid(self) -> None:
        r = ConductorValidationResult(
            valid=False,
            errors=("goal must not be empty", "workspace_id must not be empty"),
        )
        assert r.valid is False
        assert len(r.errors) == 2

    def test_frozen(self) -> None:
        r = ConductorValidationResult(valid=True, errors=())
        with pytest.raises(AttributeError):
            r.valid = False  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ConductorValidationResult(valid=True, errors=())
        b = ConductorValidationResult(valid=True, errors=())
        assert a == b


# ── TestConductorRequest ──────────────────────────────────────────────────────


class TestConductorRequest:
    def test_minimal_construction(self) -> None:
        r = ConductorRequest(
            request_id="req-001",
            goal="Draft announcement",
            workspace_id="ws-001",
        )
        assert r.request_id == "req-001"
        assert r.goal == "Draft announcement"
        assert r.workspace_id == "ws-001"

    def test_defaults(self) -> None:
        r = _minimal_request()
        assert r.routing_policy is RoutingPolicy.BALANCED
        assert r.max_fallbacks == 3
        assert r.budget == "8k"
        assert r.workflow_id == ""
        assert r.approval_status is ApprovalStatus.PENDING
        assert r.job_definition is None
        assert r.completed_job_ids == frozenset()
        assert r.operations == ()
        assert r.completed_op_ids == frozenset()
        assert r.swarm_definition is None

    def test_frozen(self) -> None:
        r = _minimal_request()
        with pytest.raises(AttributeError):
            r.goal = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = _minimal_request()
        b = _minimal_request()
        assert a == b

    def test_with_job_definition(self) -> None:
        job = _make_job_definition()
        r = _minimal_request(job_definition=job)
        assert r.job_definition is job

    def test_with_operations(self) -> None:
        op = _make_operation("op-001")
        r = _minimal_request(operations=(op,))
        assert len(r.operations) == 1

    def test_with_swarm_definition(self) -> None:
        swarm = _make_swarm_definition()
        r = _minimal_request(swarm_definition=swarm)
        assert r.swarm_definition is swarm

    def test_custom_routing_policy(self) -> None:
        r = _minimal_request(routing_policy=RoutingPolicy.CHEAPEST)
        assert r.routing_policy is RoutingPolicy.CHEAPEST

    def test_custom_budget(self) -> None:
        r = _minimal_request(budget="32k")
        assert r.budget == "32k"

    def test_completed_job_ids(self) -> None:
        r = _minimal_request(completed_job_ids=frozenset({"job-001", "job-002"}))
        assert "job-001" in r.completed_job_ids
        assert "job-002" in r.completed_job_ids

    def test_completed_op_ids(self) -> None:
        r = _minimal_request(completed_op_ids=frozenset({"op-done"}))
        assert "op-done" in r.completed_op_ids


# ── TestConductorResult ───────────────────────────────────────────────────────


class TestConductorResult:
    def _make_result(self, **kwargs) -> ConductorResult:
        audit = ConductorAudit(
            stages_completed=(), decisions=(), swarm_required=False,
            job_count=0, operation_count=0,
        )
        validation = ConductorValidationResult(valid=True, errors=())
        defaults = dict(
            request_id="req-001",
            success=True,
            final_stage=ConductorStage.COMPLETE,
            context_package=None,
            prompt_package=None,
            routing_decision=None,
            workflow=None,
            job_result=None,
            operation_results=(),
            swarm_result=None,
            audit=audit,
            validation_result=validation,
        )
        defaults.update(kwargs)
        return ConductorResult(**defaults)

    def test_construction_success(self) -> None:
        r = self._make_result()
        assert r.request_id == "req-001"
        assert r.success is True
        assert r.final_stage is ConductorStage.COMPLETE

    def test_construction_failure(self) -> None:
        r = self._make_result(success=False, final_stage=ConductorStage.FAILED)
        assert r.success is False
        assert r.final_stage is ConductorStage.FAILED

    def test_frozen(self) -> None:
        r = self._make_result()
        with pytest.raises(AttributeError):
            r.success = False  # type: ignore[misc]

    def test_operation_results_default_empty_tuple(self) -> None:
        r = self._make_result()
        assert r.operation_results == ()

    def test_equality(self) -> None:
        a = self._make_result()
        b = self._make_result()
        assert a == b

    def test_none_intermediates_on_failure(self) -> None:
        r = self._make_result(success=False, final_stage=ConductorStage.FAILED)
        assert r.context_package is None
        assert r.prompt_package is None
        assert r.routing_decision is None
        assert r.workflow is None
        assert r.job_result is None
        assert r.swarm_result is None


# ── TestConductorValidateRequest ──────────────────────────────────────────────


class TestConductorValidateRequest:
    def setup_method(self) -> None:
        self.conductor = _make_conductor()

    def test_valid_minimal(self) -> None:
        r = _minimal_request()
        result = self.conductor.validate_request(r)
        assert result.valid is True
        assert result.errors == ()

    def test_empty_request_id(self) -> None:
        r = _minimal_request(request_id="")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("request_id" in e for e in result.errors)

    def test_whitespace_request_id(self) -> None:
        r = _minimal_request(request_id="   ")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("request_id" in e for e in result.errors)

    def test_empty_goal(self) -> None:
        r = _minimal_request(goal="")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("goal" in e for e in result.errors)

    def test_whitespace_goal(self) -> None:
        r = _minimal_request(goal="   ")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("goal" in e for e in result.errors)

    def test_empty_workspace_id(self) -> None:
        r = _minimal_request(workspace_id="")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("workspace_id" in e for e in result.errors)

    def test_negative_max_fallbacks(self) -> None:
        r = _minimal_request(max_fallbacks=-1)
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("max_fallbacks" in e for e in result.errors)

    def test_zero_max_fallbacks_valid(self) -> None:
        r = _minimal_request(max_fallbacks=0)
        result = self.conductor.validate_request(r)
        assert result.valid is True

    def test_empty_budget(self) -> None:
        r = _minimal_request(budget="")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert any("budget" in e for e in result.errors)

    def test_multiple_errors_all_reported(self) -> None:
        r = _minimal_request(request_id="", goal="", workspace_id="")
        result = self.conductor.validate_request(r)
        assert result.valid is False
        assert len(result.errors) >= 3

    def test_validation_result_is_frozen(self) -> None:
        r = _minimal_request()
        result = self.conductor.validate_request(r)
        with pytest.raises(AttributeError):
            result.valid = False  # type: ignore[misc]

    def test_valid_custom_budget(self) -> None:
        r = _minimal_request(budget="32k")
        result = self.conductor.validate_request(r)
        assert result.valid is True


# ── TestConductorOrchestrate ──────────────────────────────────────────────────


class TestConductorOrchestrate:
    def setup_method(self) -> None:
        self.cm = _mock_context_manager()
        self.pc = _mock_prompt_compression()
        self.mr = _mock_model_router()
        self.we = _mock_workflow_engine()
        self.je = _mock_job_engine()
        self.oe = _mock_operation_engine()
        self.se = _mock_swarm_engine()
        self.conductor = _make_conductor(
            context_manager=self.cm,
            prompt_compression=self.pc,
            model_router=self.mr,
            workflow_engine=self.we,
            job_engine=self.je,
            operation_engine=self.oe,
            swarm_engine=self.se,
        )

    def test_minimal_request_succeeds(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert result.success is True
        assert result.final_stage is ConductorStage.COMPLETE

    def test_result_is_frozen(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]

    def test_request_id_preserved(self) -> None:
        result = self.conductor.orchestrate(_minimal_request(request_id="req-xyz"))
        assert result.request_id == "req-xyz"

    def test_context_stage_always_executed(self) -> None:
        self.conductor.orchestrate(_minimal_request())
        self.cm.assemble.assert_called_once()

    def test_context_called_with_goal_and_workspace(self) -> None:
        self.conductor.orchestrate(_minimal_request(
            goal="My goal", workspace_id="ws-test",
        ))
        self.cm.assemble.assert_called_once_with(
            query="My goal",
            workspace_id="ws-test",
        )

    def test_prompt_stage_always_executed(self) -> None:
        self.conductor.orchestrate(_minimal_request())
        self.pc.compress.assert_called_once()

    def test_prompt_called_with_context_output(self) -> None:
        context_pkg = self.cm.assemble.return_value
        self.conductor.orchestrate(_minimal_request(budget="16k"))
        call_args = self.pc.compress.call_args
        assert call_args.kwargs["package"] is context_pkg
        assert call_args.kwargs["budget"] == "16k"

    def test_routing_stage_always_executed(self) -> None:
        self.conductor.orchestrate(_minimal_request())
        self.mr.route.assert_called_once()

    def test_routing_called_with_prompt_output_and_policy(self) -> None:
        prompt_pkg = self.pc.compress.return_value
        self.conductor.orchestrate(_minimal_request(routing_policy=RoutingPolicy.CHEAPEST))
        call_args = self.mr.route.call_args
        assert call_args.kwargs["package"] is prompt_pkg
        assert call_args.kwargs["policy"] is RoutingPolicy.CHEAPEST

    def test_workflow_stage_always_executed(self) -> None:
        self.conductor.orchestrate(_minimal_request())
        self.we.build.assert_called_once()

    def test_workflow_called_with_prompt_and_routing_outputs(self) -> None:
        prompt_pkg = self.pc.compress.return_value
        routing = self.mr.route.return_value
        self.conductor.orchestrate(_minimal_request())
        call_args = self.we.build.call_args
        assert call_args.kwargs["package"] is prompt_pkg
        assert call_args.kwargs["routing"] is routing

    def test_workflow_called_with_workflow_id_and_approval(self) -> None:
        self.conductor.orchestrate(_minimal_request(
            workflow_id="wf-42",
            approval_status=ApprovalStatus.APPROVED,
        ))
        call_args = self.we.build.call_args
        assert call_args.kwargs["workflow_id"] == "wf-42"
        assert call_args.kwargs["approval_status"] is ApprovalStatus.APPROVED

    def test_result_contains_all_stage_outputs(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert result.context_package is self.cm.assemble.return_value
        assert result.prompt_package is self.pc.compress.return_value
        assert result.routing_decision is self.mr.route.return_value
        assert result.workflow is self.we.build.return_value

    def test_job_stage_skipped_when_no_job_definition(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        self.je.plan.assert_not_called()
        assert result.job_result is None

    def test_job_stage_executed_when_job_definition_provided(self) -> None:
        job = _make_job_definition()
        result = self.conductor.orchestrate(_minimal_request(job_definition=job))
        self.je.plan.assert_called_once()
        assert result.job_result is self.je.plan.return_value

    def test_job_called_with_completed_job_ids(self) -> None:
        job = _make_job_definition()
        completed = frozenset({"job-done"})
        self.conductor.orchestrate(_minimal_request(
            job_definition=job, completed_job_ids=completed,
        ))
        call_args = self.je.plan.call_args
        assert call_args.args[0] is job
        assert call_args.kwargs["completed_job_ids"] == completed

    def test_operation_stage_skipped_when_no_operations(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        self.oe.plan_all.assert_not_called()
        assert result.operation_results == ()

    def test_operation_stage_executed_when_operations_provided(self) -> None:
        op = _make_operation("op-001")
        self.oe.plan_all.return_value = (MagicMock(),)
        result = self.conductor.orchestrate(_minimal_request(operations=(op,)))
        self.oe.plan_all.assert_called_once()
        assert len(result.operation_results) == 1

    def test_operation_called_with_completed_op_ids(self) -> None:
        op = _make_operation("op-001")
        completed = frozenset({"op-done"})
        self.conductor.orchestrate(_minimal_request(
            operations=(op,), completed_op_ids=completed,
        ))
        call_args = self.oe.plan_all.call_args
        assert call_args.kwargs["completed_op_ids"] == completed

    def test_operation_tuple_converted_to_list_for_engine(self) -> None:
        op = _make_operation("op-001")
        self.conductor.orchestrate(_minimal_request(operations=(op,)))
        call_args = self.oe.plan_all.call_args
        assert isinstance(call_args.args[0], list)

    def test_swarm_skipped_when_single_skill(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation("op-b", capability_id="cap-x", sequence_index=1)
        result = self.conductor.orchestrate(_minimal_request(operations=(op_a, op_b)))
        self.se.plan.assert_not_called()
        assert result.swarm_result is None

    def test_swarm_skipped_when_no_cross_skill_deps(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x")
        op_b = _make_operation("op-b", capability_id="cap-y")
        result = self.conductor.orchestrate(_minimal_request(operations=(op_a, op_b)))
        self.se.plan.assert_not_called()
        assert result.swarm_result is None

    def test_swarm_activated_when_cross_skill_deps_detected(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        result = self.conductor.orchestrate(_minimal_request(operations=(op_a, op_b)))
        self.se.plan.assert_called_once()
        assert result.swarm_result is self.se.plan.return_value

    def test_swarm_activated_when_explicit_swarm_definition_provided(self) -> None:
        swarm = _make_swarm_definition()
        result = self.conductor.orchestrate(_minimal_request(swarm_definition=swarm))
        self.se.plan.assert_called_once_with(swarm)
        assert result.swarm_result is self.se.plan.return_value

    def test_swarm_auto_built_when_required_but_no_explicit_definition(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        self.conductor.orchestrate(_minimal_request(operations=(op_a, op_b)))
        self.se.build_swarm.assert_called_once()
        self.se.plan.assert_called_once()

    def test_audit_stages_completed_minimal(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert ConductorStage.CONTEXT in result.audit.stages_completed
        assert ConductorStage.PROMPT in result.audit.stages_completed
        assert ConductorStage.ROUTING in result.audit.stages_completed
        assert ConductorStage.WORKFLOW in result.audit.stages_completed
        assert ConductorStage.JOB_PLANNING not in result.audit.stages_completed
        assert ConductorStage.OPERATION_PLANNING not in result.audit.stages_completed
        assert ConductorStage.SWARM_PLANNING not in result.audit.stages_completed

    def test_audit_includes_job_planning_stage(self) -> None:
        job = _make_job_definition()
        result = self.conductor.orchestrate(_minimal_request(job_definition=job))
        assert ConductorStage.JOB_PLANNING in result.audit.stages_completed

    def test_audit_includes_operation_planning_stage(self) -> None:
        op = _make_operation("op-001")
        result = self.conductor.orchestrate(_minimal_request(operations=(op,)))
        assert ConductorStage.OPERATION_PLANNING in result.audit.stages_completed

    def test_audit_includes_swarm_planning_stage(self) -> None:
        swarm = _make_swarm_definition()
        result = self.conductor.orchestrate(_minimal_request(swarm_definition=swarm))
        assert ConductorStage.SWARM_PLANNING in result.audit.stages_completed

    def test_audit_swarm_required_false_by_default(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert result.audit.swarm_required is False

    def test_audit_swarm_required_true_when_activated(self) -> None:
        swarm = _make_swarm_definition()
        result = self.conductor.orchestrate(_minimal_request(swarm_definition=swarm))
        assert result.audit.swarm_required is True

    def test_audit_job_count_zero_without_job(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert result.audit.job_count == 0

    def test_audit_job_count_one_with_job(self) -> None:
        job = _make_job_definition()
        result = self.conductor.orchestrate(_minimal_request(job_definition=job))
        assert result.audit.job_count == 1

    def test_audit_operation_count(self) -> None:
        op_a = _make_operation("op-a")
        op_b = _make_operation("op-b")
        result = self.conductor.orchestrate(_minimal_request(operations=(op_a, op_b)))
        assert result.audit.operation_count == 2

    def test_audit_decisions_one_per_stage(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert len(result.audit.decisions) == len(result.audit.stages_completed)

    def test_audit_decisions_match_stage_order(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        for i, decision in enumerate(result.audit.decisions):
            assert decision.stage is result.audit.stages_completed[i]

    def test_documents_passed_to_prompt_compression(self) -> None:
        docs = {"doc-001": MagicMock()}
        self.conductor.orchestrate(_minimal_request(), documents=docs)
        call_args = self.pc.compress.call_args
        assert call_args.kwargs["documents"] is docs

    def test_empty_documents_by_default(self) -> None:
        self.conductor.orchestrate(_minimal_request())
        call_args = self.pc.compress.call_args
        assert call_args.kwargs["documents"] == {}

    def test_validation_failure_returns_failed_result(self) -> None:
        result = self.conductor.orchestrate(_minimal_request(request_id=""))
        assert result.success is False
        assert result.final_stage is ConductorStage.FAILED

    def test_validation_failure_no_engines_called(self) -> None:
        self.conductor.orchestrate(_minimal_request(goal=""))
        self.cm.assemble.assert_not_called()
        self.pc.compress.assert_not_called()
        self.mr.route.assert_not_called()
        self.we.build.assert_not_called()
        self.je.plan.assert_not_called()
        self.oe.plan_all.assert_not_called()
        self.se.plan.assert_not_called()

    def test_validation_failure_result_contains_errors(self) -> None:
        result = self.conductor.orchestrate(_minimal_request(goal=""))
        assert not result.validation_result.valid
        assert len(result.validation_result.errors) > 0

    def test_all_outputs_none_on_validation_failure(self) -> None:
        result = self.conductor.orchestrate(_minimal_request(request_id=""))
        assert result.context_package is None
        assert result.prompt_package is None
        assert result.routing_decision is None
        assert result.workflow is None
        assert result.job_result is None
        assert result.operation_results == ()
        assert result.swarm_result is None

    def test_pipeline_order_context_before_prompt(self) -> None:
        call_order: list[str] = []
        orig_assemble = self.cm.assemble.return_value

        def cm_side_effect(**kw):
            call_order.append("context")
            return orig_assemble
        self.cm.assemble.side_effect = cm_side_effect

        orig_compress = self.pc.compress.return_value

        def pc_side_effect(**kw):
            call_order.append("prompt")
            return orig_compress
        self.pc.compress.side_effect = pc_side_effect

        self.conductor.orchestrate(_minimal_request())
        assert call_order.index("context") < call_order.index("prompt")

    def test_pipeline_order_prompt_before_routing(self) -> None:
        call_order: list[str] = []
        orig_compress = self.pc.compress.return_value
        orig_route = self.mr.route.return_value

        def pc_side(**kw):
            call_order.append("prompt")
            return orig_compress
        self.pc.compress.side_effect = pc_side

        def mr_side(**kw):
            call_order.append("routing")
            return orig_route
        self.mr.route.side_effect = mr_side

        self.conductor.orchestrate(_minimal_request())
        assert call_order.index("prompt") < call_order.index("routing")

    def test_pipeline_order_routing_before_workflow(self) -> None:
        call_order: list[str] = []
        orig_route = self.mr.route.return_value
        orig_workflow = self.we.build.return_value

        def mr_side(**kw):
            call_order.append("routing")
            return orig_route
        self.mr.route.side_effect = mr_side

        def we_side(**kw):
            call_order.append("workflow")
            return orig_workflow
        self.we.build.side_effect = we_side

        self.conductor.orchestrate(_minimal_request())
        assert call_order.index("routing") < call_order.index("workflow")

    def test_max_fallbacks_passed_to_router(self) -> None:
        self.conductor.orchestrate(_minimal_request(max_fallbacks=7))
        call_args = self.mr.route.call_args
        assert call_args.kwargs["max_fallbacks"] == 7

    def test_approval_status_approved_passed_to_workflow(self) -> None:
        self.conductor.orchestrate(_minimal_request(
            approval_status=ApprovalStatus.APPROVED,
        ))
        call_args = self.we.build.call_args
        assert call_args.kwargs["approval_status"] is ApprovalStatus.APPROVED


# ── TestConductorSwarmDetection ───────────────────────────────────────────────


class TestConductorSwarmDetection:
    def setup_method(self) -> None:
        self.conductor = _make_conductor()

    def _swarm_required(self, ops, swarm_def=None):
        return self.conductor._swarm_required(ops, swarm_def)

    def test_no_operations_no_swarm(self) -> None:
        assert self._swarm_required(()) is False

    def test_single_op_no_swarm(self) -> None:
        op = _make_operation("op-a", capability_id="cap-x")
        assert self._swarm_required((op,)) is False

    def test_single_skill_no_swarm(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x")
        op_b = _make_operation("op-b", capability_id="cap-x")
        assert self._swarm_required((op_a, op_b)) is False

    def test_multi_skill_no_deps_no_swarm(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x")
        op_b = _make_operation("op-b", capability_id="cap-y")
        assert self._swarm_required((op_a, op_b)) is False

    def test_multi_skill_cross_dep_requires_swarm(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        assert self._swarm_required((op_a, op_b)) is True

    def test_explicit_swarm_definition_requires_swarm(self) -> None:
        swarm = _make_swarm_definition()
        assert self._swarm_required((), swarm) is True

    def test_explicit_swarm_definition_with_single_skill_requires_swarm(self) -> None:
        op = _make_operation("op-a", capability_id="cap-x")
        swarm = _make_swarm_definition()
        assert self._swarm_required((op,), swarm) is True

    def test_same_skill_dep_no_swarm(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-x", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        assert self._swarm_required((op_a, op_b)) is False

    def test_three_skills_cross_dep_requires_swarm(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        op_c = _make_operation(
            "op-c", capability_id="cap-z", sequence_index=2,
            depends_on=[OperationDependency(operation_id="op-b")],
        )
        assert self._swarm_required((op_a, op_b, op_c)) is True

    def test_ops_with_no_capability_id_not_cross_skill(self) -> None:
        op_a = _make_operation("op-a", capability_id="")
        op_b = _make_operation("op-b", capability_id="")
        assert self._swarm_required((op_a, op_b)) is False

    def test_dep_outside_op_set_not_cross_skill(self) -> None:
        # op-external is not in the set → its capability is unknown → no cross-skill
        op_b = _make_operation(
            "op-b", capability_id="cap-y",
            depends_on=[OperationDependency(operation_id="op-external")],
        )
        op_c = _make_operation("op-c", capability_id="cap-x")
        assert self._swarm_required((op_b, op_c)) is False

    def test_swarm_detection_deterministic(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        result_1 = self._swarm_required((op_a, op_b))
        result_2 = self._swarm_required((op_a, op_b))
        assert result_1 == result_2 == True


# ── TestAutoSwarmConstruction ─────────────────────────────────────────────────


class TestAutoSwarmConstruction:
    def setup_method(self) -> None:
        from hermes.kernel.swarm_engine import SwarmEngine
        self.real_swarm_engine = SwarmEngine()
        self.conductor = _make_conductor(swarm_engine=self.real_swarm_engine)

    def test_auto_swarm_uses_operation_ids_as_members(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        member_ids = {m.operation_id for m in swarm_def.members}
        assert "op-a" in member_ids
        assert "op-b" in member_ids

    def test_auto_swarm_preserves_dependencies(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        assert any(
            d.from_operation_id == "op-a" and d.to_operation_id == "op-b"
            for d in swarm_def.dependencies
        )

    def test_auto_swarm_context_keys_from_capability_ids(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        assert "cap-x" in swarm_def.context_keys
        assert "cap-y" in swarm_def.context_keys

    def test_auto_swarm_id_derived_from_request_id(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(request_id="req-xyz", operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        assert "req-xyz" in swarm_def.id

    def test_auto_swarm_job_id_from_job_definition(self) -> None:
        job = _make_job_definition()
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(job_definition=job, operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        assert swarm_def.job_id == "job-001"

    def test_auto_swarm_job_id_falls_back_to_request_id(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(request_id="req-fallback", operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        assert swarm_def.job_id == "req-fallback"

    def test_auto_swarm_member_roles_from_capability_ids(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        role_by_id = {m.operation_id: m.role for m in swarm_def.members}
        assert role_by_id["op-a"] == "cap-x"
        assert role_by_id["op-b"] == "cap-y"

    def test_auto_swarm_excludes_external_dependencies(self) -> None:
        # Dependency on op-external (not in op set) should not appear in swarm deps
        op_b = _make_operation(
            "op-b", capability_id="cap-y",
            depends_on=[OperationDependency(operation_id="op-external")],
        )
        request = _minimal_request(operations=(op_b,))
        swarm_def = self.conductor._build_auto_swarm(request)
        # op-external is not in op set → no dependency edge for it
        assert not any(d.from_operation_id == "op-external" for d in swarm_def.dependencies)

    def test_auto_swarm_context_keys_sorted(self) -> None:
        op_a = _make_operation("op-a", capability_id="cap-z", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-a", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        request = _minimal_request(operations=(op_a, op_b))
        swarm_def = self.conductor._build_auto_swarm(request)
        assert list(swarm_def.context_keys) == sorted(swarm_def.context_keys)


# ── TestDeterminism ───────────────────────────────────────────────────────────


class TestDeterminism:
    def test_validate_request_is_deterministic(self) -> None:
        conductor = _make_conductor()
        r = _minimal_request()
        result_a = conductor.validate_request(r)
        result_b = conductor.validate_request(r)
        assert result_a == result_b

    def test_validate_request_errors_deterministic(self) -> None:
        conductor = _make_conductor()
        r = _minimal_request(goal="", workspace_id="")
        result_a = conductor.validate_request(r)
        result_b = conductor.validate_request(r)
        assert result_a.errors == result_b.errors

    def test_swarm_required_is_deterministic(self) -> None:
        conductor = _make_conductor()
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        result_a = conductor._swarm_required((op_a, op_b), None)
        result_b = conductor._swarm_required((op_a, op_b), None)
        assert result_a == result_b

    def test_orchestrate_same_inputs_same_audit_stages(self) -> None:
        conductor_a = _make_conductor()
        conductor_b = _make_conductor()
        r = _minimal_request()
        result_a = conductor_a.orchestrate(r)
        result_b = conductor_b.orchestrate(r)
        assert result_a.audit.stages_completed == result_b.audit.stages_completed

    def test_orchestrate_with_job_deterministic_stage_list(self) -> None:
        conductor_a = _make_conductor()
        conductor_b = _make_conductor()
        job = _make_job_definition()
        r = _minimal_request(job_definition=job)
        result_a = conductor_a.orchestrate(r)
        result_b = conductor_b.orchestrate(r)
        assert result_a.audit.stages_completed == result_b.audit.stages_completed

    def test_orchestrate_swarm_required_deterministic(self) -> None:
        conductor_a = _make_conductor()
        conductor_b = _make_conductor()
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        r = _minimal_request(operations=(op_a, op_b))
        result_a = conductor_a.orchestrate(r)
        result_b = conductor_b.orchestrate(r)
        assert result_a.audit.swarm_required == result_b.audit.swarm_required


# ── TestEdgeCases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def setup_method(self) -> None:
        self.conductor = _make_conductor()

    def test_full_pipeline_all_stages(self) -> None:
        job = _make_job_definition()
        op_a = _make_operation("op-a", capability_id="cap-x", sequence_index=0)
        op_b = _make_operation(
            "op-b", capability_id="cap-y", sequence_index=1,
            depends_on=[OperationDependency(operation_id="op-a")],
        )
        swarm = _make_swarm_definition()
        result = self.conductor.orchestrate(_minimal_request(
            job_definition=job,
            operations=(op_a, op_b),
            swarm_definition=swarm,
        ))
        assert result.success is True
        completed = result.audit.stages_completed
        assert ConductorStage.CONTEXT in completed
        assert ConductorStage.PROMPT in completed
        assert ConductorStage.ROUTING in completed
        assert ConductorStage.WORKFLOW in completed
        assert ConductorStage.JOB_PLANNING in completed
        assert ConductorStage.OPERATION_PLANNING in completed
        assert ConductorStage.SWARM_PLANNING in completed

    def test_routing_failed_still_continues_to_workflow(self) -> None:
        mr = _mock_model_router()
        mr.route.return_value.routed = False
        conductor = _make_conductor(model_router=mr)
        result = conductor.orchestrate(_minimal_request())
        conductor._workflow_engine.build.assert_called_once()
        assert result.success is True

    def test_audit_is_frozen(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        with pytest.raises(AttributeError):
            result.audit.job_count = 999  # type: ignore[misc]

    def test_validation_result_in_successful_result(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        assert result.validation_result.valid is True
        assert result.validation_result.errors == ()

    def test_audit_decisions_are_frozen_dataclasses(self) -> None:
        result = self.conductor.orchestrate(_minimal_request())
        for decision in result.audit.decisions:
            with pytest.raises(AttributeError):
                decision.outcome = "changed"  # type: ignore[misc]

    def test_operation_results_preserved_from_engine(self) -> None:
        oe = _mock_operation_engine()
        mock_result = MagicMock()
        oe.plan_all.return_value = (mock_result,)
        conductor = _make_conductor(operation_engine=oe)
        op = _make_operation("op-001")
        result = conductor.orchestrate(_minimal_request(operations=(op,)))
        assert result.operation_results == (mock_result,)

    def test_whitespace_budget_fails_validation(self) -> None:
        result = self.conductor.orchestrate(_minimal_request(budget="   "))
        assert result.success is False
        assert result.final_stage is ConductorStage.FAILED
