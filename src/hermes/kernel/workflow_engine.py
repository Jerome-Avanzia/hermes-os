"""WorkflowEngine — deterministic founder workflow orchestration.

Sprint 52: Accepts a PromptPackage and RoutingDecision and produces a
FounderWorkflow describing the current pipeline state, any required
approvals, and the next action — without executing anything.

Sprint 52 (Amendment 3): All stage transitions are driven by a single
declarative TRANSITION_TABLE.  Adding new stages or conditions requires
only extending the table and enums — no changes to engine logic.

Sprint 52 (Amendment 5): The engine is provider-agnostic.  It never
references Claude, Ollama, OpenAI, Anthropic, or any other provider.
It orchestrates workflow only.

The WorkflowEngine:
- Does NOT execute inference
- Does NOT call providers
- Does NOT execute tools
- Does NOT invoke operations
- Does NOT modify prompts
- IS deterministic: same inputs always produce the same FounderWorkflow
"""

from __future__ import annotations

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
from hermes.models.prompt_package import PromptPackage
from hermes.models.routing_decision import RoutingDecision, RoutingPolicy


# ── Approval policy ──────────────────────────────────────────────────────
#
# Declarative: which routing policies require founder approval before
# the workflow may proceed to runtime.  To change approval requirements,
# edit this set — not the engine logic.

APPROVAL_REQUIRED_POLICIES: frozenset[RoutingPolicy] = frozenset({
    RoutingPolicy.CLOUD_ONLY,
    RoutingPolicy.HIGHEST_QUALITY,
})


# ── Next-action map ──────────────────────────────────────────────────────
#
# Declarative map from terminal/paused stage to the action that should
# be taken next.  The engine reads this — never computes actions inline.

STAGE_NEXT_ACTION: dict[WorkflowStage, WorkflowAction] = {
    WorkflowStage.CONTEXT_READY:                 WorkflowAction.GENERATE_RESPONSE,
    WorkflowStage.ROUTING_COMPLETE:              WorkflowAction.GENERATE_RESPONSE,
    WorkflowStage.READY_FOR_GENERATION:          WorkflowAction.GENERATE_RESPONSE,
    WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL:  WorkflowAction.WAIT,
    WorkflowStage.APPROVED:                      WorkflowAction.ROUTE_TO_RUNTIME,
    WorkflowStage.READY_FOR_RUNTIME:             WorkflowAction.ROUTE_TO_RUNTIME,
    WorkflowStage.REJECTED:                      WorkflowAction.STOP,
}


# ── Pending-stages map ───────────────────────────────────────────────────
#
# Declarative map from the current (paused/terminal) stage to the list of
# stages still expected in the happy path.

_PENDING_AFTER: dict[WorkflowStage, list[WorkflowStage]] = {
    WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL: [
        WorkflowStage.APPROVED,
        WorkflowStage.READY_FOR_RUNTIME,
    ],
    WorkflowStage.REJECTED:         [],
    WorkflowStage.READY_FOR_RUNTIME: [],
}


# ── Intent keyword table (Founder Amendment) ────────────────────────────
#
# Declarative map from WorkflowIntent to its trigger keywords.
# The engine scans the lowercased query against each entry in order.
# First match wins.  GENERATE is the fallback when no keyword matches.
#
# To add a new intent: extend WorkflowIntent and add an entry here.
# The engine logic never changes.

INTENT_KEYWORDS: list[tuple[WorkflowIntent, frozenset[str]]] = [
    (WorkflowIntent.VALIDATE, frozenset({
        "validate", "verify", "confirm", "ensure", "check if", "check that",
        "assert", "certify",
    })),
    (WorkflowIntent.EXECUTE, frozenset({
        "execute", "deploy", "run", "apply", "perform", "implement", "trigger",
        "launch", "activate",
    })),
    (WorkflowIntent.PLAN, frozenset({
        "plan", "strategy", "roadmap", "prioritize", "prioritise", "schedule",
        "outline", "milestone", "quarter",
    })),
    (WorkflowIntent.ANALYZE, frozenset({
        "analyze", "analyse", "analysis", "evaluate", "assess", "examine",
        "diagnose", "investigate", "measure", "metric",
    })),
    (WorkflowIntent.REVIEW, frozenset({
        "review", "audit", "inspect", "feedback", "critique", "proofread",
        "check",
    })),
    (WorkflowIntent.LEARN, frozenset({
        "explain", "what is", "what are", "how does", "how do", "learn",
        "understand", "describe", "teach", "clarify", "definition",
    })),
    (WorkflowIntent.DOCUMENT, frozenset({
        "document", "summarize", "summarise", "summary", "report", "record",
        "log", "note", "write up", "transcript",
    })),
    (WorkflowIntent.GENERATE, frozenset({
        "generate", "create", "write", "produce", "draft", "build", "compose",
        "design", "make",
    })),
]

# Default intent when no keyword matches
_DEFAULT_INTENT = WorkflowIntent.GENERATE


# ── Transition table (Amendment 3) ──────────────────────────────────────
#
# The sole source of truth for stage progression.  Each key is a
# (from_stage, condition) pair.  Each value is the TransitionTarget
# (next_stage, action, reason).
#
# To add a new stage or condition: add entries here and extend the enums.
# The engine loop never changes.

TRANSITION_TABLE: dict[tuple[WorkflowStage, TransitionCondition], TransitionTarget] = {

    # ── From CONTEXT_READY ─────────────────────────────────────────
    (WorkflowStage.CONTEXT_READY, TransitionCondition.ROUTING_SUCCEEDED): TransitionTarget(
        next_stage=WorkflowStage.ROUTING_COMPLETE,
        action=WorkflowAction.GENERATE_RESPONSE,
        reason=WorkflowReason.ROUTING_COMPLETE,
    ),
    (WorkflowStage.CONTEXT_READY, TransitionCondition.ROUTING_FAILED): TransitionTarget(
        next_stage=WorkflowStage.REJECTED,
        action=WorkflowAction.STOP,
        reason=WorkflowReason.NO_MODEL_AVAILABLE,
    ),

    # ── From ROUTING_COMPLETE ──────────────────────────────────────
    (WorkflowStage.ROUTING_COMPLETE, TransitionCondition.ALWAYS): TransitionTarget(
        next_stage=WorkflowStage.READY_FOR_GENERATION,
        action=WorkflowAction.GENERATE_RESPONSE,
        reason=WorkflowReason.ROUTING_COMPLETE,
    ),

    # ── From READY_FOR_GENERATION ──────────────────────────────────
    (WorkflowStage.READY_FOR_GENERATION, TransitionCondition.APPROVAL_REQUIRED): TransitionTarget(
        next_stage=WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL,
        action=WorkflowAction.REQUEST_APPROVAL,
        reason=WorkflowReason.APPROVAL_REQUIRED,
    ),
    (WorkflowStage.READY_FOR_GENERATION, TransitionCondition.APPROVAL_NOT_REQUIRED): TransitionTarget(
        next_stage=WorkflowStage.READY_FOR_RUNTIME,
        action=WorkflowAction.ROUTE_TO_RUNTIME,
        reason=WorkflowReason.READY_FOR_RUNTIME,
    ),

    # ── From WAITING_FOR_FOUNDER_APPROVAL ─────────────────────────
    (WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL, TransitionCondition.APPROVAL_APPROVED): TransitionTarget(
        next_stage=WorkflowStage.APPROVED,
        action=WorkflowAction.ROUTE_TO_RUNTIME,
        reason=WorkflowReason.APPROVAL_RECEIVED,
    ),
    (WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL, TransitionCondition.APPROVAL_REJECTED): TransitionTarget(
        next_stage=WorkflowStage.REJECTED,
        action=WorkflowAction.STOP,
        reason=WorkflowReason.APPROVAL_REJECTED,
    ),
    # APPROVAL_PENDING: _evaluate_condition returns None → walker stops

    # ── From APPROVED ──────────────────────────────────────────────
    (WorkflowStage.APPROVED, TransitionCondition.ALWAYS): TransitionTarget(
        next_stage=WorkflowStage.READY_FOR_RUNTIME,
        action=WorkflowAction.ROUTE_TO_RUNTIME,
        reason=WorkflowReason.READY_FOR_RUNTIME,
    ),

    # REJECTED and READY_FOR_RUNTIME are terminal — no entries.
}


# ── WorkflowEngine ───────────────────────────────────────────────────────


class WorkflowEngine:
    """Deterministic founder workflow orchestrator.

    Accepts a PromptPackage and RoutingDecision.
    Produces a FounderWorkflow.
    Never executes anything.

    The engine walks the TRANSITION_TABLE from CONTEXT_READY, evaluating
    one condition per stage, until it reaches a terminal or paused stage.
    All logic is driven by declarative data (Amendment 3).
    """

    def build(
        self,
        package: PromptPackage,
        routing: RoutingDecision,
        workflow_id: str = "",
        approval_status: ApprovalStatus = ApprovalStatus.PENDING,
    ) -> FounderWorkflow:
        """Build a FounderWorkflow from a PromptPackage and RoutingDecision.

        Deterministic: same inputs always produce the same FounderWorkflow.
        No side effects.  No provider calls.  No inference.

        Args:
            package:         The compressed PromptPackage from Sprint 50.
            routing:         The RoutingDecision from Sprint 51.
            workflow_id:     Caller-supplied identifier (no internal UUID).
            approval_status: Current approval state (default: PENDING).

        Returns:
            A fully populated FounderWorkflow.
        """
        requires_approval = self._check_approval_required(routing)
        intent = self._determine_intent(package.query)

        # ── Walk the transition table ─────────────────────────────────
        current_stage = WorkflowStage.CONTEXT_READY
        completed_stages: list[WorkflowStage] = []
        transitions: list[StageTransition] = []

        while True:
            condition = self._evaluate_condition(
                current_stage, routing, requires_approval, approval_status,
            )
            if condition is None:
                break  # terminal stage or awaiting external input

            key = (current_stage, condition)
            target = TRANSITION_TABLE.get(key)
            if target is None:
                break  # no rule for this (stage, condition) pair

            transitions.append(StageTransition(
                from_stage=current_stage,
                to_stage=target.next_stage,
                condition=condition,
                reason=target.reason,
                action=target.action,
            ))
            completed_stages.append(current_stage)
            current_stage = target.next_stage

        # ── Compute derived fields ────────────────────────────────────
        next_action = STAGE_NEXT_ACTION.get(current_stage, WorkflowAction.WAIT)
        pending_stages = list(_PENDING_AFTER.get(current_stage, []))
        reasons = [t.reason for t in transitions]

        effective_approval_status = (
            approval_status if requires_approval else ApprovalStatus.NOT_REQUIRED
        )

        audit = WorkflowAudit(
            visited_stages=completed_stages + [current_stage],
            transitions=transitions,
            approval_decisions=(
                [approval_status] if requires_approval
                else [ApprovalStatus.NOT_REQUIRED]
            ),
            routing_summary=self._build_routing_summary(routing),
        )

        return FounderWorkflow(
            workflow_id=workflow_id,
            current_stage=current_stage,
            completed_stages=completed_stages,
            pending_stages=pending_stages,
            approval_required=requires_approval,
            approval_status=effective_approval_status,
            routing_decision=routing,
            prompt_package_reference=f"{package.workspace_id}:{package.query}",
            context_package_reference=package.workspace_id,
            next_action=next_action,
            workflow_intent=intent,
            audit=audit,
            reasons=reasons,
        )

    # ── Condition evaluator ───────────────────────────────────────────

    @staticmethod
    def _evaluate_condition(
        stage: WorkflowStage,
        routing: RoutingDecision,
        requires_approval: bool,
        approval_status: ApprovalStatus,
    ) -> TransitionCondition | None:
        """Evaluate the active TransitionCondition for the given stage.

        Returns None when the stage is terminal or awaiting external input.
        The engine loop stops when None is returned.
        """
        match stage:
            case WorkflowStage.CONTEXT_READY:
                return (
                    TransitionCondition.ROUTING_SUCCEEDED if routing.routed
                    else TransitionCondition.ROUTING_FAILED
                )
            case WorkflowStage.ROUTING_COMPLETE:
                return TransitionCondition.ALWAYS
            case WorkflowStage.READY_FOR_GENERATION:
                return (
                    TransitionCondition.APPROVAL_REQUIRED if requires_approval
                    else TransitionCondition.APPROVAL_NOT_REQUIRED
                )
            case WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL:
                match approval_status:
                    case ApprovalStatus.APPROVED:
                        return TransitionCondition.APPROVAL_APPROVED
                    case ApprovalStatus.REJECTED:
                        return TransitionCondition.APPROVAL_REJECTED
                    case _:
                        return None  # PENDING — pause here
            case WorkflowStage.APPROVED:
                return TransitionCondition.ALWAYS
            case _:
                return None  # REJECTED, READY_FOR_RUNTIME — terminal

    # ── Approval policy ───────────────────────────────────────────────

    @staticmethod
    def _check_approval_required(routing: RoutingDecision) -> bool:
        """Determine whether founder approval is required.

        Based solely on the routing policy — declarative, no provider logic.
        Returns False when routing produced no model (nothing to approve).
        """
        if not routing.routed:
            return False
        return routing.policy in APPROVAL_REQUIRED_POLICIES

    # ── Intent determination (Founder Amendment) ─────────────────────

    @staticmethod
    def _determine_intent(query: str) -> WorkflowIntent:
        """Determine the workflow intent from the query.

        Scans the lowercased query against INTENT_KEYWORDS in priority order.
        First matching keyword wins.  Returns _DEFAULT_INTENT when no keyword
        matches.

        Deterministic: same query always produces the same WorkflowIntent.
        No AI reasoning.  No randomness.  Pure keyword lookup.
        """
        lowered = query.lower()
        for intent, keywords in INTENT_KEYWORDS:
            if any(kw in lowered for kw in keywords):
                return intent
        return _DEFAULT_INTENT

    # ── Routing summary ───────────────────────────────────────────────

    @staticmethod
    def _build_routing_summary(routing: RoutingDecision) -> str:
        """Build a deterministic text summary of the routing decision.

        Derived entirely from RoutingDecision fields — no external data.
        Used exclusively in audit metadata.
        """
        if not routing.routed:
            return f"policy={routing.policy.value} no_model_selected"
        return (
            f"policy={routing.policy.value} "
            f"model={routing.selected_model_id} "
            f"provider={routing.selected_provider} "
            f"score={routing.selected_score:.1f} "
            f"context_usage={routing.estimated_context_usage:.1%} "
            f"fallbacks={len(routing.fallbacks)}"
        )
