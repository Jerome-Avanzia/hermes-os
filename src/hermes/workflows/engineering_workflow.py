"""Engineering Workflow — Hermes' first autonomous engineering execution.

Architecture position:
  Founder Goal
       ↓
  EngineeringWorkflow   ← composition layer (this module)
       ↓
  JobEngine + OperationEngine   (planning — deterministic kernel)
       ↓
  Execution Gateway   (dispatch contracts — sole dispatch boundary)
       ↓
  LLM Adapter → Filesystem Adapter → Git Adapter   (execution — outside kernel)

Sprint 64: This module composes existing Hermes components into the first
complete engineering workflow. It does NOT introduce new architecture.
Every component used here already exists. The workflow's sole responsibility
is composition: receiving a Founder Goal and orchestrating existing components
to produce committed code.

Execution sequence (invariant — never changes):
  1. LLM generates source code from the goal description
  2. Filesystem writes the generated code to the output path
  3. Git stages the output file
  4. Git commits the staged change

Gateway invariant (enforced per step):
  gateway.dispatch() is called for every ExecutionRequest before any adapter
  is invoked. If the Gateway does not return DISPATCHED, the adapter is
  never called and the workflow halts with success=False.

No adapter may bypass the Gateway. This is enforced structurally — the
workflow calls gateway.dispatch() before every adapter call.

Adding future adapters (Docker, HTTP, Database) to future workflow steps
requires only:
  1. Register the adapter with the Gateway (AdapterRegistration).
  2. Add the workflow step with the appropriate OperationType.
  No changes to the Gateway, JobEngine, OperationEngine, or existing adapters.

Architecture invariants preserved:
  - The workflow never plans or routes — it orchestrates planning engines.
  - The workflow never calls adapters directly — it calls them only after
    a DISPATCHED gateway decision.
  - The workflow never raises exceptions — all failures are captured in
    WorkflowExecutionReport(success=False, error=...).
  - The workflow never performs filesystem work, network calls, or subprocess
    invocations directly — those are adapter responsibilities.

Sources of non-determinism introduced (explicit):
  - LLM outputs are non-deterministic (temperature=0.0 improves but doesn't
    guarantee determinism).
  - Filesystem and Git operations depend on external state.
  - All other logic is deterministic: same inputs → same plan, same IDs,
    same execution order.

Network calls introduced:
  - One LLM HTTP call (via LlmAdapter → ProviderDriver).

Filesystem writes introduced:
  - One file write (via FilesystemAdapter → create_file).
  - One git add (via GitAdapter → _add).
  - One git commit (via GitAdapter → _commit).
"""

from __future__ import annotations

import logging

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.job_engine import JobEngine
from hermes.kernel.operation_engine import OperationEngine
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

logger = logging.getLogger(__name__)

# ── Adapter ID → ExecutionAdapter mapping ──────────────────────────────────────
# These values match the ExecutionAdapter enum and the OperationExecutionReference
# adapter_id convention ("llm", "filesystem", "git").

_ADAPTER_ID_TO_TYPE: dict[str, ExecutionAdapter] = {
    op.value: op
    for op in [ExecutionAdapter.LLM, ExecutionAdapter.FILESYSTEM, ExecutionAdapter.GIT]
}


class EngineeringWorkflow:
    """Hermes' first complete autonomous engineering workflow.

    Receives a Founder Goal and orchestrates existing components to produce
    committed code:

      FounderGoal
           ↓
      (plan) JobEngine + OperationEngine
           ↓
      (dispatch) Execution Gateway
           ↓
      (generate) LLM Adapter
           ↓
      (write) Filesystem Adapter
           ↓
      (stage) Git Adapter [add]
           ↓
      (commit) Git Adapter [commit]

    The execution sequence is fixed and invariant. Each step passes through
    the Execution Gateway before the adapter is invoked. Failure at any step
    halts execution and returns a WorkflowExecutionReport with success=False.

    Construction::

        from hermes.kernel.skill_registry import SkillRegistry

        gateway = ExecutionGateway()
        gateway.register(AdapterRegistration(adapter=ExecutionAdapter.LLM, ...))
        gateway.register(AdapterRegistration(adapter=ExecutionAdapter.FILESYSTEM, ...))
        gateway.register(AdapterRegistration(adapter=ExecutionAdapter.GIT, ...))

        workflow = EngineeringWorkflow(
            gateway=gateway,
            llm_adapter=LlmAdapter(),
            filesystem_adapter=FilesystemAdapter(workspace_root="/tmp/workspace"),
            git_adapter=GitAdapter(workspace_root="/tmp/workspace"),
            job_engine=JobEngine(registry=SkillRegistry()),
            operation_engine=OperationEngine(),
            config=WorkflowConfig(...),
        )

        report = workflow.execute(goal)
    """

    def __init__(
        self,
        gateway: ExecutionGateway,
        llm_adapter: LlmAdapter,
        filesystem_adapter: FilesystemAdapter,
        git_adapter: GitAdapter,
        job_engine: JobEngine,
        operation_engine: OperationEngine,
        config: WorkflowConfig,
    ) -> None:
        """Initialise the workflow with all required components.

        Args:
            gateway:              The Execution Gateway — sole dispatch boundary.
            llm_adapter:          The LLM Adapter — code generation.
            filesystem_adapter:   The Filesystem Adapter — file writing.
            git_adapter:          The Git Adapter — staging and committing.
            job_engine:           The Job Engine — job planning.
            operation_engine:     The Operation Engine — operation planning.
            config:               Workflow runtime configuration.
        """
        self._gateway = gateway
        self._llm_adapter = llm_adapter
        self._filesystem_adapter = filesystem_adapter
        self._git_adapter = git_adapter
        self._job_engine = job_engine
        self._operation_engine = operation_engine
        self._config = config

    # ── Planning ──────────────────────────────────────────────────────────────

    def _build_operations(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the four-step operation plan from a FounderGoal.

        Returns operations in definition order (not execution order — the
        OperationEngine computes topological order separately).

        Step 1 — generate (LLM):
          Uses OperationType.LLM. OperationExecutionReference declares
          adapter_id="llm", action_id="generate".

        Step 2 — write (Filesystem):
          Uses OperationType.FILESYSTEM. Depends on generate.
          adapter_id="filesystem", action_id="create_file".

        Step 3 — add (Git):
          Uses OperationType.GIT. Depends on write.
          adapter_id="git", action_id="add".

        Step 4 — commit (Git):
          Uses OperationType.GIT. Depends on add.
          adapter_id="git", action_id="commit".
        """
        gid = goal.goal_id
        op_generate_id = f"op-generate-{gid}"
        op_write_id = f"op-write-{gid}"
        op_add_id = f"op-add-{gid}"
        op_commit_id = f"op-commit-{gid}"

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

        op_add = engine.build_operation(
            id=op_add_id,
            job_id=job_id,
            goal=f"Stage {goal.output_path} for commit",
            operation_type=OperationType.GIT,
            sequence_index=2,
            depends_on=[OperationDependency(operation_id=op_write_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="add",
            ),
        )

        op_commit = engine.build_operation(
            id=op_commit_id,
            job_id=job_id,
            goal="Commit staged changes with goal description as context",
            operation_type=OperationType.GIT,
            sequence_index=3,
            depends_on=[OperationDependency(operation_id=op_add_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="commit",
            ),
        )

        return (op_generate, op_write, op_add, op_commit)

    def _build_llm_config(self) -> AdapterConfiguration:
        """Build the AdapterConfiguration from workflow config for LLM calls."""
        from hermes.models.llm_adapter import LLMProvider
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

        Each operation type has a fixed payload shape:

          LLM generate:
            prompt        → code generation instruction from goal description
            system_prompt → developer persona

          Filesystem create_file:
            path    → goal.output_path (workspace-relative)
            content → generated code from the LLM step (from context)

          Git add:
            repository_path → goal.repository_path
            files           → goal.output_path (stage only the generated file)

          Git commit:
            repository_path → goal.repository_path
            message         → config.commit_message

        Args:
            op:      The operation being executed.
            goal:    The Founder Goal containing paths and description.
            context: Cross-step data; "generated_code" is set after the LLM step.

        Returns:
            Payload dict for gateway.build_request().
        """
        ref = op.execution_ref
        assert ref is not None  # guaranteed by _build_operations

        if ref.adapter_id == "llm" and ref.action_id == "generate":
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

        if ref.adapter_id == "filesystem" and ref.action_id == "create_file":
            return {
                "path": goal.output_path,
                "content": context.get("generated_code", ""),
            }

        if ref.adapter_id == "git" and ref.action_id == "add":
            # Convert workspace-relative output_path to repo-relative path.
            # goal.output_path is workspace-relative ("my-project/hello.py").
            # git add runs inside the repo root, so it needs "hello.py".
            from pathlib import Path as _Path
            try:
                repo_relative = str(_Path(goal.output_path).relative_to(goal.repository_path))
            except ValueError:
                repo_relative = goal.output_path
            return {
                "repository_path": goal.repository_path,
                "files": repo_relative,
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
                    "EngineeringWorkflow: LLM step complete op=%r success=%s "
                    "output_length=%d",
                    op.id, success, len(output),
                )

            elif adapter_type == ExecutionAdapter.FILESYSTEM:
                adapter_result = self._filesystem_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                output = ""
                logger.info(
                    "EngineeringWorkflow: Filesystem step complete op=%r "
                    "success=%s path=%r",
                    op.id, success,
                    adapter_result.filesystem_request.path
                    if adapter_result.filesystem_request else "",
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
            # Belt-and-suspenders: adapters are designed to never raise,
            # but capture any unexpected exception without propagating.
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
            output=output,
        )

    # ── Main execution ─────────────────────────────────────────────────────────

    def execute(self, goal: FounderGoal) -> WorkflowExecutionReport:
        """Execute a complete engineering workflow from a Founder Goal.

        Execution steps:
          1. Derive mission from goal.
          2. Build four-step operation plan (JobEngine + OperationEngine).
          3. Validate all operations; fail early if any is invalid.
          4. Compute topological execution order.
          5. For each operation in order:
             a. Build ExecutionRequest (via Gateway).
             b. gateway.dispatch() → validate and produce dispatch contract.
             c. If not DISPATCHED → halt with success=False.
             d. Invoke the appropriate adapter.
             e. If adapter fails → halt with success=False.
             f. Extract output for downstream steps.
          6. Assemble and return WorkflowExecutionReport.

        The workflow never raises exceptions. All failures are captured in
        WorkflowExecutionReport(success=False, error=...).

        Args:
            goal: The Founder Goal describing what to build.

        Returns:
            WorkflowExecutionReport capturing the complete execution audit trail.
        """
        gid = goal.goal_id
        mission_id = f"mission-{gid}"
        job_id = f"job-{gid}"
        report_id = f"report-{gid}"

        logger.info(
            "EngineeringWorkflow: starting execution for goal_id=%r description=%r",
            gid, goal.description[:80],
        )

        # ── 1. Build mission ───────────────────────────────────────────────
        mission = WorkflowMission(
            mission_id=mission_id,
            goal_id=gid,
            objective=goal.description,
        )

        # ── 2. Build operations ────────────────────────────────────────────
        operations = list(self._build_operations(goal, job_id))

        # ── 3. Validate all operations (OperationEngine) ───────────────────
        op_results = self._operation_engine.plan_all(
            operations, completed_op_ids=frozenset()
        )
        for op_result in op_results:
            v = op_result.validation_result
            if v is not None and not v.valid:
                error_msg = (
                    f"operation_plan_invalid: {op_result.operation_id} — "
                    + "; ".join(e.message if hasattr(e, "message") else str(e) for e in v.errors)
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
                        "goal_id": gid,
                        "failure_stage": "planning",
                        "steps_completed": "0",
                    }.items())),
                )

        # ── 4. Topological execution order (OperationEngine) ───────────────
        execution_order = self._operation_engine.determine_execution_order(operations)

        # ── 5. Execute steps in order ─────────────────────────────────────
        steps: list[StepExecutionRecord] = []
        context: dict[str, str] = {}   # cross-step data (generated code, etc.)

        for op_id in execution_order:
            op = next(o for o in operations if o.id == op_id)

            step = self._execute_step(op, goal, context)
            steps.append(step)

            if not step.adapter_success:
                logger.warning(
                    "EngineeringWorkflow: halting at op=%r due to step failure: %r",
                    op.id, step.adapter_error,
                )
                return WorkflowExecutionReport(
                    report_id=report_id,
                    goal_id=gid,
                    mission_id=mission_id,
                    job_id=job_id,
                    steps=tuple(steps),
                    success=False,
                    error=step.adapter_error,
                    execution_sequence=tuple(s.adapter_type.value for s in steps),
                    metadata=tuple(sorted({
                        "goal_id": gid,
                        "failure_stage": step.action_id,
                        "steps_completed": str(len(steps)),
                    }.items())),
                )

            # Populate cross-step context
            ref = op.execution_ref
            if ref and ref.adapter_id == "llm":
                context["generated_code"] = step.output

        # ── 6. Assemble success report ─────────────────────────────────────
        logger.info(
            "EngineeringWorkflow: all steps complete for goal_id=%r "
            "steps=%d",
            gid, len(steps),
        )

        return WorkflowExecutionReport(
            report_id=report_id,
            goal_id=gid,
            mission_id=mission_id,
            job_id=job_id,
            steps=tuple(steps),
            success=True,
            error=None,
            execution_sequence=tuple(s.adapter_type.value for s in steps),
            metadata=tuple(sorted({
                "goal_id": gid,
                "steps_completed": str(len(steps)),
                "generated_file": goal.output_path,
                "repository": goal.repository_path,
                "commit_message": self._config.commit_message,
            }.items())),
        )
