"""Engineering Coordinator — orchestrates planning and execution for the implement command.

Architecture position:
  implement.py (CLI — thin wiring layer)
       ↓
  EngineeringCoordinator   ← this module
       ├─► EngineeringPlanner  (autonomous mode: produces EngineeringPlan)
       └─► EngineeringWorkflow (execution: consumes EngineeringPlan)

The coordinator is the sole layer that knows about both planning and execution.
It handles two paths:

  Autonomous mode (goal.output_path == ""):
    1. Call EngineeringPlanner.plan(goal) → EngineeringPlanResult
    2. On planner failure: return WorkflowExecutionReport(success=False)
    3. On ambiguous confidence: return WorkflowExecutionReport(success=False,
       error="ambiguous_plan: <candidates>")
    4. Call EngineeringWorkflow.execute(plan, goal)

  Deterministic mode (goal.output_path != ""):
    1. Construct EngineeringPlan with a single PlannedOperation from goal.output_path
       and goal.description. Intent is derived from filesystem state — the coordinator
       IS the planner in this case and must express intent explicitly in the plan.
    2. Call EngineeringWorkflow.execute(plan, goal)

The coordinator never calls adapters directly. It never bypasses the gateway.
It never raises exceptions — all failures are captured in WorkflowExecutionReport.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path as _Path

from hermes.kernel.engineering_planner import EngineeringPlanner
from hermes.models.engineering_plan import EngineeringPlan, PlannedOperation
from hermes.models.engineering_workflow import FounderGoal, WorkflowExecutionReport
from hermes.models.repository_intelligence import RepositorySnapshot

logger = logging.getLogger(__name__)


class EngineeringCoordinator:
    """Orchestrates planning and execution for the implement command.

    Receives a FounderGoal, determines whether to use autonomous or
    deterministic mode, and drives EngineeringPlanner + EngineeringWorkflow
    to produce a WorkflowExecutionReport.

    Never raises. All failures are captured in WorkflowExecutionReport(success=False).
    """

    def __init__(
        self,
        planner: EngineeringPlanner,
        workflow: object,  # EngineeringWorkflow — typed loosely to avoid circular import
    ) -> None:
        self._planner = planner
        self._workflow = workflow

    def execute(self, goal: FounderGoal, snapshot: RepositorySnapshot) -> WorkflowExecutionReport:
        """Execute the engineering workflow for the given goal.

        Autonomous mode (goal.output_path == ""):
          1. Call planner.plan(goal, snapshot) to decompose into an EngineeringPlan.
          2. On planner failure: return failure report with failure_stage="planning".
          3. On ambiguous confidence: return failure report with error starting
             "ambiguous_plan:".
          4. Call workflow.execute(plan, goal).

        Deterministic mode (goal.output_path != ""):
          1. Build a single-operation EngineeringPlan from goal.output_path.
             Intent is determined from filesystem state (coordinator is the planner).
          2. Call workflow.execute(plan, goal).

        Args:
            goal: The FounderGoal to execute.
            snapshot: The RepositorySnapshot that informs planning in autonomous mode.

        Returns:
            WorkflowExecutionReport capturing the full execution outcome.
        """
        report_id = f"report-{goal.goal_id}"
        mission_id = f"mission-{goal.goal_id}"
        job_id = f"job-{goal.goal_id}"

        # ── Autonomous mode ───────────────────────────────────────────────────
        if not goal.output_path:
            logger.info(
                "EngineeringCoordinator: autonomous mode for goal_id=%r", goal.goal_id
            )
            plan_result = self._planner.plan(goal, snapshot)

            if not plan_result.success:
                logger.warning(
                    "EngineeringCoordinator: planning failed for goal_id=%r: %s",
                    goal.goal_id, plan_result.error,
                )
                return WorkflowExecutionReport(
                    report_id=report_id,
                    goal_id=goal.goal_id,
                    mission_id=mission_id,
                    job_id=job_id,
                    steps=(),
                    success=False,
                    error=plan_result.error,
                    execution_sequence=(),
                    metadata=tuple(sorted({
                        "failure_stage": "planning",
                        "goal_id": goal.goal_id,
                    }.items())),
                )

            plan = plan_result.plan
            assert plan is not None  # success=True guarantees plan is populated

            if plan.confidence == "ambiguous":
                candidates_str = ", ".join(plan.candidates)
                logger.info(
                    "EngineeringCoordinator: ambiguous plan for goal_id=%r candidates=%r",
                    goal.goal_id, plan.candidates,
                )
                return WorkflowExecutionReport(
                    report_id=report_id,
                    goal_id=goal.goal_id,
                    mission_id=mission_id,
                    job_id=job_id,
                    steps=(),
                    success=False,
                    error=f"ambiguous_plan: {candidates_str}",
                    execution_sequence=(),
                    metadata=tuple(sorted({
                        "failure_stage": "planning",
                        "goal_id": goal.goal_id,
                        "candidates": candidates_str,
                    }.items())),
                )

            logger.info(
                "EngineeringCoordinator: autonomous plan ready for goal_id=%r ops=%d",
                goal.goal_id, len(plan.operations),
            )
            workflow_report = self._workflow.execute(plan, goal)
            # Merge planning provenance into the report metadata (I-9: workflow never plans;
            # coordinator adds planning metadata after workflow returns).
            pm = plan_result.planning_metadata
            extra: dict[str, str] = {
                "planning_context": pm.planning_context,
                "planning_snapshot_entries": pm.planning_snapshot_entries,
                "planning_snapshot_id": pm.planning_snapshot_id,
            }
            merged_metadata = tuple(sorted({**dict(workflow_report.metadata), **extra}.items()))
            return dataclasses.replace(workflow_report, metadata=merged_metadata)

        # ── Deterministic mode ────────────────────────────────────────────────
        logger.info(
            "EngineeringCoordinator: deterministic mode for goal_id=%r output_path=%r",
            goal.goal_id, goal.output_path,
        )

        # Derive repo-relative target from workspace-relative output_path
        try:
            rel_target = str(_Path(goal.output_path).relative_to(goal.repository_path))
        except ValueError:
            rel_target = goal.output_path

        # Determine intent from filesystem state (coordinator is the planner here)
        abs_target = _Path(goal.workspace_path) / goal.output_path
        intent = "modify" if abs_target.exists() else "create"

        single_op = PlannedOperation(
            operation_id="op-0",
            target=rel_target,
            intent=intent,
            goal=goal.description,
            depends_on=(),
        )
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}-deterministic",
            goal_id=goal.goal_id,
            confidence="high",
            basis="explicit output path provided by caller",
            operations=(single_op,),
            candidates=(),
        )

        return self._workflow.execute(plan, goal)  # type: ignore[union-attr]


__all__ = ["EngineeringCoordinator"]
