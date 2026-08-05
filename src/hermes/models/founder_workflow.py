"""FounderWorkflow — typed contract between Workflow Engine and LLM Runtime.

Sprint 52: The FounderWorkflow is the deterministic output of the Founder
Workflow Engine.  It describes exactly where in the pipeline execution
stands, what approvals are outstanding, and what must happen next.

The Workflow Engine produces it.  The LLM Runtime (and Founder UI) consumes
it.  This data structure is the boundary between orchestration and execution.

No timestamps.  No randomness.  No UUID generation.
Same inputs always produce the same FounderWorkflow.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# RoutingDecision is stored by reference in FounderWorkflow (read-only)
from hermes.models.routing_decision import RoutingDecision


class WorkflowIntent(enum.Enum):
    """The mission of the workflow — what Hermes is being asked to accomplish.

    Sprint 52 (Amendment): Determined deterministically by the Workflow Engine
    from the PromptPackage query using a declarative keyword table.

    Intent is NOT the next action.  It represents the semantic purpose of
    the entire workflow — independent of stage, approval status, or routing.
    """

    GENERATE = "generate"           # create, write, produce new content
    ANALYZE = "analyze"             # evaluate, assess, examine data or situations
    PLAN = "plan"                   # produce strategy, roadmap, prioritisation
    REVIEW = "review"               # audit, inspect, give feedback
    EXECUTE = "execute"             # run, deploy, apply, perform
    VALIDATE = "validate"           # verify, confirm, ensure correctness
    LEARN = "learn"                 # explain, describe, educate
    DOCUMENT = "document"           # summarise, record, report


class WorkflowStage(enum.Enum):
    """Deterministic stages in the founder workflow.

    Stages advance in a declared order defined by the transition table
    in workflow_engine.py.  No stage logic lives here — only labels.
    """

    CONTEXT_READY = "context_ready"
    ROUTING_COMPLETE = "routing_complete"
    READY_FOR_GENERATION = "ready_for_generation"
    WAITING_FOR_FOUNDER_APPROVAL = "waiting_for_founder_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    READY_FOR_RUNTIME = "ready_for_runtime"


class WorkflowAction(enum.Enum):
    """Typed next-action codes produced by the Workflow Engine.

    Sprint 52 (Amendment 1): The engine determines the next action.
    It never executes it.
    """

    GENERATE_RESPONSE = "generate_response"     # proceed to prompt generation
    REQUEST_APPROVAL = "request_approval"       # surface decision to founder
    WAIT = "wait"                               # hold — pending external input
    STOP = "stop"                               # workflow terminated
    ROUTE_TO_RUNTIME = "route_to_runtime"      # hand off to LLM Runtime


class WorkflowReason(enum.Enum):
    """Typed reason codes explaining workflow transitions.

    Sprint 52 (Amendment 2): All reasons are enum values — never free text.
    """

    ROUTING_COMPLETE = "routing_complete"           # routing succeeded
    APPROVAL_REQUIRED = "approval_required"         # policy mandates review
    APPROVAL_RECEIVED = "approval_received"         # founder approved
    APPROVAL_REJECTED = "approval_rejected"         # founder rejected
    POLICY_REQUIRES_REVIEW = "policy_requires_review"   # policy-driven approval
    READY_FOR_RUNTIME = "ready_for_runtime"         # all preconditions met
    NO_MODEL_AVAILABLE = "no_model_available"       # routing produced no model
    CONTEXT_ASSEMBLED = "context_assembled"         # context ready for routing


class ApprovalStatus(enum.Enum):
    """Current approval status in the workflow."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(enum.Enum):
    """A discrete founder decision recorded in the audit trail."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TransitionCondition(enum.Enum):
    """Named conditions evaluated against workflow inputs.

    Sprint 52 (Amendment 3): Used as keys in the declarative transition
    table.  Adding a new condition here never changes existing rules.
    """

    ROUTING_SUCCEEDED = "routing_succeeded"
    ROUTING_FAILED = "routing_failed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_NOT_REQUIRED = "approval_not_required"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_PENDING = "approval_pending"
    ALWAYS = "always"                              # unconditional transition


@dataclass(frozen=True, slots=True)
class TransitionTarget:
    """The destination of a declarative transition rule.

    Sprint 52 (Amendment 3): Each entry in the transition table maps
    (WorkflowStage, TransitionCondition) to one TransitionTarget.
    """

    next_stage: WorkflowStage
    action: WorkflowAction
    reason: WorkflowReason


@dataclass(slots=True)
class StageTransition:
    """A single recorded stage transition — part of the audit trail.

    Sprint 52 (Amendment 4): Every transition is recorded with its
    from/to stages, the condition that triggered it, the action taken,
    and the reason code.  No free text.
    """

    from_stage: WorkflowStage
    to_stage: WorkflowStage
    condition: TransitionCondition
    reason: WorkflowReason
    action: WorkflowAction


@dataclass(slots=True)
class WorkflowAudit:
    """Full audit trail for a FounderWorkflow.

    Sprint 52 (Amendment 4): Metadata only — no logging, no side effects.
    Exposes the complete history of every stage visited, every transition
    taken, every approval decision, and a deterministic routing summary.
    """

    visited_stages: list[WorkflowStage]            # all stages reached (in order)
    transitions: list[StageTransition]             # recorded stage transitions
    approval_decisions: list[ApprovalStatus]       # approval statuses observed
    routing_summary: str                           # deterministic routing description


@dataclass(slots=True)
class FounderWorkflow:
    """Deterministic orchestration output from the Founder Workflow Engine.

    Produced by WorkflowEngine.  Consumed by the LLM Runtime and Founder UI.
    Answers: where are we, what happened, what is pending, what is next —
    without executing anything.

    No timestamps.  No UUIDs generated internally.  No randomness.
    """

    workflow_id: str                               # caller-supplied identifier
    current_stage: WorkflowStage                  # stage at time of build
    completed_stages: list[WorkflowStage]         # stages fully exited
    pending_stages: list[WorkflowStage]           # stages expected next
    approval_required: bool                        # whether founder approval is needed
    approval_status: ApprovalStatus               # current approval state
    routing_decision: RoutingDecision             # routing output (read-only reference)
    prompt_package_reference: str                 # "{workspace_id}:{query}"
    context_package_reference: str               # workspace_id
    next_action: WorkflowAction                  # what should happen next
    workflow_intent: WorkflowIntent              # mission of this workflow
    audit: WorkflowAudit                         # full audit trail
    reasons: list[WorkflowReason]                # reason codes for all transitions

    # ── Convenience properties ────────────────────────────────────

    @property
    def is_complete(self) -> bool:
        """Whether the workflow has reached a terminal stage."""
        return self.current_stage in {
            WorkflowStage.READY_FOR_RUNTIME,
            WorkflowStage.REJECTED,
        }

    @property
    def is_approved(self) -> bool:
        """Whether the workflow is approved and ready for runtime."""
        return self.current_stage == WorkflowStage.READY_FOR_RUNTIME

    @property
    def is_rejected(self) -> bool:
        """Whether the workflow was rejected."""
        return self.current_stage == WorkflowStage.REJECTED

    @property
    def is_waiting_for_approval(self) -> bool:
        """Whether the workflow is paused waiting for founder approval."""
        return self.current_stage == WorkflowStage.WAITING_FOR_FOUNDER_APPROVAL

    @property
    def transition_count(self) -> int:
        """Number of stage transitions recorded."""
        return len(self.audit.transitions)
