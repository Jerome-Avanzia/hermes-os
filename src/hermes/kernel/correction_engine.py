"""CorrectionEngine — single-operation executor with autonomous correction loop.

Architecture position:
  EngineeringWorkflow   (plan coordinator)
       ↓ delegates each PlannedOperation
  CorrectionEngine      (this module)
       ↓
  Execution Gateway → Adapters (LLM, Filesystem, Git, Validation)

Phase 7: CorrectionEngine owns the complete lifecycle of one PlannedOperation:

  Initial attempt:
    Create mode: generate → create_file → validate → run_tests → add
    Modify mode: read_file → generate → modify_file → validate → run_tests → add

  On validate or run_tests failure (correctable):
    Correction cycle N (up to WorkflowConfig.max_corrections):
      read_file → llm_correct → modify_file → validate → run_tests
    If all gates pass: git add → return success (correction_attempts=N)
    If max_corrections exhausted: return failure ("repair_limit_exceeded")

  On any other step failure (non-correctable):
    Return failure immediately (correction_attempts=0).

EngineeringWorkflow retains ownership of:
  - Bulk RepositoryManipulation validation (before any CorrectionEngine call)
  - Topological sort of PlannedOperations
  - The single plan-level git commit
  - Building WorkflowExecutionReport from accumulated OperationCorrectionResults

Gateway invariant (enforced per step):
  gateway.dispatch() is called before every adapter invocation. If the Gateway
  does not return DISPATCHED, the adapter is never called.

Architecture invariants preserved:
  - CorrectionEngine never plans or routes — it receives one pre-formed
    PlannedOperation and executes it.
  - CorrectionEngine never calls adapters directly — only after DISPATCHED.
  - CorrectionEngine never commits — git.add is the final step; commit is
    the EngineeringWorkflow's responsibility.
  - CorrectionEngine never raises exceptions — all failures captured in
    OperationCorrectionResult(success=False, error=...).
  - Step IDs are namespaced by PlannedOperation.operation_id so that steps
    from different planned operations are distinguishable in the audit trail.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path as _Path

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.models.engineering_plan import PlannedOperation
from hermes.models.engineering_workflow import (
    CorrectionRecord,
    FounderGoal,
    OperationCorrectionResult,
    StepExecutionRecord,
    WorkflowConfig,
)
from hermes.models.execution_gateway import ExecutionAdapter, ExecutionStatus
from hermes.models.llm_adapter import AdapterConfiguration
from hermes.models.operation import (
    OperationDefinition,
    OperationDependency,
    OperationExecutionReference,
    OperationType,
)
from hermes.models.validation_adapter import TestRunExecutionResult, ValidationExecutionResult

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

# Maximum chars of error output included in a correction prompt.
_MAX_ERROR_EXCERPT_CHARS = 2000

# action_ids that trigger the correction loop on failure.
_CORRECTABLE_ACTIONS = frozenset({"validate", "run_tests"})


class CorrectionEngine:
    """Executes one PlannedOperation with an autonomous correction loop.

    Receives a single PlannedOperation from EngineeringWorkflow, runs the
    appropriate pipeline (create or modify), and — if the validate or run_tests
    gate fails — attempts autonomous correction by feeding the error output
    back to the LLM and retrying the gate, up to WorkflowConfig.max_corrections
    times.

    Construction::

        engine = CorrectionEngine(
            gateway=gateway,
            llm_adapter=llm_adapter,
            filesystem_adapter=FilesystemAdapter(workspace_root=...),
            git_adapter=GitAdapter(workspace_root=...),
            validation_adapter=ValidationAdapter(workspace_root=...),
            operation_engine=OperationEngine(),
            config=WorkflowConfig(...),
        )

        result = engine.execute_operation(planned_op, goal)
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
        self._gateway = gateway
        self._llm_adapter = llm_adapter
        self._filesystem_adapter = filesystem_adapter
        self._git_adapter = git_adapter
        self._validation_adapter = validation_adapter
        self._operation_engine = operation_engine
        self._config = config

    # ── Pipeline builders ──────────────────────────────────────────────────────

    def _build_create_operations(
        self,
        goal: FounderGoal,
        op_prefix: str,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the five-step create-mode pipeline.

        Step IDs are namespaced by op_prefix (the PlannedOperation.operation_id)
        so that steps from different planned operations remain distinguishable
        in the flat WorkflowExecutionReport.steps audit trail.

        Steps:
          1 — generate    (llm)        action_id="generate"
          2 — create_file (filesystem) action_id="create_file"
          3 — validate    (validation) action_id="validate"
          4 — run_tests   (validation) action_id="run_tests"
          5 — add         (git)        action_id="add"
        """
        engine = self._operation_engine

        op_generate_id = f"op-generate-{op_prefix}"
        op_write_id = f"op-write-{op_prefix}"
        op_validate_id = f"op-validate-{op_prefix}"
        op_test_id = f"op-test-{op_prefix}"
        op_add_id = f"op-add-{op_prefix}"

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
        op_prefix: str,
        job_id: str,
    ) -> tuple[OperationDefinition, ...]:
        """Build the six-step modify-mode pipeline.

        Steps:
          1 — read_file   (filesystem) action_id="read_file"
          2 — generate    (llm)        action_id="generate"
          3 — modify_file (filesystem) action_id="modify_file"
          4 — validate    (validation) action_id="validate"
          5 — run_tests   (validation) action_id="run_tests"
          6 — add         (git)        action_id="add"
        """
        engine = self._operation_engine

        op_read_id = f"op-read-{op_prefix}"
        op_generate_id = f"op-generate-{op_prefix}"
        op_write_id = f"op-write-{op_prefix}"
        op_validate_id = f"op-validate-{op_prefix}"
        op_test_id = f"op-test-{op_prefix}"
        op_add_id = f"op-add-{op_prefix}"

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

    # ── LLM config ─────────────────────────────────────────────────────────────

    def _build_llm_config(self) -> AdapterConfiguration:
        return AdapterConfiguration(
            provider=self._config.llm_provider,  # type: ignore[arg-type]
            model=self._config.llm_model,
            base_url=self._config.llm_base_url,
            api_key=self._config.llm_api_key,
            max_tokens=self._config.llm_max_tokens,
            timeout_seconds=self._config.llm_timeout_seconds,
            temperature=0.0,
        )

    # ── Payload construction ───────────────────────────────────────────────────

    def _build_payload(
        self,
        op: OperationDefinition,
        goal: FounderGoal,
        context: dict[str, str],
        payload_override: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the adapter-specific payload for a pipeline step.

        If payload_override is provided it is returned directly — used by the
        correction loop to inject a custom LLM prompt without changing the
        dispatch infrastructure.

        Args:
            op:              The operation being executed.
            goal:            The per-operation FounderGoal (paths + description).
            context:         Cross-step data: "existing_content" after read_file,
                             "generated_code" after llm generate.
            payload_override: If set, returned as-is (skips normal construction).
        """
        if payload_override is not None:
            return payload_override

        ref = op.execution_ref
        assert ref is not None

        if ref.adapter_id == "filesystem" and ref.action_id == "read_file":
            return {"path": goal.output_path}

        if ref.adapter_id == "llm" and ref.action_id == "generate":
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

        return {}

    def _find_failing_test_files(
        self,
        error_excerpt: str,
        repo_root: _Path,
    ) -> list[_Path]:
        """Extract test file paths reported as failing in pytest output.

        Matches two pytest output patterns:
          FAILED tests/foo.py::TestClass::test_method
          tests/foo.py:14: AssertionError

        Returns only paths that exist on disk relative to repo_root.
        Returns an empty list when no recognisable paths are found — the
        caller falls back to all test files.
        """
        pattern = re.compile(
            r'(?:^FAILED\s+|^)([\w./\\-]+\.py)(?:::|:\d+)',
            re.MULTILINE,
        )
        found: set[_Path] = set()
        for m in pattern.finditer(error_excerpt):
            candidate = repo_root / m.group(1)
            if candidate.is_file():
                found.add(candidate.resolve())
        return sorted(found)

    def _build_test_context(
        self,
        error_excerpt: str,
        repo_root: _Path,
    ) -> str:
        """Build the test-files section for a correction prompt.

        Attempts to identify only the test files that pytest reported as
        failing. Falls back to all *.py files under tests/ if none can be
        identified reliably from the output.

        Returns an empty string when no test files are found at all.
        """
        test_files = self._find_failing_test_files(error_excerpt, repo_root)

        if not test_files:
            # Fallback: include all test files so the LLM always sees the contract.
            test_files = sorted(repo_root.glob("tests/**/*.py"))

        if not test_files:
            return ""

        parts: list[str] = [
            "Test files (define the contract the implementation must satisfy):\n"
        ]
        for path in test_files:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                try:
                    rel = path.relative_to(repo_root)
                except ValueError:
                    rel = path
                parts.append(f"--- {rel} ---\n{content}\n--- end ---\n\n")
            except OSError:
                pass

        return "".join(parts)

    def _build_correction_payload(
        self,
        goal: FounderGoal,
        error_excerpt: str,
    ) -> dict[str, str]:
        """Build the LLM payload for a correction cycle.

        Prompt structure:
          1. Task description (per-operation goal only; no repo context blob).
          2. Current implementation (read from disk — the failed attempt).
          3. Failing test files (parsed from pytest output; fallback to all tests).
          4. Full failure output (pytest stdout+stderr, ≤2000 chars).

        This payload is passed as payload_override to _execute_step so the
        gateway dispatch infrastructure is unchanged.
        """
        # Read current file content directly — the file exists on disk from the
        # failed write step. This read does not go through the gateway because it
        # is internal CorrectionEngine state assembly, not an adapter action.
        try:
            current_code = _Path(goal.output_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            current_code = "(file unreadable)"

        # Strip repository context blob if present (injected by implement.py for
        # the initial planning call; noise during repair).
        task = goal.description
        if "\n\nRepository context:\n" in task:
            task = task.split("\n\nRepository context:\n")[0].strip()

        repo_root = _Path(goal.workspace_path) / goal.repository_path
        test_context = self._build_test_context(error_excerpt, repo_root)

        return {
            "system_prompt": (
                "You are an expert software developer. "
                "A previous implementation failed validation or tests. "
                "Study the failing tests to understand the exact contract, "
                "then return a complete corrected implementation. "
                "Respond with code only — no explanation, no markdown fences."
            ),
            "prompt": (
                f"Task: {task}\n\n"
                f"Current implementation:\n"
                f"--- {goal.output_path} ---\n"
                f"{current_code}\n"
                f"--- end ---\n\n"
                f"{test_context}"
                f"Failure output:\n{error_excerpt}\n\n"
                f"Return the complete corrected file."
            ),
        }

    # ── Step execution ─────────────────────────────────────────────────────────

    def _execute_step(
        self,
        op: OperationDefinition,
        goal: FounderGoal,
        context: dict[str, str],
        payload_override: dict[str, str] | None = None,
    ) -> StepExecutionRecord:
        """Execute one pipeline step through the Gateway and the appropriate adapter.

        Gateway invariant: gateway.dispatch() is called before every adapter
        invocation. If dispatch does not return DISPATCHED, the adapter is
        never called.

        For run_tests steps the raw test output (stdout + stderr, ≤2000 chars)
        is stored in StepExecutionRecord.output to enable error excerpt
        extraction by the correction loop without re-querying the adapter.

        Args:
            op:              The operation to execute.
            goal:            The per-operation FounderGoal.
            context:         Cross-step data populated as steps complete.
            payload_override: If set, passed directly to the adapter as payload
                             (used by the correction loop for the LLM call).
        """
        ref = op.execution_ref
        assert ref is not None

        adapter_type = _ADAPTER_ID_TO_TYPE[ref.adapter_id]
        action_id = ref.action_id
        step_id = f"step-{op.id}"

        payload = self._build_payload(op, goal, context, payload_override)
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
                "CorrectionEngine: gateway dispatch failed op=%r status=%r error=%r",
                op.id, dispatch_result.status.value, dispatch_result.error,
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
                    "CorrectionEngine: LLM step op=%r action=%r success=%s output_len=%d",
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
                    "CorrectionEngine: Filesystem step op=%r action=%r success=%s",
                    op.id, action_id, success,
                )

            elif adapter_type == ExecutionAdapter.GIT:
                adapter_result = self._git_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                output = ""
                logger.info(
                    "CorrectionEngine: Git step op=%r action=%r success=%s",
                    op.id, action_id, success,
                )

            elif adapter_type == ExecutionAdapter.VALIDATION:
                adapter_result = self._validation_adapter.execute(request)
                success = adapter_result.success
                error = adapter_result.error
                output = ""

                if action_id == "run_tests" and isinstance(
                    adapter_result, TestRunExecutionResult
                ):
                    tr = adapter_result.test_run_result
                    if tr is not None:
                        # Store raw test output for correction context extraction.
                        raw = (tr.stdout + "\n" + tr.stderr).strip()
                        output = raw[:_MAX_ERROR_EXCERPT_CHARS]
                        logger.info(
                            "CorrectionEngine: Test run op=%r success=%s executed=%s "
                            "tests_run=%d tests_failed=%d duration_ms=%d",
                            op.id, success, tr.executed, tr.tests_run,
                            tr.tests_failed, tr.duration_ms,
                        )
                    else:
                        logger.info(
                            "CorrectionEngine: Test run op=%r success=%s (no result)",
                            op.id, success,
                        )
                elif action_id == "validate" and isinstance(
                    adapter_result, ValidationExecutionResult
                ):
                    vr = adapter_result.validation_result
                    if vr is not None and not vr.success:
                        # Store validation error for correction context extraction.
                        raw = (vr.stderr or vr.stdout or "").strip()
                        output = raw[:_MAX_ERROR_EXCERPT_CHARS]
                    logger.info(
                        "CorrectionEngine: Validation step op=%r success=%s",
                        op.id, success,
                    )
                else:
                    logger.info(
                        "CorrectionEngine: Validation step op=%r action=%r success=%s",
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
                    adapter_error=f"unsupported_adapter_type: {adapter_type.value}",
                    output="",
                )

        except Exception as exc:
            error_msg = f"unexpected_adapter_error: {type(exc).__name__}: {exc}"
            logger.error(
                "CorrectionEngine: unexpected exception op=%r: %s", op.id, error_msg,
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

    # ── Error excerpt extraction ────────────────────────────────────────────────

    def _extract_error_excerpt(self, step: StepExecutionRecord) -> str:
        """Extract the error text from a failed validate or run_tests step.

        For validate steps: returns step.output (populated with stderr by
        _execute_step) falling back to step.adapter_error.
        For run_tests steps: returns step.output (stdout+stderr captured by
        _execute_step) falling back to step.adapter_error.
        Always truncated to _MAX_ERROR_EXCERPT_CHARS.
        """
        raw = (step.output or step.adapter_error or "unknown error").strip()
        return raw[:_MAX_ERROR_EXCERPT_CHARS]

    # ── Main public method ─────────────────────────────────────────────────────

    def execute_operation(
        self,
        planned_op: PlannedOperation,
        goal: FounderGoal,
    ) -> OperationCorrectionResult:
        """Execute one PlannedOperation with an autonomous correction loop.

        Derives a per-operation FounderGoal, selects the appropriate pipeline
        (create or modify), executes steps sequentially, and — on a correctable
        failure (validate or run_tests) — runs correction cycles up to
        WorkflowConfig.max_corrections before declaring failure.

        The git.add step is only executed after all validation gates pass (either
        on the first attempt or after a successful correction cycle).

        Returns:
            OperationCorrectionResult capturing the complete audit trail and
            correction metadata. Never raises.
        """
        op_id = planned_op.operation_id
        job_id = f"job-{goal.goal_id}"

        # Derive per-operation FounderGoal.
        per_op_output_path = str(_Path(goal.repository_path) / planned_op.target)
        per_op_goal = FounderGoal(
            goal_id=goal.goal_id,
            description=planned_op.goal,
            workspace_path=goal.workspace_path,
            repository_path=goal.repository_path,
            output_path=per_op_output_path,
        )

        logger.info(
            "CorrectionEngine: starting operation op_id=%r intent=%r target=%r",
            op_id, planned_op.intent, planned_op.target,
        )

        # Build pipeline based on intent.
        if planned_op.intent == "modify":
            pipeline = list(self._build_modify_operations(per_op_goal, op_id, job_id))
        else:
            pipeline = list(self._build_create_operations(per_op_goal, op_id, job_id))

        # Validate all pipeline ops via OperationEngine before executing.
        op_results = self._operation_engine.plan_all(
            pipeline, completed_op_ids=frozenset()
        )
        for op_result in op_results:
            v = op_result.validation_result
            if v is not None and not v.valid:
                error_msg = f"operation_plan_invalid: {op_result.operation_id}"
                logger.warning("CorrectionEngine: %s", error_msg)
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=False,
                    correction_attempts=0,
                    correction_log=(),
                    steps=(),
                    error=error_msg,
                )

        # Determine step execution order.
        exec_order = self._operation_engine.determine_execution_order(pipeline)
        op_by_id = {o.id: o for o in pipeline}

        # Separate the git.add step (always last) from the validation pipeline.
        add_op_id = f"op-add-{op_id}"
        pre_add_order = [sid for sid in exec_order if sid != add_op_id]

        all_steps: list[StepExecutionRecord] = []
        context: dict[str, str] = {}
        failed_step: StepExecutionRecord | None = None

        # ── Initial pipeline (all steps except git.add) ────────────────────────
        for sub_op_id in pre_add_order:
            sub_op = op_by_id[sub_op_id]
            step = self._execute_step(sub_op, per_op_goal, context)
            all_steps.append(step)

            if step.adapter_success:
                ref = sub_op.execution_ref
                if ref and ref.adapter_id == "llm" and ref.action_id == "generate":
                    context["generated_code"] = step.output
                elif ref and ref.adapter_id == "filesystem" and ref.action_id == "read_file":
                    context["existing_content"] = step.output
            else:
                failed_step = step
                break

        if failed_step is None:
            # All pre-add steps succeeded on first attempt — execute git.add.
            add_op = op_by_id[add_op_id]
            add_step = self._execute_step(add_op, per_op_goal, context)
            all_steps.append(add_step)

            if not add_step.adapter_success:
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=False,
                    correction_attempts=0,
                    correction_log=(),
                    steps=tuple(all_steps),
                    error=add_step.adapter_error,
                )

            logger.info(
                "CorrectionEngine: operation succeeded on first attempt op_id=%r",
                op_id,
            )
            return OperationCorrectionResult(
                operation_id=op_id,
                success=True,
                correction_attempts=0,
                correction_log=(),
                steps=tuple(all_steps),
                error=None,
            )

        # ── Failure on initial attempt ─────────────────────────────────────────
        if failed_step.action_id not in _CORRECTABLE_ACTIONS:
            # Non-correctable failure (gateway, LLM, filesystem write, git).
            logger.warning(
                "CorrectionEngine: non-correctable failure op_id=%r action=%r error=%r",
                op_id, failed_step.action_id, failed_step.adapter_error,
            )
            return OperationCorrectionResult(
                operation_id=op_id,
                success=False,
                correction_attempts=0,
                correction_log=(),
                steps=tuple(all_steps),
                error=failed_step.adapter_error,
            )

        # ── Correction loop ────────────────────────────────────────────────────
        # If max_corrections=0 the loop is disabled: return the raw failure
        # immediately, matching Phase 4 behaviour (halt without correction).
        if self._config.max_corrections <= 0:
            return OperationCorrectionResult(
                operation_id=op_id,
                success=False,
                correction_attempts=0,
                correction_log=(),
                steps=tuple(all_steps),
                error=failed_step.adapter_error,
            )

        pending_trigger = (
            "validation_failure"
            if failed_step.action_id == "validate"
            else "test_failure"
        )
        pending_error_excerpt = self._extract_error_excerpt(failed_step)
        correction_records: list[CorrectionRecord] = []
        correction_count = 0

        while correction_count < self._config.max_corrections:
            correction_count += 1
            corr_prefix = f"corr{correction_count}-{op_id}"

            logger.info(
                "CorrectionEngine: correction attempt %d/%d op_id=%r trigger=%r",
                correction_count, self._config.max_corrections, op_id, pending_trigger,
            )

            # Record what triggered this correction attempt.
            correction_records.append(CorrectionRecord(
                attempt=correction_count,
                trigger=pending_trigger,
                error_excerpt=pending_error_excerpt,
            ))

            # 1. Build correction LLM payload (reads current file from disk).
            correction_payload = self._build_correction_payload(
                per_op_goal, pending_error_excerpt
            )

            # 2. LLM correction call (action_id="generate" with correction prompt).
            corr_llm_op = self._operation_engine.build_operation(
                id=f"op-generate-{corr_prefix}",
                job_id=job_id,
                goal=f"Correction {correction_count}: generate fixed code for {per_op_goal.output_path}",
                operation_type=OperationType.LLM,
                sequence_index=0,
                execution_ref=OperationExecutionReference(
                    adapter_id="llm",
                    action_id="generate",
                ),
            )
            llm_step = self._execute_step(
                corr_llm_op, per_op_goal, {}, payload_override=correction_payload
            )
            all_steps.append(llm_step)

            if not llm_step.adapter_success:
                logger.warning(
                    "CorrectionEngine: correction LLM call failed op_id=%r attempt=%d",
                    op_id, correction_count,
                )
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=False,
                    correction_attempts=correction_count,
                    correction_log=tuple(correction_records),
                    steps=tuple(all_steps),
                    error=llm_step.adapter_error,
                )

            new_code = llm_step.output

            # 3. Overwrite file with corrected code (always modify_file — file exists).
            corr_write_op = self._operation_engine.build_operation(
                id=f"op-write-{corr_prefix}",
                job_id=job_id,
                goal=f"Correction {correction_count}: overwrite {per_op_goal.output_path}",
                operation_type=OperationType.FILESYSTEM,
                sequence_index=1,
                execution_ref=OperationExecutionReference(
                    adapter_id="filesystem",
                    action_id="modify_file",
                ),
            )
            write_context = {"generated_code": new_code}
            write_step = self._execute_step(corr_write_op, per_op_goal, write_context)
            all_steps.append(write_step)

            if not write_step.adapter_success:
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=False,
                    correction_attempts=correction_count,
                    correction_log=tuple(correction_records),
                    steps=tuple(all_steps),
                    error=write_step.adapter_error,
                )

            # 4. Re-run validate.
            corr_validate_op = self._operation_engine.build_operation(
                id=f"op-validate-{corr_prefix}",
                job_id=job_id,
                goal=f"Correction {correction_count}: validate {per_op_goal.output_path}",
                operation_type=OperationType.VALIDATION,
                sequence_index=2,
                execution_ref=OperationExecutionReference(
                    adapter_id="validation",
                    action_id="validate",
                ),
            )
            validate_step = self._execute_step(corr_validate_op, per_op_goal, {})
            all_steps.append(validate_step)

            if not validate_step.adapter_success:
                pending_trigger = "validation_failure"
                pending_error_excerpt = self._extract_error_excerpt(validate_step)
                logger.info(
                    "CorrectionEngine: correction validate failed op_id=%r attempt=%d",
                    op_id, correction_count,
                )
                continue

            # 5. Re-run tests.
            corr_test_op = self._operation_engine.build_operation(
                id=f"op-test-{corr_prefix}",
                job_id=job_id,
                goal=f"Correction {correction_count}: run tests for {per_op_goal.repository_path}",
                operation_type=OperationType.VALIDATION,
                sequence_index=3,
                execution_ref=OperationExecutionReference(
                    adapter_id="validation",
                    action_id="run_tests",
                ),
            )
            test_step = self._execute_step(corr_test_op, per_op_goal, {})
            all_steps.append(test_step)

            if not test_step.adapter_success:
                pending_trigger = "test_failure"
                pending_error_excerpt = self._extract_error_excerpt(test_step)
                logger.info(
                    "CorrectionEngine: correction tests failed op_id=%r attempt=%d",
                    op_id, correction_count,
                )
                continue

            # Both validate and run_tests passed — execute git.add and return success.
            add_op = op_by_id[add_op_id]
            add_step = self._execute_step(add_op, per_op_goal, {})
            all_steps.append(add_step)

            if not add_step.adapter_success:
                return OperationCorrectionResult(
                    operation_id=op_id,
                    success=False,
                    correction_attempts=correction_count,
                    correction_log=tuple(correction_records),
                    steps=tuple(all_steps),
                    error=add_step.adapter_error,
                )

            logger.info(
                "CorrectionEngine: operation succeeded after %d correction(s) op_id=%r",
                correction_count, op_id,
            )
            return OperationCorrectionResult(
                operation_id=op_id,
                success=True,
                correction_attempts=correction_count,
                correction_log=tuple(correction_records),
                steps=tuple(all_steps),
                error=None,
            )

        # Correction limit exhausted.
        logger.warning(
            "CorrectionEngine: repair_limit_exceeded op_id=%r max_corrections=%d",
            op_id, self._config.max_corrections,
        )
        return OperationCorrectionResult(
            operation_id=op_id,
            success=False,
            correction_attempts=correction_count,
            correction_log=tuple(correction_records),
            steps=tuple(all_steps),
            error="repair_limit_exceeded",
        )
