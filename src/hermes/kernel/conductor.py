"""Conductor — deterministic orchestration of the Hermes engine pipeline.

Sprint 58: The Conductor is the sole top-level orchestrator. It accepts a
Founder Goal, coordinates all deterministic engines in architectural order,
applies conditional stage logic, assembles intermediate outputs, and returns
an immutable ConductorResult.

Architecture invariants:
  - The Conductor is an ORCHESTRATOR. It never implements logic owned by
    an existing engine.
  - Every delegation maps to exactly one authoritative engine (see matrix).
  - The Conductor never executes operations.
  - The Conductor never calls providers.
  - The Conductor never calls the Runtime.
  - The Conductor never calls the Execution Gateway.
  - The Conductor never performs filesystem work.
  - The Conductor never performs network operations.
  - The Conductor is provider-independent.
  - The Conductor is deterministic: same inputs → same outputs.

Delegation matrix (invariant — must never be violated):
  Context resolution  → ContextManager.assemble()
  Prompt rendering    → PromptCompression.compress()
  Model selection     → ModelRouter.route()
  Workflow state      → WorkflowEngine.build()
  Job planning        → JobEngine.plan()
  Operation planning  → OperationEngine.plan_all()
  Swarm planning      → SwarmEngine.plan() + SwarmEngine.build_swarm()

Conductor-owned responsibilities (orchestration only):
  - Pipeline stage sequence
  - Conditional stage gates (job/operation/swarm activation)
  - Swarm formation rule: multi-skill + cross-skill-dep topology detection
  - Audit metadata assembly
  - Request structural validation

Swarm formation rule (from architecture):
  Single-skill job             → no swarm
  Multi-skill, independent ops → no swarm
  Multi-skill, cross-skill deps → swarm required
"""

from __future__ import annotations

import logging

from hermes.kernel.context_manager import ContextManager
from hermes.kernel.job_engine import JobEngine
from hermes.kernel.model_router import ModelRouter
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.prompt_compression import PromptCompression
from hermes.kernel.swarm_engine import SwarmEngine
from hermes.kernel.workflow_engine import WorkflowEngine
from hermes.models.conductor import (
    ConductorAudit,
    ConductorDecision,
    ConductorRequest,
    ConductorResult,
    ConductorStage,
    ConductorValidationResult,
)
from hermes.models.knowledge_document import KnowledgeDocument
from hermes.models.operation import OperationDefinition, OperationResult
from hermes.models.swarm import SwarmDependency, SwarmMember

logger = logging.getLogger(__name__)


class Conductor:
    """Deterministic orchestration of the Hermes engine pipeline.

    Stateless with respect to orchestration runs: every call to orchestrate()
    is independent. Engine instances are injected at construction and held
    as read-only references.

    All seven engine dependencies are required. The Conductor composes them;
    it never re-implements their logic.

    Usage::

        conductor = Conductor(
            context_manager=context_manager,
            prompt_compression=prompt_compression,
            model_router=model_router,
            workflow_engine=workflow_engine,
            job_engine=job_engine,
            operation_engine=operation_engine,
            swarm_engine=swarm_engine,
        )

        request = ConductorRequest(
            request_id="run-001",
            goal="Draft the Q3 product announcement",
            workspace_id="workspace-avanzia",
        )

        result = conductor.orchestrate(request)
        # result.success → True if all stages completed
        # result.workflow.current_stage → terminal workflow stage
    """

    def __init__(
        self,
        *,
        context_manager: ContextManager,
        prompt_compression: PromptCompression,
        model_router: ModelRouter,
        workflow_engine: WorkflowEngine,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        swarm_engine: SwarmEngine,
    ) -> None:
        self._context_manager = context_manager
        self._prompt_compression = prompt_compression
        self._model_router = model_router
        self._workflow_engine = workflow_engine
        self._job_engine = job_engine
        self._operation_engine = operation_engine
        self._swarm_engine = swarm_engine

    # ── Orchestration ─────────────────────────────────────────────────────────

    def orchestrate(
        self,
        request: ConductorRequest,
        documents: dict[str, KnowledgeDocument] | None = None,
    ) -> ConductorResult:
        """Run the full deterministic engine pipeline for a Founder Goal.

        Pipeline (in strict architectural order):
          1. Validate request structurally
          2. Context  → ContextManager.assemble()
          3. Prompt   → PromptCompression.compress()
          4. Routing  → ModelRouter.route()
          5. Workflow → WorkflowEngine.build()
          6. Jobs     → JobEngine.plan()         [if job_definition provided]
          7. Ops      → OperationEngine.plan_all() [if operations provided]
          8. Swarm    → SwarmEngine.plan()        [if swarm required]

        Documents (dict[str, KnowledgeDocument]) are passed separately from
        the request because they are runtime infrastructure, not Founder intent.
        The Conductor does not load them — it receives them pre-loaded.

        Args:
            request:   Immutable ConductorRequest carrying the Founder Goal and
                       all planning inputs.
            documents: Map of document_id → KnowledgeDocument with content,
                       used by PromptCompression. Defaults to empty dict.

        Returns:
            ConductorResult with all engine outputs assembled. success=False
            and final_stage=FAILED if request validation fails.
        """
        docs: dict[str, KnowledgeDocument] = documents or {}
        stages_completed: list[ConductorStage] = []
        decisions: list[ConductorDecision] = []

        # ── 1. Request validation ──────────────────────────────────────────
        validation = self.validate_request(request)
        if not validation.valid:
            logger.warning(
                "Conductor request validation failed: request_id=%r errors=%r",
                request.request_id,
                validation.errors,
            )
            return ConductorResult(
                request_id=request.request_id,
                success=False,
                final_stage=ConductorStage.FAILED,
                context_package=None,
                prompt_package=None,
                routing_decision=None,
                workflow=None,
                job_result=None,
                operation_results=(),
                swarm_result=None,
                audit=ConductorAudit(
                    stages_completed=(),
                    decisions=(),
                    swarm_required=False,
                    job_count=0,
                    operation_count=0,
                ),
                validation_result=validation,
            )

        # ── 2. Context — delegated entirely to ContextManager ─────────────
        context_package = self._context_manager.assemble(
            query=request.goal,
            workspace_id=request.workspace_id,
        )
        stages_completed.append(ConductorStage.CONTEXT)
        decisions.append(ConductorDecision(
            stage=ConductorStage.CONTEXT,
            outcome=(
                f"context_assembled "
                f"knowledge_count={len(context_package.knowledge)} "
                f"capability_count={len(context_package.capabilities)}"
            ),
        ))

        # ── 3. Prompt — delegated entirely to PromptCompression ───────────
        prompt_package = self._prompt_compression.compress(
            package=context_package,
            documents=docs,
            budget=request.budget,
        )
        stages_completed.append(ConductorStage.PROMPT)
        decisions.append(ConductorDecision(
            stage=ConductorStage.PROMPT,
            outcome=(
                f"prompt_built "
                f"section_count={len(prompt_package.sections)} "
                f"chars={prompt_package.estimated_chars}"
            ),
        ))

        # ── 4. Routing — delegated entirely to ModelRouter ────────────────
        routing_decision = self._model_router.route(
            package=prompt_package,
            policy=request.routing_policy,
            max_fallbacks=request.max_fallbacks,
        )
        stages_completed.append(ConductorStage.ROUTING)
        decisions.append(ConductorDecision(
            stage=ConductorStage.ROUTING,
            outcome=(
                f"routing_{'succeeded' if routing_decision.routed else 'failed'}"
            ),
        ))

        # ── 5. Workflow — delegated entirely to WorkflowEngine ────────────
        workflow = self._workflow_engine.build(
            package=prompt_package,
            routing=routing_decision,
            workflow_id=request.workflow_id,
            approval_status=request.approval_status,
        )
        stages_completed.append(ConductorStage.WORKFLOW)
        decisions.append(ConductorDecision(
            stage=ConductorStage.WORKFLOW,
            outcome=(
                f"workflow_advanced "
                f"stage={workflow.current_stage.value} "
                f"action={workflow.next_action.value}"
            ),
        ))

        # ── 6. Job planning — conditional; delegated to JobEngine ─────────
        job_result = None
        if request.job_definition is not None:
            job_result = self._job_engine.plan(
                request.job_definition,
                completed_job_ids=request.completed_job_ids,
            )
            stages_completed.append(ConductorStage.JOB_PLANNING)
            decisions.append(ConductorDecision(
                stage=ConductorStage.JOB_PLANNING,
                outcome=(
                    f"job_planned "
                    f"status={job_result.status.value} "
                    f"resolved={len(job_result.resolved_capabilities)} "
                    f"unresolved={len(job_result.unresolved_capabilities)}"
                ),
            ))

        # ── 7. Operation planning — conditional; delegated to OperationEngine
        operation_results: tuple[OperationResult, ...] = ()
        if request.operations:
            operation_results = self._operation_engine.plan_all(
                list(request.operations),
                completed_op_ids=request.completed_op_ids,
            )
            stages_completed.append(ConductorStage.OPERATION_PLANNING)
            decisions.append(ConductorDecision(
                stage=ConductorStage.OPERATION_PLANNING,
                outcome=f"operations_planned count={len(operation_results)}",
            ))

        # ── 8. Swarm planning — conditional; delegated to SwarmEngine ─────
        #
        # Swarm activation (Conductor-owned formation rule):
        #   The Conductor applies the architectural rule — it does not
        #   duplicate SwarmEngine logic. SwarmEngine plans; Conductor decides
        #   WHEN to plan.
        swarm_result = None
        swarm_required = self._swarm_required(
            request.operations, request.swarm_definition
        )
        if swarm_required:
            swarm_def = request.swarm_definition
            if swarm_def is None:
                # Auto-build via SwarmEngine.build_swarm() — still delegated
                swarm_def = self._build_auto_swarm(request)
            swarm_result = self._swarm_engine.plan(swarm_def)
            stages_completed.append(ConductorStage.SWARM_PLANNING)
            decisions.append(ConductorDecision(
                stage=ConductorStage.SWARM_PLANNING,
                outcome=(
                    f"swarm_planned "
                    f"conflict_detected={swarm_result.conflict_detected} "
                    f"member_count={len(swarm_def.members)}"
                ),
            ))

        # ── 9. Assemble result ─────────────────────────────────────────────
        audit = ConductorAudit(
            stages_completed=tuple(stages_completed),
            decisions=tuple(decisions),
            swarm_required=swarm_required,
            job_count=1 if request.job_definition is not None else 0,
            operation_count=len(request.operations),
        )

        logger.info(
            "Conductor orchestration complete: request_id=%r stages=%d "
            "swarm=%s job=%s operations=%d",
            request.request_id,
            len(stages_completed),
            swarm_required,
            request.job_definition is not None,
            len(request.operations),
        )

        return ConductorResult(
            request_id=request.request_id,
            success=True,
            final_stage=ConductorStage.COMPLETE,
            context_package=context_package,
            prompt_package=prompt_package,
            routing_decision=routing_decision,
            workflow=workflow,
            job_result=job_result,
            operation_results=operation_results,
            swarm_result=swarm_result,
            audit=audit,
            validation_result=validation,
        )

    # ── Request validation ────────────────────────────────────────────────────

    def validate_request(self, request: ConductorRequest) -> ConductorValidationResult:
        """Validate a ConductorRequest at the orchestration level only.

        This is ORCHESTRATION-LEVEL validation. It checks that the Conductor
        has sufficient information to begin the pipeline:

          1. request_id must be non-empty after stripping whitespace
          2. goal must be non-empty after stripping whitespace
          3. workspace_id must be non-empty after stripping whitespace
          4. max_fallbacks must be >= 0
          5. budget must be a non-empty string

        The Conductor does NOT validate:
          - Workflow correctness  → WorkflowEngine is the authority
          - Job structure         → JobEngine.validate() is the authority
          - Operation structure   → OperationEngine.validate() is the authority
          - Swarm structure       → SwarmEngine.validate() is the authority

        Each engine validates its own contracts. This method only checks that
        the orchestration pipeline has the preconditions it needs to proceed.

        Args:
            request: The ConductorRequest to validate.

        Returns:
            ConductorValidationResult with valid=True if all checks pass.
        """
        errors: list[str] = []

        if not request.request_id.strip():
            errors.append("request_id must not be empty")

        if not request.goal.strip():
            errors.append("goal must not be empty")

        if not request.workspace_id.strip():
            errors.append("workspace_id must not be empty")

        if request.max_fallbacks < 0:
            errors.append(
                f"max_fallbacks must be >= 0, got {request.max_fallbacks}"
            )

        if not request.budget.strip():
            errors.append("budget must not be empty")

        return ConductorValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Swarm formation rule ──────────────────────────────────────────────────

    def _swarm_required(
        self,
        ops: tuple[OperationDefinition, ...],
        swarm_definition: object,
    ) -> bool:
        """Determine whether the Swarm Engine should be activated.

        This is the Conductor's formation rule — it reads existing
        OperationDefinition data and applies the architectural rule.
        It does NOT duplicate SwarmEngine logic.

        Formation rules (from architecture):
          1. An explicit SwarmDefinition was provided in the request → True
          2. Single skill or no operations → False
          3. Multiple skills AND at least one cross-skill dependency → True
          4. Multiple skills, all independent → False

        A cross-skill dependency is one where the dependent operation and the
        prerequisite operation have different (non-empty) capability_ids.

        Args:
            ops:              Operations from the request.
            swarm_definition: Explicit SwarmDefinition if provided, else None.

        Returns:
            True if the Swarm Engine should be activated.
        """
        # Rule 1: explicit swarm definition provided
        if swarm_definition is not None:
            return True

        if not ops:
            return False

        # Rule 2: single skill → no swarm
        capabilities = {op.capability_id for op in ops if op.capability_id}
        if len(capabilities) <= 1:
            return False

        # Rules 3/4: multi-skill — check for cross-skill dependencies
        cap_by_op_id: dict[str, str] = {
            op.id: op.capability_id for op in ops if op.capability_id
        }
        for op in ops:
            if not op.capability_id:
                continue
            for dep in op.depends_on:
                dep_cap = cap_by_op_id.get(dep.operation_id, "")
                if dep_cap and dep_cap != op.capability_id:
                    return True

        return False

    # ── Auto-swarm construction ───────────────────────────────────────────────

    def _build_auto_swarm(self, request: ConductorRequest) -> object:
        """Auto-build a SwarmDefinition when cross-skill deps are detected.

        Delegates to SwarmEngine.build_swarm() for deterministic construction
        and normalisation. The Conductor maps operation structure to swarm
        members and dependencies; SwarmEngine sorts and validates.

        Args:
            request: The ConductorRequest containing operations.

        Returns:
            A SwarmDefinition built via SwarmEngine.build_swarm().
        """
        ops = list(request.operations)
        op_id_set = {op.id for op in ops}

        members = [
            SwarmMember(
                operation_id=op.id,
                role=op.capability_id or "",
                sequence_index=op.sequence_index,
            )
            for op in ops
        ]

        # Only include intra-swarm dependencies (both endpoints in op set)
        dependencies = [
            SwarmDependency(
                from_operation_id=dep.operation_id,
                to_operation_id=op.id,
            )
            for op in ops
            for dep in op.depends_on
            if dep.operation_id in op_id_set
        ]

        context_keys = sorted({op.capability_id for op in ops if op.capability_id})

        job_id = (
            request.job_definition.id
            if request.job_definition is not None
            else request.request_id
        )

        return self._swarm_engine.build_swarm(
            id=f"swarm-{request.request_id}",
            job_id=job_id,
            members=members,
            dependencies=dependencies,
            context_keys=context_keys,
        )
