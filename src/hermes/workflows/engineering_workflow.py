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

Create mode (write_mode="create_file") — AT-3 path, 5 steps:
  1. LLM generates source code from the goal description
  2. Filesystem creates the output file
  3. Validation adapter checks the generated file (pre-commit gate)
  4. Git stages the output file
  5. Git commits the staged change

Modify mode (write_mode="modify_file") — AT-3 path, 6 steps:
  1. Filesystem reads the existing file content
  2. (RepositoryManipulationPlan validation — deterministic gate, no LLM)
  3. LLM generates the complete modified file (existing content in prompt)
  4. Filesystem writes the modified file (fails if file missing)
  5. Validation adapter checks the modified file (pre-commit gate)
  6. Git stages the output file
  7. Git commits the staged change

RepositoryManipulationPlan invariant (modify mode only):
  Before any LLM token is consumed, the planned MODIFY_FILE operation is
  validated against the live repository state. If the plan has conflicts
  (e.g. target file no longer exists), the workflow terminates immediately
  with success=False. No LLM call is made. No filesystem write occurs.

Gateway invariant (enforced per step):
  gateway.dispatch() is called for every ExecutionRequest before any adapter
  is invoked. If the Gateway does not return DISPATCHED, the adapter is
  never called and the workflow halts with success=False.

No adapter may bypass the Gateway. This is enforced structurally — the
workflow calls gateway.dispatch() before every adapter call.

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

Filesystem writes introduced (create mode):
  - One file write (via FilesystemAdapter → create_file).
  - One git add (via GitAdapter → _add).
  - One git commit (via GitAdapter → _commit).

Filesystem reads and writes introduced (modify mode):
  - One file read (via FilesystemAdapter → read_file).
  - One file write (via FilesystemAdapter → modify_file).
  - One git add (via GitAdapter → _add).
  - One git commit (via GitAdapter → _commit).
"""

from __future__ import annotations

import logging

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.job_engine import JobEngine
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.repository_manipulation import RepositoryManipulation
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
# These values match the ExecutionAdapter enum and the OperationExecutionReference
# adapter_id convention ("llm", "filesystem", "git").

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
        validation_adapter: ValidationAdapter,
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
            validation_adapter:   The Validation Adapter — pre-commit syntax gate.
            job_engine:           The Job Engine — job planning.
            operation_engine:     The Operation Engine — operation planning.
            config:               Workflow runtime configuration.
        """
        self._gateway = gateway
        self._llm_adapter = llm_adapter
        self._filesystem_adapter = filesystem_adapter
        self._git_adapter = git_adapter
        self._validation_adapter = validation_adapter
        self._job_engine = job_engine
        self._operation_engine = operation_engine
        self._config = config

    # ── Planning ──────────────────────────────────────────────────────────────

    def _build_operations(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the operation plan from a FounderGoal.

        Dispatches to the create-mode plan (4 steps) or modify-mode plan
        (5 steps) based on WorkflowConfig.write_mode.

        Create mode (write_mode="create_file"):
          generate → create_file → add → commit

        Modify mode (write_mode="modify_file"):
          read_file → generate → modify_file → add → commit
          (RepositoryManipulationPlan gate runs between read_file and generate
          in execute(), before any LLM token is consumed.)
        """
        if self._config.write_mode == "modify_file":
            return self._build_modify_operations(goal, job_id)
        return self._build_create_operations(goal, job_id)

    def _build_create_operations(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the six-step create-mode plan.

        Step 1 — generate (LLM):        adapter_id="llm",        action_id="generate"
        Step 2 — write (FS):             adapter_id="filesystem", action_id="create_file"
        Step 3 — validate (Validation):  adapter_id="validation", action_id="validate"
        Step 4 — run_tests (Validation): adapter_id="validation", action_id="run_tests"
        Step 5 — add (Git):              adapter_id="git",        action_id="add"
        Step 6 — commit (Git):           adapter_id="git",        action_id="commit"
        """
        gid = goal.goal_id
        op_generate_id = f"op-generate-{gid}"
        op_write_id = f"op-write-{gid}"
        op_validate_id = f"op-validate-{gid}"
        op_test_id = f"op-test-{gid}"
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

        op_commit = engine.build_operation(
            id=op_commit_id,
            job_id=job_id,
            goal="Commit staged changes with goal description as context",
            operation_type=OperationType.GIT,
            sequence_index=5,
            depends_on=[OperationDependency(operation_id=op_add_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="commit",
            ),
        )

        return (op_generate, op_write, op_validate, op_test, op_add, op_commit)

    def _build_modify_operations(
        self,
        goal: FounderGoal,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the seven-step modify-mode plan (AT-4 path).

        Step 1 — read_file (FS):    adapter_id="filesystem", action_id="read_file"
        Step 2 — generate (LLM):    adapter_id="llm",        action_id="generate"
                                    (RepositoryManipulationPlan gate runs before
                                     this step in execute() — no LLM tokens
                                     are consumed if the plan is invalid)
        Step 3 — modify_file (FS):  adapter_id="filesystem", action_id="modify_file"
        Step 4 — validate:          adapter_id="validation",  action_id="validate"
        Step 5 — run_tests:         adapter_id="validation",  action_id="run_tests"
        Step 6 — add (Git):         adapter_id="git",        action_id="add"
        Step 7 — commit (Git):      adapter_id="git",        action_id="commit"
        """
        gid = goal.goal_id
        op_read_id = f"op-read-{gid}"
        op_generate_id = f"op-generate-{gid}"
        op_write_id = f"op-write-{gid}"
        op_validate_id = f"op-validate-{gid}"
        op_test_id = f"op-test-{gid}"
        op_add_id = f"op-add-{gid}"
        op_commit_id = f"op-commit-{gid}"

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

        op_commit = engine.build_operation(
            id=op_commit_id,
            job_id=job_id,
            goal="Commit staged changes with goal description as context",
            operation_type=OperationType.GIT,
            sequence_index=6,
            depends_on=[OperationDependency(operation_id=op_add_id)],
            execution_ref=OperationExecutionReference(
                adapter_id="git",
                action_id="commit",
            ),
        )

        return (op_read, op_generate, op_write, op_validate, op_test, op_add, op_commit)

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

    def _run_modification_plan_gate(
        self,
        goal: FounderGoal,
        operation_id: str,
    ) -> str | None:
        """Validate the planned MODIFY_FILE operation before consuming LLM tokens.

        Builds a RepositoryManipulationPlan for the single MODIFY_FILE operation
        and returns an error string if the plan has conflicts, or None if valid.

        This gate enforces the invariant that Hermes never contacts the LLM
        provider if the target file is not in a state that allows modification
        (e.g. the file was deleted between mode detection and execution).

        Args:
            goal:         The FounderGoal containing workspace and file paths.
            operation_id: The operation ID for the plan (used for conflict linking).

        Returns:
            None if the plan is valid and execution should proceed.
            An error string starting with "manipulation_plan_conflict:" if invalid.
        """
        from pathlib import Path as _Path

        try:
            repo_relative = str(_Path(goal.output_path).relative_to(goal.repository_path))
        except ValueError:
            repo_relative = goal.output_path

        operation = RepositoryOperation(
            operation_id=operation_id,
            kind=RepositoryOperationKind.MODIFY_FILE,
            path=repo_relative,
        )

        try:
            rm_engine = RepositoryManipulation(workspace_root=goal.workspace_path)
            plan = rm_engine.plan(goal.repository_path, [operation])
        except Exception as exc:
            return f"manipulation_plan_error: {type(exc).__name__}: {exc}"

        if not plan.valid:
            detail = "; ".join(c.detail for c in plan.conflicts)
            return f"manipulation_plan_conflict: {detail}"

        return None

    # ── Step execution ─────────────────────────────────────────────────────────

    def _build_payload(
        self,
        op: OperationDefinition,
        goal: FounderGoal,
        context: dict[str, str],
    ) -> dict[str, str]:
        """Build the adapter-specific payload for an operation.

        Create-mode payloads (write_mode="create_file"):
          LLM generate:       prompt from goal description; create-mode system prompt
          Filesystem create_file: path + generated code
          Git add:            repository_path + repo-relative file path
          Git commit:         repository_path + commit message

        Modify-mode payloads (write_mode="modify_file"):
          Filesystem read_file:   path only (content returned via adapter result)
          LLM generate:       prompt includes existing file content from context;
                              modify-mode system prompt instructs complete file return
          Filesystem modify_file: path + generated code
          Git add/commit:     identical to create mode

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
            if self._config.write_mode == "modify_file":
                existing = context.get("existing_content", "")
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
                    "EngineeringWorkflow: LLM step complete op=%r success=%s "
                    "output_length=%d",
                    op.id, success, len(output),
                )

            elif adapter_type == ExecutionAdapter.FILESYSTEM:
                adapter_result = self._filesystem_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                # Return file content as output for read_file; empty for write ops.
                output = (
                    adapter_result.filesystem_result.content
                    if adapter_result.filesystem_result is not None
                    else ""
                )
                logger.info(
                    "EngineeringWorkflow: Filesystem step complete op=%r "
                    "action=%r success=%s path=%r",
                    op.id, action_id, success,
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
                        "success=%s validator=%r",
                        op.id, success,
                        (
                            adapter_result.validation_result.validator_used
                            if adapter_result.validation_result else "n/a"
                        ),
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
            ref = op.execution_ref

            # ── RepositoryManipulationPlan gate (modify mode only) ─────────
            # Runs before the LLM call — no tokens consumed if plan is invalid.
            if (
                self._config.write_mode == "modify_file"
                and ref is not None
                and ref.adapter_id == "llm"
                and ref.action_id == "generate"
            ):
                plan_error = self._run_modification_plan_gate(goal, op.id)
                if plan_error is not None:
                    logger.warning(
                        "EngineeringWorkflow: modification plan invalid for goal_id=%r: %s",
                        gid, plan_error,
                    )
                    return WorkflowExecutionReport(
                        report_id=report_id,
                        goal_id=gid,
                        mission_id=mission_id,
                        job_id=job_id,
                        steps=tuple(steps),
                        success=False,
                        error=plan_error,
                        execution_sequence=tuple(s.adapter_type.value for s in steps),
                        metadata=tuple(sorted({
                            "goal_id": gid,
                            "failure_stage": "manipulation_plan",
                            "steps_completed": str(len(steps)),
                        }.items())),
                    )

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
            if ref and ref.adapter_id == "llm":
                context["generated_code"] = step.output
            elif ref and ref.adapter_id == "filesystem" and ref.action_id == "read_file":
                context["existing_content"] = step.output

        # ── 6. Assemble success report ─────────────────────────────────────
        logger.info(
            "EngineeringWorkflow: all steps complete for goal_id=%r "
            "steps=%d",
            gid, len(steps),
        )

        if self._config.write_mode == "modify_file":
            success_metadata: dict[str, str] = {
                "goal_id": gid,
                "steps_completed": str(len(steps)),
                "modified_file": goal.output_path,
                "repository": goal.repository_path,
                "commit_message": self._config.commit_message,
            }
        else:
            success_metadata = {
                "goal_id": gid,
                "steps_completed": str(len(steps)),
                "generated_file": goal.output_path,
                "repository": goal.repository_path,
                "commit_message": self._config.commit_message,
            }

        return WorkflowExecutionReport(
            report_id=report_id,
            goal_id=gid,
            mission_id=mission_id,
            job_id=job_id,
            steps=tuple(steps),
            success=True,
            error=None,
            execution_sequence=tuple(s.adapter_type.value for s in steps),
            metadata=tuple(sorted(success_metadata.items())),
        )
