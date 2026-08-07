"""Engineering Workflow — plan coordinator for autonomous engineering execution.

Architecture position:
  EngineeringCoordinator
       ↓
  EngineeringWorkflow   ← plan coordinator (this module)
       ↓ delegates each PlannedOperation
  CorrectionEngine      ← single-operation executor with correction loop
       ↓
  Execution Gateway → LLM Adapter → Filesystem Adapter → Git Adapter

Phase 7: EngineeringWorkflow is now a pure plan coordinator. It retains
ownership of:
  1. Bulk RepositoryManipulation validation (atomic, before any LLM token).
  2. Topological sort of PlannedOperations via OperationEngine.
  3. Per-operation delegation to CorrectionEngine.execute_operation().
  4. Single plan-level git commit after all operations complete.
  5. Building WorkflowExecutionReport from accumulated OperationCorrectionResults.

CorrectionEngine owns:
  - Pipeline construction (create mode: generate→write→validate→run_tests→add;
    modify mode: read_file→generate→write→validate→run_tests→add)
  - Step execution through the Gateway
  - Correction loop: on validate/run_tests failure, feeds error back to LLM
    and retries up to WorkflowConfig.max_corrections times.
  - Returns OperationCorrectionResult per operation (complete audit trail +
    correction metadata).

RepositoryManipulation invariant:
  Before any LLM token is consumed, ALL planned operations are validated
  atomically via RepositoryManipulation.plan(). If any conflict is detected,
  the workflow terminates immediately with success=False.

Gateway invariant (enforced by CorrectionEngine per step):
  gateway.dispatch() is called for every ExecutionRequest before any adapter
  is invoked. EngineeringWorkflow only invokes the gateway directly for the
  plan-level commit step.

Architecture invariants preserved:
  - The workflow never plans or routes — it receives an already-formed plan.
  - The workflow never calls adapters directly except for the plan-level commit.
  - The workflow never raises exceptions — all failures captured in report.
  - propose_target, RepositorySelectionResult, SelectionExecutionResult are
    preserved in the codebase (not removed) but not called here.
"""

from __future__ import annotations

import logging
from pathlib import Path as _Path

from hermes.adapters.git_adapter import GitAdapter
from hermes.kernel.correction_engine import CorrectionEngine
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.repository_manipulation import RepositoryManipulation
from hermes.models.engineering_plan import EngineeringPlan
from hermes.models.engineering_workflow import (
    FounderGoal,
    StepExecutionRecord,
    WorkflowConfig,
    WorkflowExecutionReport,
    WorkflowMission,
)
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionStatus
from hermes.models.operation import (
    OperationDependency,
    OperationExecutionReference,
    OperationType,
)
from hermes.models.repository_manipulation import (
    RepositoryOperation,
    RepositoryOperationKind,
)

logger = logging.getLogger(__name__)


class EngineeringWorkflow:
    """Coordinates execution of a pre-formed EngineeringPlan.

    Receives an EngineeringPlan from EngineeringCoordinator and:
      1. Validates all operations atomically via RepositoryManipulation.
      2. Determines execution order via OperationEngine topological sort.
      3. Delegates each PlannedOperation to CorrectionEngine.execute_operation().
      4. Issues a single plan-level git commit after all operations complete.
      5. Returns a WorkflowExecutionReport.

    Never plans, never builds per-operation pipelines, never calls LLM/FS/Git
    adapters directly (except for the plan-level commit).

    Construction::

        workflow = EngineeringWorkflow(
            gateway=gateway,
            git_adapter=GitAdapter(workspace_root=...),
            operation_engine=OperationEngine(),
            config=WorkflowConfig(...),
            correction_engine=CorrectionEngine(...),
        )

        report = workflow.execute(plan, goal)
    """

    def __init__(
        self,
        gateway: ExecutionGateway,
        git_adapter: GitAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
        correction_engine: CorrectionEngine,
    ) -> None:
        """Initialise the workflow with all required components.

        Args:
            gateway:           The Execution Gateway — sole dispatch boundary.
            git_adapter:       The Git Adapter — plan-level commit only.
            operation_engine:  The Operation Engine — topological sort.
            config:            Workflow runtime configuration.
            correction_engine: The Correction Engine — per-operation executor.
        """
        self._gateway = gateway
        self._git_adapter = git_adapter
        self._operation_engine = operation_engine
        self._config = config
        self._correction_engine = correction_engine

    # ── Plan-level commit ──────────────────────────────────────────────────────

    def _execute_commit(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> StepExecutionRecord:
        """Execute the single plan-level git commit through the Gateway."""
        op = self._operation_engine.build_operation(
            id=f"op-commit-plan-{goal.goal_id}",
            job_id=job_id,
            goal="Commit all staged changes for the engineering plan",
            operation_type=OperationType.GIT,
            sequence_index=0,
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="commit",
            ),
        )
        step_id = f"step-{op.id}"

        payload = {
            "repository_path": goal.repository_path,
            "message": self._config.commit_message,
        }
        request = self._gateway.build_request(
            request_id=f"req-{op.id}",
            operation_id=op.id,
            adapter_type=ExecutionAdapter.GIT,
            action_id="commit",
            payload=payload,
        )
        dispatch_result = self._gateway.dispatch(request)

        if dispatch_result.status != ExecutionStatus.DISPATCHED:
            return StepExecutionRecord(
                step_id=step_id,
                operation_id=op.id,
                adapter_type=ExecutionAdapter.GIT,
                action_id="commit",
                execution_request=request,
                dispatch_status=dispatch_result.status,
                adapter_success=False,
                adapter_error=(
                    f"gateway_{dispatch_result.status.value}: {dispatch_result.error}"
                ),
                output="",
            )

        try:
            adapter_result = self._git_adapter.execute(request)
        except Exception as exc:
            error_msg = f"unexpected_adapter_error: {type(exc).__name__}: {exc}"
            return StepExecutionRecord(
                step_id=step_id,
                operation_id=op.id,
                adapter_type=ExecutionAdapter.GIT,
                action_id="commit",
                execution_request=request,
                dispatch_status=dispatch_result.status,
                adapter_success=False,
                adapter_error=error_msg,
                output="",
            )

        return StepExecutionRecord(
            step_id=step_id,
            operation_id=op.id,
            adapter_type=ExecutionAdapter.GIT,
            action_id="commit",
            execution_request=request,
            dispatch_status=dispatch_result.status,
            adapter_success=adapter_result.success,
            adapter_error=adapter_result.error,
            output="",
        )

    # ── Main execution ─────────────────────────────────────────────────────────

    def execute(self, plan: EngineeringPlan, goal: FounderGoal) -> WorkflowExecutionReport:
        """Execute a pre-formed EngineeringPlan for the given FounderGoal.

        Steps:
          1. Bulk RepositoryManipulation validation (atomic — before any LLM call).
          2. Topological sort of plan operations via OperationEngine.
          3. Delegate each PlannedOperation to CorrectionEngine.execute_operation().
          4. Single plan-level commit after all operations complete.
          5. Return WorkflowExecutionReport.

        The workflow never raises exceptions. All failures are captured in
        WorkflowExecutionReport(success=False, error=...).

        Args:
            plan: The pre-formed EngineeringPlan from EngineeringCoordinator.
            goal: The original FounderGoal (provides workspace context).

        Returns:
            WorkflowExecutionReport capturing the complete execution audit trail.
        """
        gid = goal.goal_id
        mission_id = f"mission-{gid}"
        job_id = f"job-{gid}"
        report_id = f"report-{gid}"

        logger.info(
            "EngineeringWorkflow: starting execution goal_id=%r plan_id=%r operations=%d",
            gid, plan.plan_id, len(plan.operations),
        )

        WorkflowMission(
            mission_id=mission_id,
            goal_id=gid,
            objective=goal.description,
        )

        all_steps: list[StepExecutionRecord] = []
        completed_count = 0
        total_correction_attempts = 0

        # ── 1. Bulk RepositoryManipulation validation ──────────────────────────
        repo_ops: list[RepositoryOperation] = []
        for op in plan.operations:
            kind = (
                RepositoryOperationKind.MODIFY_FILE
                if op.intent == "modify"
                else RepositoryOperationKind.CREATE_FILE
            )
            repo_ops.append(RepositoryOperation(
                operation_id=op.operation_id,
                kind=kind,
                path=op.target,
            ))

        try:
            rm_engine = RepositoryManipulation(workspace_root=goal.workspace_path)
            rm_plan = rm_engine.plan(goal.repository_path, repo_ops)
        except Exception as exc:
            error_msg = f"manipulation_plan_error: {type(exc).__name__}: {exc}"
            logger.error(
                "EngineeringWorkflow: RepositoryManipulation raised goal_id=%r: %s",
                gid, error_msg,
            )
            return WorkflowExecutionReport(
                report_id=report_id,
                goal_id=gid,
                mission_id=mission_id,
                job_id=job_id,
                steps=(),
                success=False,
                error=error_msg,
                execution_sequence=(),
                metadata=tuple(sorted({
                    "correction_attempts": "0",
                    "failure_stage": "plan_validation",
                    "goal_id": gid,
                    "operations_completed": "0",
                }.items())),
            )

        if not rm_plan.valid:
            detail = "; ".join(c.detail for c in rm_plan.conflicts)
            error_msg = f"plan_validation_conflict: {detail}"
            logger.warning(
                "EngineeringWorkflow: plan validation conflict goal_id=%r: %s",
                gid, detail,
            )
            return WorkflowExecutionReport(
                report_id=report_id,
                goal_id=gid,
                mission_id=mission_id,
                job_id=job_id,
                steps=(),
                success=False,
                error=error_msg,
                execution_sequence=(),
                metadata=tuple(sorted({
                    "correction_attempts": "0",
                    "failure_stage": "plan_validation",
                    "goal_id": gid,
                    "operations_completed": "0",
                }.items())),
            )

        # ── 2. Topological sort ────────────────────────────────────────────────
        op_defs = [
            self._operation_engine.build_operation(
                id=op.operation_id,
                job_id=job_id,
                goal=op.goal,
                sequence_index=i,
                depends_on=[OperationDependency(operation_id=d) for d in op.depends_on],
            )
            for i, op in enumerate(plan.operations)
        ]
        execution_order = self._operation_engine.determine_execution_order(op_defs)
        op_by_id = {op.operation_id: op for op in plan.operations}

        # ── 3. Per-operation delegation to CorrectionEngine ───────────────────
        for op_id in execution_order:
            planned_op = op_by_id[op_id]

            op_result = self._correction_engine.execute_operation(planned_op, goal)

            all_steps.extend(op_result.steps)
            total_correction_attempts += op_result.correction_attempts

            if not op_result.success:
                logger.warning(
                    "EngineeringWorkflow: operation failed op_id=%r error=%r "
                    "correction_attempts=%d",
                    op_id, op_result.error, op_result.correction_attempts,
                )
                return WorkflowExecutionReport(
                    report_id=report_id,
                    goal_id=gid,
                    mission_id=mission_id,
                    job_id=job_id,
                    steps=tuple(all_steps),
                    success=False,
                    error=op_result.error,
                    execution_sequence=tuple(s.adapter_type.value for s in all_steps),
                    metadata=tuple(sorted({
                        "correction_attempts": str(total_correction_attempts),
                        "failure_stage": op_result.error or "operation_failed",
                        "goal_id": gid,
                        "operations_completed": str(completed_count),
                    }.items())),
                )

            completed_count += 1
            logger.info(
                "EngineeringWorkflow: operation complete op_id=%r correction_attempts=%d "
                "(%d/%d total)",
                op_id, op_result.correction_attempts,
                completed_count, len(plan.operations),
            )

        # ── 4. Single plan-level commit ────────────────────────────────────────
        commit_step = self._execute_commit(goal, job_id)
        all_steps.append(commit_step)

        if not commit_step.adapter_success:
            logger.warning(
                "EngineeringWorkflow: plan-level commit failed goal_id=%r: %r",
                gid, commit_step.adapter_error,
            )
            return WorkflowExecutionReport(
                report_id=report_id,
                goal_id=gid,
                mission_id=mission_id,
                job_id=job_id,
                steps=tuple(all_steps),
                success=False,
                error=commit_step.adapter_error,
                execution_sequence=tuple(s.adapter_type.value for s in all_steps),
                metadata=tuple(sorted({
                    "correction_attempts": str(total_correction_attempts),
                    "failure_stage": "commit",
                    "goal_id": gid,
                    "operations_completed": str(completed_count),
                }.items())),
            )

        # ── 5. Success ─────────────────────────────────────────────────────────
        logger.info(
            "EngineeringWorkflow: all operations complete goal_id=%r "
            "operations_completed=%d total_steps=%d total_correction_attempts=%d",
            gid, completed_count, len(all_steps), total_correction_attempts,
        )

        return WorkflowExecutionReport(
            report_id=report_id,
            goal_id=gid,
            mission_id=mission_id,
            job_id=job_id,
            steps=tuple(all_steps),
            success=True,
            error=None,
            execution_sequence=tuple(s.adapter_type.value for s in all_steps),
            metadata=tuple(sorted({
                "commit_message": self._config.commit_message,
                "correction_attempts": str(total_correction_attempts),
                "goal_id": gid,
                "operations_completed": str(completed_count),
                "planned_operations": str(len(plan.operations)),
                "repository": goal.repository_path,
            }.items())),
        )
