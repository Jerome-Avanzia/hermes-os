"""Tests for Founder Workflow Engine (Sprint 52).

Validates deterministic workflow orchestration: same PromptPackage +
same RoutingDecision + same ApprovalStatus → same FounderWorkflow.

The WorkflowEngine:
- Does NOT execute inference
- Does NOT call providers
- Produces a typed FounderWorkflow with full audit metadata

Coverage:
- Determinism (same inputs → same output)
- Stage transitions (all paths)
- Approval handling (NOT_REQUIRED, PENDING, APPROVED, REJECTED)
- Declarative transition table (Amendment 3)
- Audit metadata (Amendment 4)
- WorkflowAction (Amendment 1)
- WorkflowReason (Amendment 2)
- Routing integration (RoutingDecision consumed correctly)
- Provider-agnosticism (Amendment 5)
- Edge cases (empty routing, no model, etc.)
"""

from __future__ import annotations

import pytest

from hermes.kernel.workflow_engine import (
    APPROVAL_REQUIRED_POLICIES,
    INTENT_KEYWORDS,
    STAGE_NEXT_ACTION,
    TRANSITION_TABLE,
    WorkflowEngine,
    _DEFAULT_INTENT,
)
from hermes.models.founder_workflow import (
    ApprovalDecision,
    ApprovalStatus,
    FounderWorkflow,
    StageTransition,
    TransitionCondition,
    TransitionTarget,
    WorkflowAction,
    WorkflowAudit,
    WorkflowIntent,
    WorkflowReason,
    WorkflowStage,
)
from hermes.models.prompt_package import (
    PromptPackage,
    PromptSection,
    TruncationReport,
)
from hermes.models.routing_decision import (
    CostTier,
    LatencyTier,
    Locality,
    ModelEntry,
    RejectedModel,
    RoutingDecision,
    RoutingPolicy,
    RoutingReason,
    SelectedModel,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _package(
    chars: int = 1_000,
    workspace_id: str = "ws-test",
    query: str = "test query",
    budget: str = "8k",
) -> PromptPackage:
    """Minimal PromptPackage for testing."""
    section = PromptSection(name="header", content="x" * chars, estimated_chars=chars)
    report = TruncationReport(total_sections=1, rendered_chars=chars, budget_chars=chars)
    return PromptPackage(
        system_prompt="You are Hermes.",
        sections=[section],
        estimated_chars=chars,
        truncation_report=report,
        query=query,
        workspace_id=workspace_id,
        recommended_budget=budget,
    )


def _selected(
    model_id: str = "test--model",
    provider: str = "test",
    name: str = "Test Model",
    context_window: int = 128_000,
    locality: Locality = Locality.CLOUD,
    score: float = 75.0,
) -> SelectedModel:
    return SelectedModel(
        model_id=model_id,
        provider=provider,
        name=name,
        context_window=context_window,
        reasons=[RoutingReason.PRIMARY_RECOMMENDATION, RoutingReason.CONTEXT_FITS],
        score=score,
        locality=locality,
    )


def _routing(
    routed: bool = True,
    policy: RoutingPolicy = RoutingPolicy.BALANCED,
    selected: SelectedModel | None = None,
    fallbacks: list[SelectedModel] | None = None,
    budget_compatible: bool = True,
    context_usage: float = 0.1,
    score: float = 75.0,
) -> RoutingDecision:
    """Minimal RoutingDecision for testing."""
    if selected is None and routed:
        selected = _selected()
    return RoutingDecision(
        selected=selected,
        fallbacks=fallbacks or [],
        policy=policy,
        reasons=[RoutingReason.PRIMARY_RECOMMENDATION] if routed else [RoutingReason.NO_MODELS_AVAILABLE],
        estimated_context_usage=context_usage,
        budget_compatible=budget_compatible,
        recommended_budget="8k",
        prompt_chars=1_000,
        models_evaluated=3,
        models_filtered=1,
        evaluated_model_ids=["test--model"],
        rejected_models=[],
        selected_score=score if routed else 0.0,
        fallback_scores=[],
    )


def _engine() -> WorkflowEngine:
    return WorkflowEngine()


# ── Determinism ───────────────────────────────────────────────────────────


class TestDeterminism:
    """Same inputs must always produce the same FounderWorkflow."""

    def test_identical_inputs_produce_identical_workflows(self) -> None:
        engine = _engine()
        pkg = _package()
        routing = _routing()
        w1 = engine.build(pkg, routing, workflow_id="wf-1")
        w2 = engine.build(pkg, routing, workflow_id="wf-1")
        assert w1.current_stage == w2.current_stage
        assert w1.next_action == w2.next_action
        assert w1.approval_required == w2.approval_required
        assert w1.approval_status == w2.approval_status
        assert w1.completed_stages == w2.completed_stages
        assert w1.pending_stages == w2.pending_stages

    def test_repeated_calls_same_reasons(self) -> None:
        engine = _engine()
        pkg = _package()
        routing = _routing(policy=RoutingPolicy.BALANCED)
        reasons = [engine.build(pkg, routing).reasons for _ in range(5)]
        for r in reasons[1:]:
            assert r == reasons[0]

    def test_different_policies_may_differ(self) -> None:
        engine = _engine()
        pkg = _package()
        d_balanced = engine.build(pkg, _routing(policy=RoutingPolicy.BALANCED))
        d_cloud_only = engine.build(pkg, _routing(policy=RoutingPolicy.CLOUD_ONLY))
        # CLOUD_ONLY requires approval; BALANCED does not
        assert d_balanced.approval_required is False
        assert d_cloud_only.approval_required is True

    def test_routing_summary_is_deterministic(self) -> None:
        engine = _engine()
        pkg = _package()
        routing = _routing()
        summaries = [engine.build(pkg, routing).audit.routing_summary for _ in range(5)]
        for s in summaries[1:]:
            assert s == summaries[0]

    def test_no_timestamps_in_workflow(self) -> None:
        """FounderWorkflow must not contain any time-based fields."""
        engine = _engine()
        wf = engine.build(_package(), _routing())
        # Verify the dataclass has no timestamp-like attributes
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(wf)}
        time_fields = {n for n in field_names if "time" in n or "stamp" in n or "date" in n}
        assert not time_fields, f"Unexpected time fields: {time_fields}"


# ── Stage Transitions ─────────────────────────────────────────────────────


class TestStageTransitions:
    """All workflow paths must produce correct stage sequences."""

    def test_no_approval_path(self) -> None:
        """BALANCED policy → no approval → READY_FOR_RUNTIME."""
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.current_stage == WorkflowStage.READY_FOR_RUNTIME
        assert WorkflowStage.CONTEXT_READY in wf.completed_stages
        assert WorkflowStage.ROUTING_COMPLETE in wf.completed_stages
        assert WorkflowStage.READY_FOR_GENERATION in wf.completed_stages

    def test_approval_pending_path(self) -> None:
        """CLOUD_ONLY + PENDING → WAITING_FOR_FOUNDER_APPROVAL."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert wf.current_stage == WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL
        assert WorkflowStage.READY_FOR_GENERATION in wf.completed_stages

    def test_approval_approved_path(self) -> None:
        """CLOUD_ONLY + APPROVED → READY_FOR_RUNTIME."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.APPROVED,
        )
        assert wf.current_stage == WorkflowStage.READY_FOR_RUNTIME
        assert WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL in wf.completed_stages
        assert WorkflowStage.APPROVED in wf.completed_stages

    def test_approval_rejected_path(self) -> None:
        """CLOUD_ONLY + REJECTED → REJECTED."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.REJECTED,
        )
        assert wf.current_stage == WorkflowStage.REJECTED
        assert WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL in wf.completed_stages

    def test_routing_failed_path(self) -> None:
        """No model routed → REJECTED immediately."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(routed=False, policy=RoutingPolicy.BALANCED),
        )
        assert wf.current_stage == WorkflowStage.REJECTED
        assert wf.completed_stages == [WorkflowStage.CONTEXT_READY]

    def test_completed_stages_does_not_include_current(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.current_stage not in wf.completed_stages

    def test_visited_stages_includes_current(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.current_stage in wf.audit.visited_stages

    def test_visited_stages_equals_completed_plus_current(self) -> None:
        engine = _engine()
        for policy in RoutingPolicy:
            routing = _routing(policy=policy)
            wf = engine.build(_package(), routing)
            expected = wf.completed_stages + [wf.current_stage]
            assert wf.audit.visited_stages == expected

    def test_highest_quality_requires_approval(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.HIGHEST_QUALITY))
        assert wf.approval_required is True

    def test_balanced_does_not_require_approval(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.approval_required is False

    def test_prefer_local_does_not_require_approval(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.PREFER_LOCAL))
        assert wf.approval_required is False

    def test_cheapest_does_not_require_approval(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.CHEAPEST))
        assert wf.approval_required is False


# ── Approval Handling ─────────────────────────────────────────────────────


class TestApprovalHandling:
    """Approval status must be correctly propagated and reflected."""

    def test_no_approval_required_status_is_not_required(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.approval_status == ApprovalStatus.NOT_REQUIRED

    def test_approval_required_pending_status(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert wf.approval_status == ApprovalStatus.PENDING

    def test_approval_required_approved_status(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.APPROVED,
        )
        assert wf.approval_status == ApprovalStatus.APPROVED

    def test_approval_required_rejected_status(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.REJECTED,
        )
        assert wf.approval_status == ApprovalStatus.REJECTED

    def test_approval_not_required_overrides_pending_input(self) -> None:
        """When policy doesn't require approval, status must be NOT_REQUIRED."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.BALANCED),
            approval_status=ApprovalStatus.PENDING,
        )
        assert wf.approval_status == ApprovalStatus.NOT_REQUIRED

    def test_routing_failed_does_not_require_approval(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(routed=False, policy=RoutingPolicy.CLOUD_ONLY),
        )
        assert wf.approval_required is False

    def test_pending_stages_when_waiting_for_approval(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert WorkflowStage.APPROVED in wf.pending_stages
        assert WorkflowStage.READY_FOR_RUNTIME in wf.pending_stages

    def test_no_pending_stages_when_ready_for_runtime(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.pending_stages == []

    def test_no_pending_stages_when_rejected(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(routed=False))
        assert wf.pending_stages == []

    def test_audit_records_approval_status_when_required(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert ApprovalStatus.PENDING in wf.audit.approval_decisions

    def test_audit_records_not_required_when_not_required(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert ApprovalStatus.NOT_REQUIRED in wf.audit.approval_decisions

    def test_approval_policies_are_declared(self) -> None:
        """APPROVAL_REQUIRED_POLICIES must exist and be non-empty."""
        assert len(APPROVAL_REQUIRED_POLICIES) > 0
        for p in APPROVAL_REQUIRED_POLICIES:
            assert isinstance(p, RoutingPolicy)


# ── Transition Table (Amendment 3) ───────────────────────────────────────


class TestTransitionTable:
    """The transition table must be declarative data, not logic."""

    def test_transition_table_is_dict(self) -> None:
        assert isinstance(TRANSITION_TABLE, dict)

    def test_transition_table_keys_are_tuples(self) -> None:
        for key in TRANSITION_TABLE:
            assert isinstance(key, tuple)
            assert len(key) == 2
            stage, condition = key
            assert isinstance(stage, WorkflowStage)
            assert isinstance(condition, TransitionCondition)

    def test_transition_table_values_are_transition_targets(self) -> None:
        for value in TRANSITION_TABLE.values():
            assert isinstance(value, TransitionTarget)
            assert isinstance(value.next_stage, WorkflowStage)
            assert isinstance(value.action, WorkflowAction)
            assert isinstance(value.reason, WorkflowReason)

    def test_context_ready_routing_succeeded_exists(self) -> None:
        key = (WorkflowStage.CONTEXT_READY, TransitionCondition.ROUTING_SUCCEEDED)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.ROUTING_COMPLETE

    def test_context_ready_routing_failed_exists(self) -> None:
        key = (WorkflowStage.CONTEXT_READY, TransitionCondition.ROUTING_FAILED)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.REJECTED
        assert target.action == WorkflowAction.STOP

    def test_ready_for_generation_approval_required_exists(self) -> None:
        key = (WorkflowStage.READY_FOR_GENERATION, TransitionCondition.APPROVAL_REQUIRED)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL
        assert target.action == WorkflowAction.REQUEST_APPROVAL

    def test_ready_for_generation_approval_not_required_exists(self) -> None:
        key = (WorkflowStage.READY_FOR_GENERATION, TransitionCondition.APPROVAL_NOT_REQUIRED)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.READY_FOR_RUNTIME
        assert target.action == WorkflowAction.ROUTE_TO_RUNTIME

    def test_waiting_approval_approved_exists(self) -> None:
        key = (WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL, TransitionCondition.APPROVAL_APPROVED)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.APPROVED
        assert target.reason == WorkflowReason.APPROVAL_RECEIVED

    def test_waiting_approval_rejected_exists(self) -> None:
        key = (WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL, TransitionCondition.APPROVAL_REJECTED)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.REJECTED
        assert target.action == WorkflowAction.STOP

    def test_approved_always_exists(self) -> None:
        key = (WorkflowStage.APPROVED, TransitionCondition.ALWAYS)
        assert key in TRANSITION_TABLE
        target = TRANSITION_TABLE[key]
        assert target.next_stage == WorkflowStage.READY_FOR_RUNTIME

    def test_terminal_stages_not_in_table_as_sources(self) -> None:
        """REJECTED and READY_FOR_RUNTIME must have no outgoing transitions."""
        terminal = {WorkflowStage.REJECTED, WorkflowStage.READY_FOR_RUNTIME}
        for (stage, _) in TRANSITION_TABLE:
            assert stage not in terminal, (
                f"Terminal stage {stage} must not appear as a table source"
            )

    def test_stage_next_action_covers_all_stages(self) -> None:
        """Every WorkflowStage must have a defined next-action."""
        for stage in WorkflowStage:
            assert stage in STAGE_NEXT_ACTION, (
                f"WorkflowStage.{stage.name} missing from STAGE_NEXT_ACTION"
            )


# ── Audit Metadata (Amendment 4) ─────────────────────────────────────────


class TestAuditMetadata:
    """Every workflow must expose a complete, deterministic audit trail."""

    def test_audit_visited_stages_non_empty(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert len(wf.audit.visited_stages) > 0

    def test_audit_transitions_non_empty_when_routed(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert len(wf.audit.transitions) > 0

    def test_audit_transitions_have_correct_fields(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        for t in wf.audit.transitions:
            assert isinstance(t.from_stage, WorkflowStage)
            assert isinstance(t.to_stage, WorkflowStage)
            assert isinstance(t.condition, TransitionCondition)
            assert isinstance(t.reason, WorkflowReason)
            assert isinstance(t.action, WorkflowAction)

    def test_audit_transitions_form_continuous_chain(self) -> None:
        """to_stage of transition N must equal from_stage of transition N+1."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.APPROVED,
        )
        transitions = wf.audit.transitions
        for i in range(len(transitions) - 1):
            assert transitions[i].to_stage == transitions[i + 1].from_stage

    def test_audit_routing_summary_is_string(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert isinstance(wf.audit.routing_summary, str)
        assert len(wf.audit.routing_summary) > 0

    def test_audit_routing_summary_contains_policy(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.CHEAPEST))
        assert "cheapest" in wf.audit.routing_summary

    def test_audit_routing_summary_contains_model_id_when_routed(self) -> None:
        engine = _engine()
        routing = _routing(
            selected=_selected(model_id="test--alpha"),
            policy=RoutingPolicy.BALANCED,
        )
        wf = engine.build(_package(), routing)
        assert "test--alpha" in wf.audit.routing_summary

    def test_audit_routing_summary_when_no_model(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(routed=False))
        assert "no_model_selected" in wf.audit.routing_summary

    def test_audit_approval_decisions_not_empty(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert len(wf.audit.approval_decisions) > 0

    def test_reasons_count_matches_transition_count(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert len(wf.reasons) == len(wf.audit.transitions)

    def test_transition_count_property(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert wf.transition_count == len(wf.audit.transitions)


# ── WorkflowAction (Amendment 1) ─────────────────────────────────────────


class TestWorkflowAction:
    """The engine must determine the correct next action without executing it."""

    def test_next_action_is_route_to_runtime_when_ready(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.next_action == WorkflowAction.ROUTE_TO_RUNTIME

    def test_next_action_is_wait_when_pending_approval(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert wf.next_action == WorkflowAction.WAIT

    def test_next_action_is_stop_when_rejected(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(routed=False))
        assert wf.next_action == WorkflowAction.STOP

    def test_next_action_is_route_to_runtime_after_approval(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.APPROVED,
        )
        assert wf.next_action == WorkflowAction.ROUTE_TO_RUNTIME

    def test_next_action_is_stop_when_approval_rejected(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.REJECTED,
        )
        assert wf.next_action == WorkflowAction.STOP

    def test_workflow_action_enum_has_required_values(self) -> None:
        required = [
            "GENERATE_RESPONSE",
            "REQUEST_APPROVAL",
            "WAIT",
            "STOP",
            "ROUTE_TO_RUNTIME",
        ]
        names = {a.name for a in WorkflowAction}
        for name in required:
            assert name in names, f"WorkflowAction.{name} is missing"

    def test_next_action_is_typed(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        assert isinstance(wf.next_action, WorkflowAction)


# ── WorkflowReason (Amendment 2) ─────────────────────────────────────────


class TestWorkflowReason:
    """All reason codes must be typed enum values — no free text."""

    def test_reasons_are_enum_values(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing())
        for reason in wf.reasons:
            assert isinstance(reason, WorkflowReason)

    def test_routing_complete_reason_present(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert WorkflowReason.ROUTING_COMPLETE in wf.reasons

    def test_no_model_available_reason_when_not_routed(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(routed=False))
        assert WorkflowReason.NO_MODEL_AVAILABLE in wf.reasons

    def test_approval_required_reason_when_approval_needed(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert WorkflowReason.APPROVAL_REQUIRED in wf.reasons

    def test_approval_received_reason_when_approved(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.APPROVED,
        )
        assert WorkflowReason.APPROVAL_RECEIVED in wf.reasons

    def test_approval_rejected_reason_when_rejected(self) -> None:
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.REJECTED,
        )
        assert WorkflowReason.APPROVAL_REJECTED in wf.reasons

    def test_ready_for_runtime_reason_when_no_approval(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert WorkflowReason.READY_FOR_RUNTIME in wf.reasons

    def test_workflow_reason_enum_has_required_values(self) -> None:
        required = [
            "ROUTING_COMPLETE",
            "APPROVAL_REQUIRED",
            "APPROVAL_RECEIVED",
            "POLICY_REQUIRES_REVIEW",
            "READY_FOR_RUNTIME",
        ]
        names = {r.name for r in WorkflowReason}
        for name in required:
            assert name in names, f"WorkflowReason.{name} is missing"


# ── Routing Integration ───────────────────────────────────────────────────


class TestRoutingIntegration:
    """RoutingDecision must be consumed correctly by the engine."""

    def test_routing_decision_stored_by_reference(self) -> None:
        engine = _engine()
        routing = _routing()
        wf = engine.build(_package(), routing)
        assert wf.routing_decision is routing

    def test_prompt_package_reference_format(self) -> None:
        engine = _engine()
        pkg = _package(workspace_id="ws-abc", query="what is the plan")
        wf = engine.build(pkg, _routing())
        assert wf.prompt_package_reference == "ws-abc:what is the plan"

    def test_context_package_reference_is_workspace_id(self) -> None:
        engine = _engine()
        pkg = _package(workspace_id="ws-xyz")
        wf = engine.build(pkg, _routing())
        assert wf.context_package_reference == "ws-xyz"

    def test_workflow_id_propagated(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(), workflow_id="sprint-52-test")
        assert wf.workflow_id == "sprint-52-test"

    def test_all_routing_policies_produce_workflow(self) -> None:
        engine = _engine()
        pkg = _package()
        for policy in RoutingPolicy:
            routing = _routing(policy=policy)
            wf = engine.build(pkg, routing)
            assert isinstance(wf, FounderWorkflow)
            assert isinstance(wf.current_stage, WorkflowStage)

    def test_approval_policies_subset_of_routing_policies(self) -> None:
        for policy in APPROVAL_REQUIRED_POLICIES:
            assert isinstance(policy, RoutingPolicy)

    def test_routing_with_fallbacks_is_handled(self) -> None:
        fallback = _selected(model_id="fallback--model", score=60.0)
        routing = _routing(fallbacks=[fallback])
        engine = _engine()
        wf = engine.build(_package(), routing)
        assert wf.current_stage == WorkflowStage.READY_FOR_RUNTIME


# ── Provider Agnosticism (Amendment 5) ───────────────────────────────────


class TestProviderAgnosticism:
    """The engine must never reference specific providers."""

    def test_engine_source_contains_no_provider_names(self) -> None:
        import inspect
        import hermes.kernel.workflow_engine as eng_module
        source = inspect.getsource(eng_module)
        forbidden = ["claude", "ollama", "openai", "anthropic", "kimi", "qwen"]
        for name in forbidden:
            # Allow in comments but not in logic (comparisons, string literals used in logic)
            assert f'== "{name}"' not in source.lower(), (
                f"Engine must not compare against provider '{name}'"
            )
            assert f"== '{name}'" not in source.lower(), (
                f"Engine must not compare against provider '{name}'"
            )

    def test_workflow_result_contains_no_hardcoded_provider(self) -> None:
        """FounderWorkflow fields must not expose provider-specific values."""
        engine = _engine()
        wf = engine.build(_package(), _routing())
        # current_stage, next_action, reasons, etc. are all typed enums
        assert isinstance(wf.current_stage, WorkflowStage)
        assert isinstance(wf.next_action, WorkflowAction)
        for reason in wf.reasons:
            assert isinstance(reason, WorkflowReason)

    def test_approval_policy_references_only_routing_policy_enum(self) -> None:
        """APPROVAL_REQUIRED_POLICIES must contain only RoutingPolicy values."""
        for policy in APPROVAL_REQUIRED_POLICIES:
            assert isinstance(policy, RoutingPolicy)


# ── Typed Models ──────────────────────────────────────────────────────────


class TestTypedModels:
    """All Sprint 52 types must be dataclasses with correct fields."""

    def test_founder_workflow_is_dataclass(self) -> None:
        import dataclasses
        assert dataclasses.is_dataclass(FounderWorkflow)

    def test_workflow_audit_is_dataclass(self) -> None:
        import dataclasses
        assert dataclasses.is_dataclass(WorkflowAudit)

    def test_stage_transition_is_dataclass(self) -> None:
        import dataclasses
        assert dataclasses.is_dataclass(StageTransition)

    def test_transition_target_is_frozen(self) -> None:
        """TransitionTarget must be immutable (frozen dataclass)."""
        target = TransitionTarget(
            next_stage=WorkflowStage.ROUTING_COMPLETE,
            action=WorkflowAction.GENERATE_RESPONSE,
            reason=WorkflowReason.ROUTING_COMPLETE,
        )
        with pytest.raises((AttributeError, TypeError)):
            target.next_stage = WorkflowStage.REJECTED  # type: ignore[misc]

    def test_workflow_stage_enum_has_required_values(self) -> None:
        required = [
            "CONTEXT_READY",
            "ROUTING_COMPLETE",
            "READY_FOR_GENERATION",
            "WAITING_FOR_FOUNDER_APPROVAL",
            "APPROVED",
            "REJECTED",
            "READY_FOR_RUNTIME",
        ]
        names = {s.name for s in WorkflowStage}
        for name in required:
            assert name in names, f"WorkflowStage.{name} is missing"

    def test_approval_status_enum_has_required_values(self) -> None:
        required = ["NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED"]
        names = {s.name for s in ApprovalStatus}
        for name in required:
            assert name in names, f"ApprovalStatus.{name} is missing"

    def test_approval_decision_enum_has_required_values(self) -> None:
        required = ["NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED"]
        names = {d.name for d in ApprovalDecision}
        for name in required:
            assert name in names, f"ApprovalDecision.{name} is missing"

    def test_founder_workflow_convenience_properties(self) -> None:
        engine = _engine()
        wf_ready = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        wf_rejected = engine.build(_package(), _routing(routed=False))
        wf_waiting = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.PENDING,
        )
        assert wf_ready.is_complete is True
        assert wf_ready.is_approved is True
        assert wf_ready.is_rejected is False
        assert wf_ready.is_waiting_for_approval is False

        assert wf_rejected.is_complete is True
        assert wf_rejected.is_approved is False
        assert wf_rejected.is_rejected is True

        assert wf_waiting.is_complete is False
        assert wf_waiting.is_waiting_for_approval is True


# ── WorkflowIntent (Founder Amendment) ───────────────────────────────────


class TestWorkflowIntent:
    """WorkflowIntent must be typed, deterministic, and distinct from next_action."""

    def test_workflow_intent_is_present_in_founder_workflow(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="create a report"), _routing())
        assert hasattr(wf, "workflow_intent")
        assert isinstance(wf.workflow_intent, WorkflowIntent)

    def test_intent_is_not_next_action(self) -> None:
        """workflow_intent and next_action must be different types."""
        engine = _engine()
        wf = engine.build(_package(query="generate a summary"), _routing())
        assert not isinstance(wf.workflow_intent, WorkflowAction)
        assert isinstance(wf.workflow_intent, WorkflowIntent)

    def test_intent_enum_has_required_values(self) -> None:
        required = [
            "GENERATE", "ANALYZE", "PLAN", "REVIEW",
            "EXECUTE", "VALIDATE", "LEARN", "DOCUMENT",
        ]
        names = {i.name for i in WorkflowIntent}
        for name in required:
            assert name in names, f"WorkflowIntent.{name} is missing"

    # ── Keyword matching ──────────────────────────────────────────────

    def test_generate_intent_from_create(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="create a business proposal"), _routing())
        assert wf.workflow_intent == WorkflowIntent.GENERATE

    def test_generate_intent_from_write(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="write an email to the team"), _routing())
        assert wf.workflow_intent == WorkflowIntent.GENERATE

    def test_analyze_intent_from_analyze(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="analyze the sales data"), _routing())
        assert wf.workflow_intent == WorkflowIntent.ANALYZE

    def test_analyze_intent_from_evaluate(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="evaluate the customer feedback"), _routing())
        assert wf.workflow_intent == WorkflowIntent.ANALYZE

    def test_plan_intent_from_plan(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="plan the next quarter"), _routing())
        assert wf.workflow_intent == WorkflowIntent.PLAN

    def test_plan_intent_from_roadmap(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="build a product roadmap"), _routing())
        assert wf.workflow_intent == WorkflowIntent.PLAN

    def test_review_intent_from_review(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="review the pull request"), _routing())
        assert wf.workflow_intent == WorkflowIntent.REVIEW

    def test_review_intent_from_audit(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="audit the financial records"), _routing())
        assert wf.workflow_intent == WorkflowIntent.REVIEW

    def test_execute_intent_from_deploy(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="deploy the new release"), _routing())
        assert wf.workflow_intent == WorkflowIntent.EXECUTE

    def test_execute_intent_from_run(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="run the onboarding workflow"), _routing())
        assert wf.workflow_intent == WorkflowIntent.EXECUTE

    def test_validate_intent_from_verify(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="verify the configuration is correct"), _routing())
        assert wf.workflow_intent == WorkflowIntent.VALIDATE

    def test_validate_intent_from_validate(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="validate the data schema"), _routing())
        assert wf.workflow_intent == WorkflowIntent.VALIDATE

    def test_learn_intent_from_explain(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="explain how the pipeline works"), _routing())
        assert wf.workflow_intent == WorkflowIntent.LEARN

    def test_learn_intent_from_what_is(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="what is the current approach"), _routing())
        assert wf.workflow_intent == WorkflowIntent.LEARN

    def test_document_intent_from_summarize(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="summarize the meeting notes"), _routing())
        assert wf.workflow_intent == WorkflowIntent.DOCUMENT

    def test_document_intent_from_document(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="document the API endpoints"), _routing())
        assert wf.workflow_intent == WorkflowIntent.DOCUMENT

    def test_default_intent_when_no_keyword_matches(self) -> None:
        engine = _engine()
        wf = engine.build(_package(query="hermes"), _routing())
        assert wf.workflow_intent == _DEFAULT_INTENT
        assert _DEFAULT_INTENT == WorkflowIntent.GENERATE

    def test_intent_is_case_insensitive(self) -> None:
        engine = _engine()
        wf_lower = engine.build(_package(query="analyze the results"), _routing())
        wf_upper = engine.build(_package(query="ANALYZE the results"), _routing())
        wf_mixed = engine.build(_package(query="Analyze The Results"), _routing())
        assert wf_lower.workflow_intent == WorkflowIntent.ANALYZE
        assert wf_upper.workflow_intent == WorkflowIntent.ANALYZE
        assert wf_mixed.workflow_intent == WorkflowIntent.ANALYZE

    # ── Determinism ───────────────────────────────────────────────────

    def test_same_query_same_intent(self) -> None:
        engine = _engine()
        query = "analyze the monthly performance metrics"
        intents = [
            engine.build(_package(query=query), _routing()).workflow_intent
            for _ in range(5)
        ]
        assert all(i == intents[0] for i in intents)

    def test_intent_independent_of_stage(self) -> None:
        """Intent must be the same regardless of approval status."""
        engine = _engine()
        query = "generate the quarterly report"
        pkg = _package(query=query)
        routing = _routing(policy=RoutingPolicy.CLOUD_ONLY)
        wf_pending = engine.build(pkg, routing, approval_status=ApprovalStatus.PENDING)
        wf_approved = engine.build(pkg, routing, approval_status=ApprovalStatus.APPROVED)
        assert wf_pending.workflow_intent == wf_approved.workflow_intent

    def test_intent_independent_of_routing_policy(self) -> None:
        """Intent must be the same regardless of routing policy."""
        engine = _engine()
        query = "plan the product roadmap"
        pkg = _package(query=query)
        intents = [
            engine.build(pkg, _routing(policy=p)).workflow_intent
            for p in RoutingPolicy
        ]
        assert all(i == intents[0] for i in intents)
        assert intents[0] == WorkflowIntent.PLAN

    # ── Declarative table ─────────────────────────────────────────────

    def test_intent_keywords_table_is_list(self) -> None:
        assert isinstance(INTENT_KEYWORDS, list)

    def test_intent_keywords_table_entries_are_typed(self) -> None:
        for entry in INTENT_KEYWORDS:
            intent, keywords = entry
            assert isinstance(intent, WorkflowIntent)
            assert isinstance(keywords, frozenset)
            for kw in keywords:
                assert isinstance(kw, str)

    def test_intent_keywords_covers_all_intents(self) -> None:
        """Every WorkflowIntent must appear at least once in the keyword table."""
        covered = {intent for intent, _ in INTENT_KEYWORDS}
        for intent in WorkflowIntent:
            assert intent in covered, (
                f"WorkflowIntent.{intent.name} has no keywords in INTENT_KEYWORDS"
            )

    def test_intent_keywords_has_no_duplicates(self) -> None:
        """Each WorkflowIntent must appear at most once in the table."""
        seen: list[WorkflowIntent] = []
        for intent, _ in INTENT_KEYWORDS:
            assert intent not in seen, f"Duplicate entry for {intent.name}"
            seen.append(intent)


# ── Edge Cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Engine must handle all boundary inputs correctly."""

    def test_empty_workflow_id(self) -> None:
        engine = _engine()
        wf = engine.build(_package(), _routing(), workflow_id="")
        assert wf.workflow_id == ""

    def test_engine_does_not_mutate_package(self) -> None:
        engine = _engine()
        pkg = _package(chars=5_000)
        original_chars = pkg.estimated_chars
        original_query = pkg.query
        engine.build(pkg, _routing())
        assert pkg.estimated_chars == original_chars
        assert pkg.query == original_query

    def test_engine_does_not_mutate_routing(self) -> None:
        engine = _engine()
        routing = _routing()
        original_policy = routing.policy
        original_score = routing.selected_score
        engine.build(_package(), routing)
        assert routing.policy == original_policy
        assert routing.selected_score == original_score

    def test_all_routing_policies_produce_valid_stage(self) -> None:
        engine = _engine()
        for policy in RoutingPolicy:
            routing = _routing(policy=policy)
            wf = engine.build(_package(), routing)
            assert wf.current_stage in WorkflowStage.__members__.values()

    def test_workflow_with_zero_char_package(self) -> None:
        engine = _engine()
        pkg = _package(chars=0)
        wf = engine.build(pkg, _routing())
        assert isinstance(wf, FounderWorkflow)

    def test_workflow_engine_is_stateless(self) -> None:
        """Calling build() multiple times must not affect subsequent calls."""
        engine = _engine()
        pkg = _package()
        routing = _routing(policy=RoutingPolicy.BALANCED)
        results = [engine.build(pkg, routing, workflow_id=str(i)) for i in range(5)]
        stages = [wf.current_stage for wf in results]
        assert all(s == stages[0] for s in stages)

    def test_transition_count_is_zero_for_immediately_rejected(self) -> None:
        """Routing failure produces exactly one transition."""
        engine = _engine()
        wf = engine.build(_package(), _routing(routed=False))
        assert wf.transition_count == 1

    def test_no_approval_workflow_has_correct_transition_count(self) -> None:
        """BALANCED policy: CONTEXT_READY→ROUTING_COMPLETE→READY_FOR_GENERATION→READY_FOR_RUNTIME = 3 transitions."""
        engine = _engine()
        wf = engine.build(_package(), _routing(policy=RoutingPolicy.BALANCED))
        assert wf.transition_count == 3

    def test_approval_approved_workflow_has_correct_transition_count(self) -> None:
        """CLOUD_ONLY + APPROVED: 3 base + 2 approval = 5 transitions."""
        engine = _engine()
        wf = engine.build(
            _package(),
            _routing(policy=RoutingPolicy.CLOUD_ONLY),
            approval_status=ApprovalStatus.APPROVED,
        )
        assert wf.transition_count == 5
