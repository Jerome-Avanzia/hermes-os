"""Engineering Planner — produces EngineeringPlan objects from FounderGoals.

Architecture position:
  EngineeringCoordinator
       ↓
  EngineeringPlanner   ← this module
       ↓
  ExecutionGateway → LlmAdapter (action_id="generate") → raw LLMResponse
       ↓
  [JSON parse + EngineeringPlan construction — this module]

The planner is the sole owner of:
  - Planning prompt construction (system prompt + JSON schema)
  - LLM call dispatch (via gateway and LlmAdapter)
  - JSON interpretation and validation
  - EngineeringPlan construction

The planner does NOT:
  - Validate plan operations against repository state (RepositoryManipulation's role)
  - Execute any file operations (EngineeringWorkflow's role)
  Receives a RepositorySnapshot that informs file target selection in the planning prompt.

LlmAdapter invariant: the planner calls LlmAdapter with action_id="generate" and
receives a raw AdapterExecutionResult. LlmAdapter never interprets the response
content — that is exclusively the planner's responsibility.

Never raises. All failures are captured in EngineeringPlanResult(success=False).
"""

from __future__ import annotations

import json
import logging

from hermes.adapters.llm_adapter import LlmAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.models.engineering_plan import (
    EngineeringPlan,
    EngineeringPlanResult,
    PlannedOperation,
)
from hermes.models.engineering_plan import PlanningMetadata
from hermes.models.engineering_workflow import FounderGoal
from hermes.models.repository_intelligence import RepositorySnapshot
from hermes.models.execution_gateway import ExecutionAdapter
from hermes.models.llm_adapter import AdapterConfiguration
from hermes.models.operation import OperationDependency, OperationExecutionReference, OperationType

logger = logging.getLogger(__name__)

_PLANNING_SYSTEM_PROMPT = (
    "You are a software engineering planner. Decompose the engineering task below "
    "into the minimum ordered set of file operations needed to complete it. "
    "Each operation targets exactly one file.\n\n"
    "Respond ONLY with a valid JSON object — no markdown, no code fences, no explanation.\n\n"
    "JSON schema:\n"
    "{\n"
    '  "confidence": "<high|ambiguous>",\n'
    '  "basis": "<one sentence explaining your decomposition or why it is ambiguous>",\n'
    '  "operations": [\n'
    "    {\n"
    '      "operation_id": "<unique string, e.g. op-0>",\n'
    '      "target": "<repo-relative path to the file>",\n'
    '      "intent": "<create|modify>",\n'
    '      "goal": "<precise description of the change required at this specific file>",\n'
    '      "depends_on": ["<operation_id of prerequisite operation, or empty list>"]\n'
    "    }\n"
    "  ],\n"
    '  "candidates": ["<path if ambiguous>"]\n'
    "}\n\n"
    "Rules:\n"
    '- confidence="high": you are certain about the correct set of file operations\n'
    '- confidence="ambiguous": the task is underspecified or multiple decompositions '
    "are equally plausible\n"
    '- When confidence="high": operations contains all required file changes; candidates=[]\n'
    '- When confidence="ambiguous": operations=[]; candidates contains plausible file '
    "paths (alphabetically sorted)\n"
    '- intent="create": the file does not yet exist and must be created\n'
    '- intent="modify": the file already exists and must be modified\n'
    "- target must be repo-relative (no leading /)\n"
    "- depends_on must always be present; use [] when the operation has no prerequisites\n"
    "- goal must precisely describe what change is required at this specific file "
    "(not the overall task)\n"
    "- Minimize the number of operations — only include files that genuinely require changes"
)


def _build_planning_system_prompt(snapshot: RepositorySnapshot) -> str:
    """Build the planning system prompt enriched with repository structure.

    The base planning prompt is combined with a Repository structure section
    derived from the snapshot. This section contains the complete file listing
    (no cap — full RepositorySnapshot.files is used) to enable accurate target
    selection.

    Full RepositorySnapshot transmission is intentional for AT-8. Snapshot
    compression or summarisation is explicitly out of scope for Phase 8.
    """
    lines: list[str] = [_PLANNING_SYSTEM_PROMPT, "", "Repository structure:"]

    if snapshot.primary_language:
        lines.append(f"Primary language: {snapshot.primary_language}")

    all_files = [f.path for f in snapshot.files if not f.is_directory]
    lines.append(f"Files ({len(all_files)} total):")
    for path in all_files:
        lines.append(f"  {path}")

    if snapshot.test_locations:
        lines.append("Test locations:")
        for tl in snapshot.test_locations:
            lines.append(f"  {tl.path} [{tl.framework}]")

    return "\n".join(lines)


class EngineeringPlanner:
    """Decomposes a FounderGoal into a multi-operation EngineeringPlan.

    The planner is the sole owner of planning prompt construction, LLM call
    dispatch, JSON interpretation, and EngineeringPlan construction.

    Never raises. All failures are captured in EngineeringPlanResult(success=False).
    """

    def __init__(
        self,
        gateway: ExecutionGateway,
        llm_adapter: LlmAdapter,
        llm_config: AdapterConfiguration,
        operation_engine: OperationEngine,
    ) -> None:
        self._gateway = gateway
        self._llm_adapter = llm_adapter
        self._llm_config = llm_config
        self._operation_engine = operation_engine

    def plan(self, goal: FounderGoal, snapshot: RepositorySnapshot) -> EngineeringPlanResult:
        """Decompose goal into an EngineeringPlan via an LLM planning call.

        Args:
            goal: The FounderGoal containing the task description.
            snapshot: The RepositorySnapshot that provides repository structure
                for the planning prompt.

        Returns:
            EngineeringPlanResult with success=True and a populated plan on
            success, or success=False with an error description on any failure.
        """
        plan_op_id = f"plan-op-{goal.goal_id}"
        request_id = f"plan-req-{goal.goal_id}"

        # ── 1. Build and dispatch through gateway ─────────────────────────────
        payload = {
            "system_prompt": _build_planning_system_prompt(snapshot),
            "prompt": goal.description,
        }
        request = self._gateway.build_request(
            request_id=request_id,
            operation_id=plan_op_id,
            adapter_type=ExecutionAdapter.LLM,
            action_id="generate",
            payload=payload,
        )

        dispatch_result = self._gateway.dispatch(request)
        if dispatch_result.status.value != "dispatched":
            error = f"gateway_dispatch_failed: {dispatch_result.error}"
            logger.warning("EngineeringPlanner: dispatch failed for goal_id=%r: %s", goal.goal_id, error)
            return EngineeringPlanResult(success=False, plan=None, error=error)

        # ── 2. Call LLM adapter ───────────────────────────────────────────────
        try:
            adapter_result = self._llm_adapter.execute(request, self._llm_config)
        except Exception as exc:
            error = f"llm_adapter_exception: {type(exc).__name__}: {exc}"
            logger.error("EngineeringPlanner: LLM adapter raised for goal_id=%r: %s", goal.goal_id, error)
            return EngineeringPlanResult(success=False, plan=None, error=error)

        if not adapter_result.success:
            error = adapter_result.error or "llm_adapter_failed: unknown error"
            logger.warning("EngineeringPlanner: LLM adapter failed for goal_id=%r: %s", goal.goal_id, error)
            return EngineeringPlanResult(success=False, plan=None, error=error)

        # ── 3. Extract raw content ────────────────────────────────────────────
        raw = (
            adapter_result.llm_response.content
            if adapter_result.llm_response is not None
            else None
        )
        if not raw:
            error = "llm_empty_response: LLM returned empty content"
            logger.warning("EngineeringPlanner: empty LLM response for goal_id=%r", goal.goal_id)
            return EngineeringPlanResult(success=False, plan=None, error=error)

        # ── 4. Parse JSON ─────────────────────────────────────────────────────
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            error = f"json_parse_error: {exc}"
            logger.warning("EngineeringPlanner: JSON parse failed for goal_id=%r: %s", goal.goal_id, error)
            return EngineeringPlanResult(success=False, plan=None, error=error)

        # ── 5. Validate top-level fields ──────────────────────────────────────
        confidence = data.get("confidence", "")
        if confidence not in ("high", "ambiguous"):
            error = f"invalid_confidence: {confidence!r} must be 'high' or 'ambiguous'"
            return EngineeringPlanResult(success=False, plan=None, error=error)

        basis = data.get("basis", "")
        operations_raw = data.get("operations")
        if not isinstance(operations_raw, list):
            error = "invalid_plan: 'operations' must be a list"
            return EngineeringPlanResult(success=False, plan=None, error=error)

        candidates_raw = data.get("candidates", [])
        if not isinstance(candidates_raw, list):
            candidates_raw = []
        candidates = tuple(str(c) for c in candidates_raw)

        # ── 6. Validate and build PlannedOperations ───────────────────────────
        planned_ops: list[PlannedOperation] = []
        for i, op_data in enumerate(operations_raw):
            if not isinstance(op_data, dict):
                error = f"invalid_operation[{i}]: each operation must be a JSON object"
                return EngineeringPlanResult(success=False, plan=None, error=error)

            op_id = op_data.get("operation_id")
            if not op_id or not str(op_id).strip():
                error = f"invalid_operation[{i}]: missing or empty 'operation_id'"
                return EngineeringPlanResult(success=False, plan=None, error=error)

            target = op_data.get("target")
            if not target or not str(target).strip():
                error = f"invalid_operation[{i}]: missing or empty 'target'"
                return EngineeringPlanResult(success=False, plan=None, error=error)

            intent = op_data.get("intent")
            if intent not in ("create", "modify"):
                error = f"invalid_operation[{i}]: intent {intent!r} must be 'create' or 'modify'"
                return EngineeringPlanResult(success=False, plan=None, error=error)

            op_goal = op_data.get("goal")
            if not op_goal or not str(op_goal).strip():
                error = f"invalid_operation[{i}]: missing or empty 'goal'"
                return EngineeringPlanResult(success=False, plan=None, error=error)

            # depends_on defaults to [] if absent (defensive parse)
            depends_on_raw = op_data.get("depends_on", [])
            if not isinstance(depends_on_raw, list):
                depends_on_raw = []
            depends_on = tuple(str(d) for d in depends_on_raw if d)

            planned_ops.append(PlannedOperation(
                operation_id=str(op_id),
                target=str(target),
                intent=str(intent),
                goal=str(op_goal),
                depends_on=depends_on,
            ))

        # ── 7. Cycle detection ────────────────────────────────────────────────
        if planned_ops:
            op_defs = [
                self._operation_engine.build_operation(
                    id=op.operation_id,
                    job_id="plan",
                    goal=op.goal,
                    sequence_index=i,
                    depends_on=[OperationDependency(operation_id=d) for d in op.depends_on],
                )
                for i, op in enumerate(planned_ops)
            ]
            if self._operation_engine.detect_cycle(op_defs):
                error = "plan_cycle_detected: dependency cycle in planned operations"
                logger.warning("EngineeringPlanner: cycle detected for goal_id=%r", goal.goal_id)
                return EngineeringPlanResult(success=False, plan=None, error=error)

        # ── 8. Build EngineeringPlan ──────────────────────────────────────────
        plan = EngineeringPlan(
            plan_id=f"plan-{goal.goal_id}",
            goal_id=goal.goal_id,
            confidence=confidence,
            basis=basis,
            operations=tuple(planned_ops),
            candidates=candidates,
        )

        planning_meta = PlanningMetadata(
            planning_context="repository_snapshot_v1",
            planning_snapshot_entries=str(snapshot.file_count),
            planning_snapshot_id=snapshot.snapshot_id,
        )

        logger.info(
            "EngineeringPlanner: plan built for goal_id=%r confidence=%r ops=%d",
            goal.goal_id, confidence, len(planned_ops),
        )
        return EngineeringPlanResult(success=True, plan=plan, error=None, planning_metadata=planning_meta)


__all__ = ["EngineeringPlanner"]
