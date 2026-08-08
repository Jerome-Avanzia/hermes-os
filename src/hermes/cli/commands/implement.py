"""Bootstrap Phase 7 — hermes implement <task> CLI command.

Thin wiring layer. All orchestration lives in EngineeringCoordinator.

Architecture:
  hermes implement "<task>" [--output <path>] [--repo <path>]
       ↓
  RepositoryIntelligence.scan()   → RepositorySnapshot (what is in the repo)
       ↓
  RepositorySnapshot              → passed to EngineeringCoordinator for planning context
       ↓
  configure_from_env()            → OllamaEnvConfig + WorkflowConfig
       ↓
  EngineeringCoordinator.execute() → WorkflowExecutionReport
       ↓
  stdout: engineering report

Autonomous mode (--output omitted): EngineeringCoordinator calls
EngineeringPlanner to decompose the goal, then EngineeringWorkflow executes
each operation and commits once at the end.

Deterministic mode (--output set): EngineeringCoordinator builds a
single-operation plan and calls EngineeringWorkflow directly.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

import time

import typer

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.adapters.validation_adapter import ValidationAdapter
from hermes.kernel.correction_engine import CorrectionEngine
from hermes.kernel.engineering_coordinator import EngineeringCoordinator
from hermes.kernel.engineering_planner import EngineeringPlanner
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.repository_intelligence import RepositoryIntelligence
from hermes.models.engineering_workflow import FounderGoal, WorkflowConfig
from hermes.models.execution_gateway import AdapterRegistration, ExecutionAdapter
from hermes.models.llm_adapter import AdapterConfiguration, LLMProvider
from hermes.models.repository_intelligence import RepositorySnapshot
from hermes.providers.ollama_driver import configure_from_env
from hermes.workflows.engineering_workflow import EngineeringWorkflow


def implement(
    task: str = typer.Argument(..., help="Engineering task in plain language"),
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Workspace-relative path for the output file. "
            "When omitted, Hermes selects and decomposes the task autonomously (Phase 6+)."
        ),
    ),
    repo: str = typer.Option(
        ".",
        "--repo",
        "-r",
        help="Workspace-relative path to the git repository (default: current directory).",
    ),
) -> None:
    """Implement an engineering task using Ollama and the Hermes workflow engine.

    Phase 6: reads the repository, builds context, asks Ollama to plan a
    multi-operation engineering plan, executes each operation, and commits once.

    Example:

        hermes implement "Add input validation to the user endpoint"
        hermes implement "Add input validation to the user endpoint" --output src/api.py
    """
    _hermes_repos = os.environ.get("HERMES_REPOSITORIES", "").strip()
    workspace_root = Path(_hermes_repos) if _hermes_repos else Path.cwd()

    # ── 1. Scan repository with RepositoryIntelligence ────────────────────────
    try:
        ri_engine = RepositoryIntelligence(workspace_root)
        snapshot = ri_engine.scan(repo)
    except Exception as exc:
        typer.echo(f"Error: failed to scan repository — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # ── 2. Configure Ollama from environment ──────────────────────────────────
    try:
        env_cfg, capabilities, driver = configure_from_env()
    except Exception as exc:
        typer.echo(f"Error: failed to read Ollama configuration — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    test_command = (
        snapshot.build_system.test_command.strip()
        if snapshot.build_system and snapshot.build_system.test_command.strip()
        else ""
    )

    config = WorkflowConfig(
        llm_provider=LLMProvider.OLLAMA,
        llm_model=capabilities.default_model,
        llm_base_url=env_cfg.base_url,
        llm_api_key=env_cfg.api_key,
        llm_max_tokens=4096,
        llm_timeout_seconds=120,
        commit_message=f"feat: {task[:72]}",
        test_command=test_command,
    )

    # ── 3. Build FounderGoal ──────────────────────────────────────────────────
    goal_id = str(uuid.uuid4())[:8]

    # output_path="" → autonomous mode; coordinator/planner derives the file(s).
    # goal.description is the task only — repository context flows via the
    # RepositorySnapshot passed formally to EngineeringCoordinator.execute().
    goal = FounderGoal(
        goal_id=goal_id,
        description=task,
        workspace_path=str(workspace_root),
        repository_path=repo,
        output_path=output if output is not None else "",
    )

    # ── 4. Wire adapters and gateway ──────────────────────────────────────────
    gateway = ExecutionGateway()
    gateway.register(AdapterRegistration(
        adapter=ExecutionAdapter.LLM,
        adapter_id="llm-ollama",
        available=True,
        description="Ollama LLM provider (local or cloud)",
    ))
    gateway.register(AdapterRegistration(
        adapter=ExecutionAdapter.FILESYSTEM,
        adapter_id="filesystem-workspace",
        available=True,
        description="Workspace filesystem adapter",
    ))
    gateway.register(AdapterRegistration(
        adapter=ExecutionAdapter.GIT,
        adapter_id="git-workspace",
        available=True,
        description="Workspace git adapter",
    ))
    gateway.register(AdapterRegistration(
        adapter=ExecutionAdapter.VALIDATION,
        adapter_id="validation-workspace",
        available=True,
        description="Workspace validation adapter (syntax gate)",
    ))

    llm_adapter = LlmAdapter()
    llm_adapter.register_provider(LLMProvider.OLLAMA, capabilities, driver=driver)

    fs_adapter = FilesystemAdapter(workspace_root=workspace_root)
    git_adapter = GitAdapter(workspace_root=workspace_root)
    validation_adapter = ValidationAdapter(workspace_root=workspace_root)
    op_engine = OperationEngine()

    # Build LLM config for the planner
    llm_config = AdapterConfiguration(
        provider=LLMProvider.OLLAMA,
        model=capabilities.default_model,
        base_url=env_cfg.base_url,
        api_key=env_cfg.api_key,
        max_tokens=4096,
        timeout_seconds=120,
        temperature=0.0,
    )

    correction_engine = CorrectionEngine(
        gateway=gateway,
        llm_adapter=llm_adapter,
        filesystem_adapter=fs_adapter,
        git_adapter=git_adapter,
        validation_adapter=validation_adapter,
        operation_engine=op_engine,
        config=config,
    )

    workflow = EngineeringWorkflow(
        gateway=gateway,
        git_adapter=git_adapter,
        operation_engine=op_engine,
        config=config,
        correction_engine=correction_engine,
    )

    planner = EngineeringPlanner(
        gateway=gateway,
        llm_adapter=llm_adapter,
        llm_config=llm_config,
        operation_engine=op_engine,
    )

    coordinator = EngineeringCoordinator(
        planner=planner,
        workflow=workflow,
    )

    # ── 5. Execute ────────────────────────────────────────────────────────────
    typer.echo(f"Implementing: {task}")
    typer.echo(f"Output:       {output if output is not None else '(autonomous)'}")
    typer.echo(f"Repository:   {repo}")
    typer.echo(f"Model:        {config.llm_model}  ({env_cfg.mode.value})")
    typer.echo("")

    start_time = time.monotonic()
    report = coordinator.execute(goal, snapshot)
    elapsed = time.monotonic() - start_time

    # ── 6. Print report ───────────────────────────────────────────────────────
    _print_report(report, elapsed_seconds=elapsed)

    if not report.success:
        raise typer.Exit(code=1)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _print_report(report, *, elapsed_seconds: float) -> None:  # type: ignore[no-untyped-def]
    """Print a WorkflowExecutionReport to stdout."""
    status = "SUCCESS" if report.success else "FAILED"
    typer.echo(f"Status: {status}")
    typer.echo("")

    for step in report.steps:
        step_status = "+" if step.adapter_success else "!"
        typer.echo(f"  {step_status}  {step.adapter_type.value:<12}  {step.action_id}")

    if not report.success and report.error:
        typer.echo("")
        typer.echo(f"Error: {report.error}")

    typer.echo("")
    metadata_dict = dict(report.metadata)
    correction_attempts = int(metadata_dict.get("correction_attempts", "0"))
    if correction_attempts > 0:
        typer.echo(f"  correction_attempts: {correction_attempts}")

    for key, value in report.metadata:
        if key == "correction_attempts":
            continue  # already printed above with emphasis
        typer.echo(f"  {key}: {value}")

    typer.echo(f"\n  Total execution time: {elapsed_seconds:.1f}s")
