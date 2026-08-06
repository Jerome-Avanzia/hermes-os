"""Engineering Workflow — typed contracts for the first autonomous engineering workflow.

Architecture position:
  Founder Goal
       ↓
  EngineeringWorkflow  (composition layer)
       ↓
  JobEngine + OperationEngine  (planning)
       ↓
  Execution Gateway  (dispatch contracts)
       ↓
  LLM Adapter → Filesystem Adapter → Git Adapter  (execution)

Sprint 64: Hermes' first complete engineering workflow. These contracts
represent the planning and execution record for one Founder Goal executed
as a four-step operation chain:

  LLM generate → Filesystem write → Git add → Git commit

All contracts are immutable. The WorkflowExecutionReport captures the
complete audit trail from goal to committed code.

No new architecture is introduced here. This module only defines the typed
contracts that the EngineeringWorkflow produces during composition.

Sprint 64 scope:
  FounderGoal     → input: what to build, where to write it, which repo to commit
  WorkflowMission → derived from the goal; sets the planning objective
  WorkflowConfig  → runtime configuration (LLM adapter settings, commit message)
  StepExecutionRecord → immutable record of one step (Gateway dispatch + adapter result)
  WorkflowExecutionReport → full audit trail across all steps

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest, ExecutionStatus


# ── FounderGoal ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FounderGoal:
    """Immutable description of a single engineering task from the Founder.

    The Founder provides a goal in plain language. The EngineeringWorkflow
    translates it into a four-step execution plan.

    goal_id         → unique identifier for this goal (used to derive all other IDs)
    description     → plain-language description of what to build (feeds the LLM prompt)
    workspace_path  → absolute path to the workspace root directory
    repository_path → workspace-relative path to the git repository to commit into
    output_path     → workspace-relative path where generated code will be written

    Example::

        goal = FounderGoal(
            goal_id="hello-world-001",
            description="Write a Python function that returns 'Hello, World!'",
            workspace_path="/tmp/hermes-workspace",
            repository_path="my-project",
            output_path="my-project/hello.py",
        )

    Immutable after construction.
    """

    goal_id: str
    description: str
    workspace_path: str
    repository_path: str     # workspace-relative path to git repository root
    output_path: str         # workspace-relative path where generated code is written


# ── WorkflowMission ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkflowMission:
    """Immutable planning objective derived from a FounderGoal.

    Created by the EngineeringWorkflow before JobEngine planning begins.
    Carries the goal forward through the planning phase.

    mission_id → derived from goal_id: "mission-{goal_id}"
    goal_id    → the originating FounderGoal's goal_id
    objective  → copied from FounderGoal.description

    Immutable after construction.
    """

    mission_id: str
    goal_id: str
    objective: str


# ── WorkflowConfig ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    """Immutable runtime configuration for an EngineeringWorkflow instance.

    Carries the LLM adapter configuration and the commit message for the
    generated code. The LLM configuration is passed to LlmAdapter.execute()
    for the code generation step.

    llm_provider         → which LLM provider to use (e.g. LLMProvider.OLLAMA)
    llm_model            → model identifier (e.g. "llama3.2")
    llm_base_url         → provider endpoint (e.g. "http://localhost:11434")
    llm_api_key          → authentication token; empty string for local providers
    llm_max_tokens       → maximum completion tokens
    llm_timeout_seconds  → HTTP timeout for LLM calls
    commit_message       → git commit message for the generated code
    write_mode           → filesystem action for the write step: "create_file" (default,
                           fails if file exists) or "overwrite_file" (create or replace).
                           Bootstrap Phase 1 only — set by implement.py based on whether
                           the target file already exists.

    Immutable after construction.
    """

    llm_provider: object           # LLMProvider enum value (typed loosely to avoid circular)
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    llm_max_tokens: int
    llm_timeout_seconds: int
    commit_message: str
    write_mode: str = "create_file"  # "create_file" or "overwrite_file" — Bootstrap Phase 1


# ── StepExecutionRecord ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StepExecutionRecord:
    """Immutable record of a single workflow step execution.

    Each step follows the same lifecycle:
      1. Build ExecutionRequest via the Gateway.
      2. gateway.dispatch() → dispatch contract (DISPATCHED or failure).
      3. If DISPATCHED: invoke the adapter.
      4. Record outcome here.

    The Gateway dispatch always precedes adapter invocation. If dispatch fails,
    adapter_success=False and the adapter was never called.

    step_id          → unique identifier for this step ("step-{operation_id}")
    operation_id     → the OperationDefinition being executed
    adapter_type     → which ExecutionAdapter was targeted
    action_id        → the specific action within the adapter
    execution_request → the ExecutionRequest built and dispatched through the Gateway
    dispatch_status  → the ExecutionStatus returned by gateway.dispatch()
    adapter_success  → True if the adapter executed successfully; False otherwise
    adapter_error    → error description if adapter_success=False; None otherwise
    output           → adapter output extracted for downstream use:
                       LLM step: generated code content
                       Filesystem step: path written (empty string)
                       Git steps: empty string

    Immutable after construction.
    """

    step_id: str
    operation_id: str
    adapter_type: ExecutionAdapter
    action_id: str
    execution_request: ExecutionRequest
    dispatch_status: ExecutionStatus
    adapter_success: bool
    adapter_error: str | None
    output: str                  # content (LLM) or "" (other adapters)


# ── WorkflowExecutionReport ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkflowExecutionReport:
    """Immutable audit trail of a complete engineering workflow execution.

    Produced by EngineeringWorkflow.execute(). Contains the complete record
    of every planning and execution step from Founder Goal to committed code.

    report_id          → unique identifier ("report-{goal_id}")
    goal_id            → the originating FounderGoal's goal_id
    mission_id         → the WorkflowMission's mission_id
    job_id             → the JobDefinition's job_id
    steps              → ordered tuple of StepExecutionRecords (LLM, FS, Git, Git)
    success            → True if all four steps completed successfully
    error              → description of the first failure; None if success=True
    execution_sequence → ordered tuple of adapter type values that were invoked
                         (e.g. ("llm", "filesystem", "git", "git"))
    metadata           → sorted tuple of key-value pairs summarising the run

    Immutable after construction.
    """

    report_id: str
    goal_id: str
    mission_id: str
    job_id: str
    steps: tuple[StepExecutionRecord, ...]
    success: bool
    error: str | None
    execution_sequence: tuple[str, ...]   # adapter type values in execution order
    metadata: tuple[tuple[str, str], ...]  # sorted (key, value) pairs


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "FounderGoal",
    "WorkflowConfig",
    "WorkflowExecutionReport",
    "WorkflowMission",
    "StepExecutionRecord",
]
