"""Conductor — typed orchestration contracts for the Conductor kernel service.

Sprint 58: The Conductor is the top-level orchestrator that transforms a
Founder Goal into a complete Execution Plan by coordinating all deterministic
engines in architectural order.

Architecture position:
  The Conductor is an ORCHESTRATOR, not an implementation layer.
  It delegates every specialised responsibility to exactly one authoritative
  engine. No business logic is duplicated here.

  Delegation map:
    Context resolution  → ContextManager
    Prompt rendering    → PromptCompression
    Model selection     → ModelRouter
    Workflow state      → WorkflowEngine
    Job planning        → JobEngine
    Operation planning  → OperationEngine
    Swarm planning      → SwarmEngine

  The Conductor itself owns:
    - Orchestration sequence (pipeline order)
    - Conditional stage activation (job/operation/swarm gates)
    - Swarm formation rule application (multi-skill cross-dep detection)
    - Audit metadata assembly
    - Request-level structural validation

All contracts are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from hermes.models.context_package import ContextPackage
from hermes.models.founder_workflow import ApprovalStatus, FounderWorkflow
from hermes.models.job import JobDefinition, JobResult
from hermes.models.operation import OperationDefinition, OperationResult
from hermes.models.prompt_package import PromptPackage
from hermes.models.routing_decision import RoutingDecision, RoutingPolicy
from hermes.models.swarm import SwarmDefinition, SwarmResult


# ── ConductorStage ─────────────────────────────────────────────────────────────


class ConductorStage(enum.Enum):
    """Deterministic lifecycle stage of a Conductor orchestration run.

    Stages are traversed in pipeline order. Each stage maps to exactly one
    engine delegation. COMPLETE and FAILED are terminal stages.

    Lifecycle:
      CONTEXT           → ContextManager.assemble() delegation
      PROMPT            → PromptCompression.compress() delegation
      ROUTING           → ModelRouter.route() delegation
      WORKFLOW          → WorkflowEngine.build() delegation
      JOB_PLANNING      → JobEngine.plan() delegation (conditional)
      OPERATION_PLANNING → OperationEngine.plan_all() delegation (conditional)
      SWARM_PLANNING    → SwarmEngine.plan() delegation (conditional)
      COMPLETE          → all required stages finished successfully
      FAILED            → orchestration halted due to validation failure
    """

    CONTEXT = "context"
    PROMPT = "prompt"
    ROUTING = "routing"
    WORKFLOW = "workflow"
    JOB_PLANNING = "job_planning"
    OPERATION_PLANNING = "operation_planning"
    SWARM_PLANNING = "swarm_planning"
    COMPLETE = "complete"
    FAILED = "failed"


# ── ConductorDecision ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConductorDecision:
    """A single deterministic orchestration decision record.

    Captured at each stage as the Conductor delegates to an engine.
    outcome is a human-readable summary of the delegation result.

    Immutable after construction.
    """

    stage: ConductorStage
    outcome: str


# ── ConductorAudit ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConductorAudit:
    """Immutable audit trail for a single Conductor orchestration run.

    stages_completed → all stages that completed successfully, in order
    decisions        → one ConductorDecision per completed stage, in order
    swarm_required   → True if the Swarm Engine was activated
    job_count        → number of jobs planned (0 or 1 in current design)
    operation_count  → number of operations submitted for planning

    Immutable after construction.
    """

    stages_completed: tuple[ConductorStage, ...]
    decisions: tuple[ConductorDecision, ...]
    swarm_required: bool
    job_count: int
    operation_count: int


# ── ConductorValidationResult ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConductorValidationResult:
    """Outcome of orchestration-level validation of a ConductorRequest.

    This captures ORCHESTRATION-LEVEL validation only:
      - required request fields (request_id, goal, workspace_id)
      - pipeline preconditions (budget, max_fallbacks)
      - orchestration completeness (enough information to start the pipeline)

    The Conductor does NOT validate:
      - workflow correctness  → WorkflowEngine.validate() is the authority
      - job structure         → JobEngine.validate() is the authority
      - operation structure   → OperationEngine.validate() is the authority
      - swarm structure       → SwarmEngine.validate() is the authority

    Each engine validates its own contracts and reports their own errors
    within their respective result types.

    valid=True  → all orchestration preconditions met; errors is empty
    valid=False → one or more precondition failures; errors is non-empty

    Validation errors cause orchestration to halt immediately before any
    engine is invoked.

    Immutable after construction.
    """

    valid: bool
    errors: tuple[str, ...]


# ── ConductorRequest ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConductorRequest:
    """Immutable orchestration request carrying a Founder Goal and all planning
    inputs required by the Conductor pipeline.

    required fields:
      request_id   → unique identifier for this orchestration run
      goal         → the Founder Goal (human-readable intent string)
      workspace_id → target workspace for context assembly

    optional planning inputs (conditional stage activation):
      job_definition  → activates JOB_PLANNING stage
      operations      → activates OPERATION_PLANNING stage
      swarm_definition → explicitly provides a SwarmDefinition (overrides
                         auto-detection); SWARM_PLANNING also activates when
                         the Conductor detects a multi-skill cross-dep topology

    Field ordering: required fields first, then optional with defaults.

    Immutable after construction.
    """

    # ── Required ──────────────────────────────────────────────────────────
    request_id: str
    goal: str
    workspace_id: str

    # ── Routing ───────────────────────────────────────────────────────────
    routing_policy: RoutingPolicy = RoutingPolicy.BALANCED
    max_fallbacks: int = 3

    # ── Prompt budget ──────────────────────────────────────────────────────
    budget: str = "8k"

    # ── Workflow ───────────────────────────────────────────────────────────
    workflow_id: str = ""
    approval_status: ApprovalStatus = ApprovalStatus.PENDING

    # ── Job planning (conditional) ────────────────────────────────────────
    job_definition: JobDefinition | None = None
    completed_job_ids: frozenset[str] = field(default_factory=frozenset)

    # ── Operation planning (conditional) ──────────────────────────────────
    operations: tuple[OperationDefinition, ...] = ()
    completed_op_ids: frozenset[str] = field(default_factory=frozenset)

    # ── Swarm planning (conditional; None → auto-detect) ──────────────────
    swarm_definition: SwarmDefinition | None = None


# ── ConductorResult ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ConductorResult:
    """Immutable output of a Conductor orchestration run.

    Contains all intermediate engine outputs assembled into a single
    coherent result. This IS the Execution Plan — every engine's output
    is retained for downstream use by the Execution Gateway.

    success=True   → all required stages completed; execution_plan may proceed
    success=False  → orchestration halted at final_stage=FAILED

    None fields indicate that the corresponding stage was not reached or
    not activated (conditional stages: job_result, operation_results,
    swarm_result).

    Immutable after construction.
    """

    request_id: str
    success: bool
    final_stage: ConductorStage

    # ── Per-stage engine outputs ───────────────────────────────────────────
    context_package: ContextPackage | None
    prompt_package: PromptPackage | None
    routing_decision: RoutingDecision | None
    workflow: FounderWorkflow | None

    # ── Conditional engine outputs ────────────────────────────────────────
    job_result: JobResult | None
    operation_results: tuple[OperationResult, ...]
    swarm_result: SwarmResult | None

    # ── Orchestration metadata ────────────────────────────────────────────
    audit: ConductorAudit
    validation_result: ConductorValidationResult


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "ConductorAudit",
    "ConductorDecision",
    "ConductorRequest",
    "ConductorResult",
    "ConductorStage",
    "ConductorValidationResult",
]
