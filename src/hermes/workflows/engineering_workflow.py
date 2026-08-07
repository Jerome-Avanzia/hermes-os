"""Engineering Workflow — Hermes' autonomous engineering execution engine.

Architecture position:
  EngineeringCoordinator
       ↓
  EngineeringWorkflow   ← composition layer (this module)
       ↓
  OperationEngine   (planning — deterministic kernel)
       ↓
  Execution Gateway   (dispatch contracts — sole dispatch boundary)
       ↓
  LLM Adapter → Filesystem Adapter → Git Adapter   (execution — outside kernel)

Phase 6: This module receives a pre-formed EngineeringPlan (from
EngineeringCoordinator) and executes each PlannedOperation through the existing
single-file pipeline. The workflow never calls the planner — it only executes.

Per-operation pipeline (no per-op commit):
  Create mode: generate → create_file → validate → run_tests → add
  Modify mode: read_file → generate → modify_file → validate → run_tests → add

After all operations complete, a single plan-level commit is made.

RepositoryManipulation invariant:
  Before any LLM token is consumed, ALL planned operations are validated
  atomically via RepositoryManipulation.plan(). If any conflict is detected,
  the workflow terminates immediately with success=False.

Gateway invariant (enforced per step):
  gateway.dispatch() is called for every ExecutionRequest before any adapter
  is invoked. If the Gateway does not return DISPATCHED, the adapter is
  never called and the workflow halts with success=False.

Architecture invariants preserved:
  - The workflow never plans or routes — it receives an already-formed plan.
  - The workflow never calls adapters directly — only after DISPATCHED.
  - The workflow never raises exceptions — all failures captured in report.
  - propose_target, RepositorySelectionResult, SelectionExecutionResult are
    preserved in the codebase (not removed) but not called here.
"""

from __future__ import annotations

import logging
from pathlib import Path as _Path

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
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
from hermes.models.llm_adapter import AdapterConfiguration
from hermes.models.operation import (
    OperationDefinition,
    OperationDependency,
    OperationExecutionReference,
    OperationType,
)
from hermes.models.repository_manipulation import (
    RepositoryOperation,
    RepositoryOperationKind,
)

logger = logging.getLogger(__name__)

# ── Adapter ID → ExecutionAdapter mapping ──────────────────────────────────────

_ADAPTER_ID_TO_TYPE: dict[str, ExecutionAdapter] = {
    op.value: op
    for op in [
        ExecutionAdapter.LLM,
        ExecutionAdapter.FILESYSTEM,
        ExecutionAdapter.GIT,
        ExecutionAdapter.VALIDATION,
    ]
}


class EngineeringWorkflow:
    """Executes a pre-formed EngineeringPlan through the single-file pipeline.

    Receives an EngineeringPlan from EngineeringCoordinator and executes each
    PlannedOperation through the existing pipeline. Never plans — only executes.

    Construction::

        workflow = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=LlmAdapter(),
            filesystem_adapter=FilesystemAdapter(workspace_root="/tmp/workspace"),
            git_adapter=GitAdapter(workspace_root="/tmp/workspace"),
            validation_adapter=ValidationAdapter(workspace_root="/tmp/workspace"),
            operation_engine=OperationEngine(),
            config=WorkflowConfig(...),
        )

        report = workflow.execute(plan, goal)
    """

    def __init__(
        self,
        gateway: ExecutionGateway,
        llm_adapter: LlmAdapter,
        filesystem_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        validation_adapter: ValidationAdapter,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
    ) -> None:
        """Initialise the workflow with all required components.

        Args:
            gateway:              The Execution Gateway — sole dispatch boundary.
            llm_adapter:          The LLM Adapter — code generation.
            filesystem_adapter:   The Filesystem Adapter — file writing.
            git_adapter:          The Git Adapter — staging and committing.
            validation_adapter:   The Validation Adapter — pre-commit syntax gate.
            operation_engine:     The Operation Engine — operation planning.
            config:               Workflow runtime configuration.
        """
        self._gateway = gateway
        self._llm_adapter = llm_adapter
        self._filesystem_adapter = filesystem_adapter
        self._git_adapter = git_adapter
        self._validation_adapter = validation_adapter
        self._operation_engine = operation_engine
        self._config = config

    # ── Per-operation pipeline builders ───────────────────────────────────────

    def _build_create_operations(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the five-step create-mode per-operation pipeline (no commit).

        Step 1 — generate (LLM):        adapter_id="llm",        action_id="generate"
        Step 2 — create_file (FS):       adapter_id="filesystem", action_id="create_file"
        Step 3 — validate (Validation):  adapter_id="validation", action_id="validate"
        Step 4 — run_tests (Validation): adapter_id="validation", action_id="run_tests"
        Step 5 — add (Git):              adapter_id="git",        action_id="add"
        """
        gid = goal.goal_id
        op_generate_id = f"op-generate-{gid}"
        op_write_id = f"op-write-{gid}"
        op_validate_id = f"op-validate-{gid}"
        op_test_id = f"op-test-{gid}"
        op_add_id = f"op-add-{gid}"

        engine = self._operation_engine

        op_generate = engine.build_operation(
            id=op_generate_id,
            job_id=job_id,
            goal=f"Generate source code for: {goal.description}",
            operation_type=OperationType.LLM,
            sequence_index=0,
            execution_ref=OperationExecutionReference(
                adapter_id="llm",
                action_id="generate",
            ),
        )

        op_write = engine.build_operation(
            id=op_write_id,
            job_id=job_id,
            goal=f"Write generated code to {goal.output_path}",
            operation_type=OperationType.FILESYSTEM,
            sequence_index=1,
            depends_on=[OperationDependency(operation_id=op_generate_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="filesystem",
                action_id="create_file",
            ),
        )

        op_validate = engine.build_operation(
            id=op_validate_id,
            job_id=job_id,
            goal=f"Validate generated code at {goal.output_path}",
            operation_type=OperationType.VALIDATION,
            sequence_index=2,
            depends_on=[OperationDependency(operation_id=op_write_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="validation",
                action_id="validate",
            ),
        )

        op_test = engine.build_operation(
            id=op_test_id,
            job_id=job_id,
            goal=f"Run test suite for repository {goal.repository_path}",
            operation_type=OperationType.VALIDATION,
            sequence_index=3,
            depends_on=[OperationDependency(operation_id=op_validate_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="validation",
                action_id="run_tests",
            ),
        )

        op_add = engine.build_operation(
            id=op_add_id,
            job_id=job_id,
            goal=f"Stage {goal.output_path} for commit",
            operation_type=OperationType.GIT,
            sequence_index=4,
            depends_on=[OperationDependency(operation_id=op_test_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="add",
            ),
        )

        return (op_generate, op_write, op_validate, op_test, op_add)

    def _build_modify_operations(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the six-step modify-mode per-operation pipeline (no commit).

        Step 1 — read_file (FS):    adapter_id="filesystem", action_id="read_file"
        Step 2 — generate (LLM):    adapter_id="llm",        action_id="generate"
        Step 3 — modify_file (FS):  adapter_id="filesystem", action_id="modify_file"
        Step 4 — validate:          adapter_id="validation",  action_id="validate"
        Step 5 — run_tests:         adapter_id="validation",  action_id="run_tests"
        Step 6 — add (Git):         adapter_id="git",        action_id="add"
        """
        gid = goal.goal_id
        op_read_id = f"op-read-{gid}"
        op_generate_id = f"op-generate-{gid}"
        op_write_id = f"op-write-{gid}"
        op_validate_id = f"op-validate-{gid}"
        op_test_id = f"op-test-{gid}"
        op_add_id = f"op-add-{gid}"

        engine = self._operation_engine

        op_read = engine.build_operation(
            id=op_read_id,
            job_id=job_id,
            goal=f"Read existing content of {goal.output_path}",
            operation_type=OperationType.FILESYSTEM,
            sequence_index=0,
            execution_ref=OperationExecutionReference(
                adapter_id="filesystem",
                action_id="read_file",
            ),
        )

        op_generate = engine.build_operation(
            id=op_generate_id,
            job_id=job_id,
            goal=f"Generate modified source code for: {goal.description}",
            operation_type=OperationType.LLM,
            sequence_index=1,
            depends_on=[OperationDependency(operation_id=op_read_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="llm",
                action_id="generate",
            ),
        )

        op_write = engine.build_operation(
            id=op_write_id,
            job_id=job_id,
            goal=f"Write modified code to {goal.output_path}",
            operation_type=OperationType.FILESYSTEM,
            sequence_index=2,
            depends_on=[OperationDependency(operation_id=op_generate_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="filesystem",
                action_id="modify_file",
            ),
        )

        op_validate = engine.build_operation(
            id=op_validate_id,
            job_id=job_id,
            goal=f"Validate modified code at {goal.output_path}",
            operation_type=OperationType.VALIDATION,
            sequence_index=3,
            depends_on=[OperationDependency(operation_id=op_write_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="validation",
                action_id="validate",
            ),
        )

        op_test = engine.build_operation(
            id=op_test_id,
            job_id=job_id,
            goal=f"Run test suite for repository {goal.repository_path}",
            operation_type=OperationType.VALIDATION,
            sequence_index=4,
            depends_on=[OperationDependency(operation_id=op_validate_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="validation",
                action_id="run_tests",
            ),
        )

        op_add = engine.build_operation(
            id=op_add_id,
            job_id=job_id,
            goal=f"Stage {goal.output_path} for commit",
            operation_type=OperationType.GIT,
            sequence_index=5,
            depends_on=[OperationDependency(operation_id=op_test_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="add",
            ),
        )

        return (op_read, op_generate, op_write, op_validate, op_test, op_add)

    def _build_llm_config(self) -> AdapterConfiguration:
        """Build the AdapterConfiguration from workflow config for LLM calls."""
        return AdapterConfiguration(
            provider=self._config.llm_provider,  # type: ignore[arg-type]
            model=self._config.llm_model,
            base_url=self._config.llm_base_url,
            api_key=self._config.llm_api_key,
            max_tokens=self._config.llm_max_tokens,
            timeout_seconds=self._config.llm_timeout_seconds,
            temperature=0.0,
        )

    # ── Step execution ─────────────────────────────────────────────────────────

    def _build_payload(
        self,
        op: OperationDefinition,
        goal: FounderGoal,
        context: dict[str, str],
    ) -> dict[str, str]:
        """Build the adapter-specific payload for an operation.

        Args:
            op:      The operation being executed.
            goal:    The Founder Goal containing paths and description.
            context: Cross-step data populated as steps complete:
                     "existing_content" — set after filesystem read_file
                     "generated_code"   — set after llm generate

        Returns:
            Payload dict for gateway.build_request().
        """
        ref = op.execution_ref
        assert ref is not None  # guaranteed by _build_operations

        if ref.adapter_id == "filesystem" and ref.action_id == "read_file":
            return {"path": goal.output_path}

        if ref.adapter_id == "llm" and ref.action_id == "generate":
            # Use modify-mode prompt if existing_content is in context (non-empty)
            if context.get("existing_content"):
                existing = context["existing_content"]
                return {
                    "system_prompt": (
                        "You are an expert software developer. "
                        "Generate clean, production-quality source code. "
                        "Return the complete modified file — preserve all existing content. "
                        "Respond with code only — no explanation, no markdown fences."
                    ),
                    "prompt": (
                        f"The following file exists:\n\n"
                        f"--- {goal.output_path} ---\n"
                        f"{existing}\n"
                        f"--- end of file ---\n\n"
                        f"Modify this file to accomplish the following task:\n\n"
                        f"{goal.description}\n\n"
                        f"Return the complete modified file. "
                        f"Preserve all existing functions and content."
                    ),
                }
            return {
                "system_prompt": (
                    "You are an expert software developer. "
                    "Generate clean, production-quality source code. "
                    "Respond with code only — no explanation, no markdown fences."
                ),
                "prompt": (
                    f"Write source code to accomplish the following task:\n\n"
                    f"{goal.description}\n\n"
                    f"Output file: {goal.output_path}"
                ),
            }

        if ref.adapter_id == "filesystem" and ref.action_id in (
            "create_file", "overwrite_file", "modify_file"
        ):
            return {
                "path": goal.output_path,
                "content": context.get("generated_code", ""),
            }

        if ref.adapter_id == "git" and ref.action_id == "add":
            # Convert workspace-relative output_path to repo-relative path.
            try:
                repo_relative = str(_Path(goal.output_path).relative_to(goal.repository_path))
            except ValueError:
                repo_relative = goal.output_path
            return {
                "repository_path": goal.repository_path,
                "files": repo_relative,
            }

        if ref.adapter_id == "validation" and ref.action_id == "validate":
            return {"path": goal.output_path}

        if ref.adapter_id == "validation" and ref.action_id == "run_tests":
            return {
                "repository_path": goal.repository_path,
                "test_command": self._config.test_command,
            }

        if ref.adapter_id == "git" and ref.action_id == "commit":
            return {
                "repository_path": goal.repository_path,
                "message": self._config.commit_message,
            }

        # Should never reach here if workflow is constructed correctly
        return {}

    def _execute_step(
        self,
        op: OperationDefinition,
        goal: FounderGoal,
        context: dict[str, str],
    ) -> StepExecutionRecord:
        """Execute one workflow step through the Gateway and appropriate adapter.

        Gateway invariant: gateway.dispatch() is called before every adapter
        invocation. If dispatch does not return DISPATCHED, the adapter is
        never called.

        Args:
            op:      The operation to execute.
            goal:    The Founder Goal for payload construction.
            context: Cross-step data (populated as steps complete).

        Returns:
            StepExecutionRecord capturing the full step outcome.
        """
        ref = op.execution_ref
        assert ref is not None  # guaranteed by _build_operations

        adapter_type = _ADAPTER_ID_TO_TYPE[ref.adapter_id]
        action_id = ref.action_id
        step_id = f"step-{op.id}"

        # ── Build and dispatch through the Gateway ────────────────────────
        payload = self._build_payload(op, goal, context)
        request = self._gateway.build_request(
            request_id=f"req-{op.id}",
            operation_id=op.id,
            adapter_type=adapter_type,
            action_id=action_id,
            payload=payload,
        )

        dispatch_result = self._gateway.dispatch(request)

        if dispatch_result.status != ExecutionStatus.DISPATCHED:
            logger.warning(
                "EngineeringWorkflow: gateway dispatch failed for op=%r "
                "status=%r error=%r",
                op.id,
                dispatch_result.status.value,
                dispatch_result.error,
            )
            return StepExecutionRecord(
                step_id=step_id,
                operation_id=op.id,
                adapter_type=adapter_type,
                action_id=action_id,
                execution_request=request,
                dispatch_status=dispatch_result.status,
                adapter_success=False,
                adapter_error=(
                    f"gateway_{dispatch_result.status.value}: {dispatch_result.error}"
                ),
                output="",
            )

        # ── Invoke adapter (Gateway dispatched successfully) ───────────────
        try:
            if adapter_type == ExecutionAdapter.LLM:
                llm_config = self._build_llm_config()
                adapter_result = self._llm_adapter.execute(request, llm_config)
                success = adapter_result.success
                error = adapter_result.error
                output = (
                    adapter_result.llm_response.content
                    if adapter_result.llm_response
                    else ""
                )
                logger.info(
                    "EngineeringWorkflow: LLM step complete op=%r action=%r success=%s "
                    "output_length=%d",
                    op.id, action_id, success, len(output or ""),
                )

            elif adapter_type == ExecutionAdapter.FILESYSTEM:
                adapter_result = self._filesystem_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                output = (
                    adapter_result.filesystem_result.content
                    if adapter_result.filesystem_result is not None
                    else ""
                )
                logger.info(
                    "EngineeringWorkflow: Filesystem step complete op=%r "
                    "action=%r success=%s",
                    op.id, action_id, success,
                )

            elif adapter_type == ExecutionAdapter.GIT:
                adapter_result = self._git_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                output = ""
                logger.info(
                    "EngineeringWorkflow: Git step complete op=%r action=%r "
                    "success=%s",
                    op.id, action_id, success,
                )

            elif adapter_type == ExecutionAdapter.VALIDATION:
                adapter_result = self._validation_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                output = ""
                if action_id == "run_tests":
                    from hermes.models.validation_adapter import TestRunExecutionResult as _TRER
                    if isinstance(adapter_result, _TRER) and adapter_result.test_run_result is not None:
                        tr = adapter_result.test_run_result
                        logger.info(
                            "EngineeringWorkflow: Test run complete op=%r success=%s "
                            "executed=%s tests_run=%d tests_failed=%d duration_ms=%d",
                            op.id, success, tr.executed, tr.tests_run,
                            tr.tests_failed, tr.duration_ms,
                        )
                    else:
                        logger.info(
                            "EngineeringWorkflow: Test run complete op=%r success=%s",
                            op.id, success,
                        )
                else:
                    logger.info(
                        "EngineeringWorkflow: Validation step complete op=%r "
                        "success=%s",
                        op.id, success,
                    )

            else:
                return StepExecutionRecord(
                    step_id=step_id,
                    operation_id=op.id,
                    adapter_type=adapter_type,
                    action_id=action_id,
                    execution_request=request,
                    dispatch_status=dispatch_result.status,
                    adapter_success=False,
                    adapter_error=f"unsupported adapter type: {adapter_type.value}",
                    output="",
                )

        except Exception as exc:
            error_msg = f"unexpected_adapter_error: {type(exc).__name__}: {exc}"
            logger.error(
                "EngineeringWorkflow: unexpected exception in op=%r: %s",
                op.id, error_msg,
            )
            return StepExecutionRecord(
                step_id=step_id,
                operation_id=op.id,
                adapter_type=adapter_type,
                action_id=action_id,
                execution_request=request,
                dispatch_status=dispatch_result.status,
                adapter_success=False,
                adapter_error=error_msg,
                output="",
            )

        return StepExecutionRecord(
            step_id=step_id,
            operation_id=op.id,
            adapter_type=adapter_type,
            action_id=action_id,
            execution_request=request,
            dispatch_status=dispatch_result.status,
            adapter_success=success,
            adapter_error=error,
            output=output or "",
        )

    # ── Main execution ─────────────────────────────────────────────────────────

    def execute(self, plan: EngineeringPlan, goal: FounderGoal) -> WorkflowExecutionReport:
        """Execute a pre-formed EngineeringPlan for the given FounderGoal.

        Steps:
          1. Bulk RepositoryManipulation validation (atomic — before any LLM call).
          2. Topological sort of plan operations via OperationEngine.
          3. Per-operation pipeline loop (generate → write → validate → run_tests → add).
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
            "EngineeringWorkflow: starting execution for goal_id=%r "
            "plan_id=%r operations=%d",
            gid, plan.plan_id, len(plan.operations),
        )

        # ── 1. Build mission ───────────────────────────────────────────────
        WorkflowMission(
            mission_id=mission_id,
            goal_id=gid,
            objective=goal.description,
        )

        all_steps: list[StepExecutionRecord] = []
        completed_count = 0

        # ── 2. Bulk RepositoryManipulation validation (atomic) ─────────────
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
                "EngineeringWorkflow: RepositoryManipulation raised for goal_id=%r: %s",
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
                    "failure_stage": "plan_validation",
                    "goal_id": gid,
                    "operations_completed": "0",
                }.items())),
            )

        if not rm_plan.valid:
            detail = "; ".join(c.detail for c in rm_plan.conflicts)
            error_msg = f"plan_validation_conflict: {detail}"
            logger.warning(
                "EngineeringWorkflow: plan validation conflict for goal_id=%r: %s",
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
                    "failure_stage": "plan_validation",
                    "goal_id": gid,
                    "operations_completed": "0",
                }.items())),
            )

        # ── 3. Determine execution order (OperationEngine topological sort) ─
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

        # ── 4. Per-operation pipeline loop ────────────────────────────────
        for op_id in execution_order:
            planned_op = op_by_id[op_id]

            # Derive per-op FounderGoal (output_path = workspace-relative path)
            per_op_output_path = str(_Path(goal.repository_path) / planned_op.target)
            per_op_goal = FounderGoal(
                goal_id=goal.goal_id,
                description=planned_op.goal,
                workspace_path=goal.workspace_path,
                repository_path=goal.repository_path,
                output_path=per_op_output_path,
            )

            # Build pipeline based on intent — no commit step per operation
            if planned_op.intent == "modify":
                operations = list(self._build_modify_operations(per_op_goal, job_id))
            else:
                operations = list(self._build_create_operations(per_op_goal, job_id))

            # Validate all ops via OperationEngine
            op_results = self._operation_engine.plan_all(
                operations, completed_op_ids=frozenset()
            )
            for op_result in op_results:
                v = op_result.validation_result
                if v is not None and not v.valid:
                    error_msg = f"operation_plan_invalid: {op_result.operation_id}"
                    return WorkflowExecutionReport(
                        report_id=report_id,
                        goal_id=gid,
                        mission_id=mission_id,
                        job_id=job_id,
                        steps=tuple(all_steps),
                        success=False,
                        error=error_msg,
                        execution_sequence=tuple(s.adapter_type.value for s in all_steps),
                        metadata=tuple(sorted({
                            "failure_stage": "planning",
                            "goal_id": gid,
                            "operations_completed": str(completed_count),
                        }.items())),
                    )

            # Execute pipeline steps in topological order
            op_exec_order = self._operation_engine.determine_execution_order(operations)
            context: dict[str, str] = {}

            for sub_op_id in op_exec_order:
                sub_op = next(o for o in operations if o.id == sub_op_id)
                step = self._execute_step(sub_op, per_op_goal, context)
                all_steps.append(step)

                if not step.adapter_success:
                    logger.warning(
                        "EngineeringWorkflow: halting at op=%r planned_op=%r: %r",
                        sub_op.id, op_id, step.adapter_error,
                    )
                    return WorkflowExecutionReport(
                        report_id=report_id,
                        goal_id=gid,
                        mission_id=mission_id,
                        job_id=job_id,
                        steps=tuple(all_steps),
                        success=False,
                        error=step.adapter_error,
                        execution_sequence=tuple(s.adapter_type.value for s in all_steps),
                        metadata=tuple(sorted({
                            "failure_stage": step.action_id,
                            "goal_id": gid,
                            "operations_completed": str(completed_count),
                        }.items())),
                    )

                # Populate cross-step context
                ref = sub_op.execution_ref
                if ref and ref.adapter_id == "llm" and ref.action_id == "generate":
                    context["generated_code"] = step.output
                elif ref and ref.adapter_id == "filesystem" and ref.action_id == "read_file":
                    context["existing_content"] = step.output

            completed_count += 1

        # ── 5. Single plan-level commit ────────────────────────────────────
        commit_op = self._operation_engine.build_operation(
            id=f"op-commit-plan-{gid}",
            job_id=job_id,
            goal="Commit all staged changes for the engineering plan",
            operation_type=OperationType.GIT,
            sequence_index=0,
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="commit",
            ),
        )

        # Pass the parent goal (not per_op_goal) for repository_path and commit_message
        commit_step = self._execute_step(commit_op, goal, {})
        all_steps.append(commit_step)

        if not commit_step.adapter_success:
            logger.warning(
                "EngineeringWorkflow: plan-level commit failed for goal_id=%r: %r",
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
                    "failure_stage": "commit",
                    "goal_id": gid,
                    "operations_completed": str(completed_count),
                }.items())),
            )

        # ── 6. Success ─────────────────────────────────────────────────────
        logger.info(
            "EngineeringWorkflow: all steps complete for goal_id=%r "
            "operations_completed=%d total_steps=%d",
            gid, completed_count, len(all_steps),
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
                "goal_id": gid,
                "operations_completed": str(completed_count),
                "planned_operations": str(len(plan.operations)),
                "repository": goal.repository_path,
            }.items())),
        )
