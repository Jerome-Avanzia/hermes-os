"""Bootstrap Phase 1 — hermes implement <task> CLI command.

Composes existing Hermes components into a user-facing engineering command.

Architecture:
  hermes implement "<task>" --output <path> [--repo <path>]
       ↓
  RepositoryIntelligence.scan()   → RepositorySnapshot (what is in the repo)
       ↓
  _snapshot_to_context()          → compact LLM-readable context string
       ↓
  configure_from_env()            → OllamaEnvConfig + WorkflowConfig
       ↓
  EngineeringWorkflow.execute()   → WorkflowExecutionReport
       ↓
  stdout: engineering report

Bootstrap Phase 1 constraints:
  - --output is required: the Founder specifies the target file.
  - --repo defaults to "." (current directory as the git repository).
  - write_mode is selected automatically: "overwrite_file" if the target
    path already exists, "create_file" otherwise.

Long-term target (Phase 2+):
  - --output is removed.
  - Ollama selects target files autonomously from the RepositorySnapshot
    via structured JSON output + RepositoryManipulationPlan validation.
  - The Founder experience becomes: hermes implement "<task>"

No new architecture is introduced here. Every component used already exists.
This module's sole responsibility is Bootstrap Phase 1 orchestration.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import typer

from hermes.adapters.filesystem_adapter import FilesystemAdapter
from hermes.adapters.git_adapter import GitAdapter
from hermes.adapters.llm_adapter import LlmAdapter
from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.kernel.job_engine import JobEngine
from hermes.kernel.operation_engine import OperationEngine
from hermes.kernel.repository_intelligence import RepositoryIntelligence
from hermes.kernel.skill_registry import SkillRegistry
from hermes.models.engineering_workflow import FounderGoal, WorkflowConfig
from hermes.models.execution_gateway import AdapterRegistration, ExecutionAdapter
from hermes.models.llm_adapter import LLMProvider
from hermes.models.repository_intelligence import RepositorySnapshot
from hermes.providers.ollama_driver import configure_from_env
from hermes.workflows.engineering_workflow import EngineeringWorkflow


def implement(
    task: str = typer.Argument(..., help="Engineering task in plain language"),
    output: str = typer.Option(
        ...,
        "--output",
        "-o",
        help=(
            "[Bootstrap v1] Workspace-relative path for the output file. "
            "Future: Hermes selects target files autonomously."
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

    Bootstrap Phase 1: reads the repository, builds context, asks Ollama to
    generate code, writes the file, and commits the result.

    Example:

        hermes implement "Add input validation to the user endpoint" --output src/api.py
    """
    workspace_root = Path.cwd()

    # ── 1. Scan repository with RepositoryIntelligence ────────────────────────
    try:
        ri_engine = RepositoryIntelligence(workspace_root)
        snapshot = ri_engine.scan(repo)
    except Exception as exc:
        typer.echo(f"Error: failed to scan repository — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    context_str = _snapshot_to_context(snapshot)

    # ── 2. Configure Ollama from environment ──────────────────────────────────
    try:
        env_cfg, capabilities, driver = configure_from_env()
    except Exception as exc:
        typer.echo(f"Error: failed to read Ollama configuration — {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # ── 3. Select write mode: overwrite if file exists, create otherwise ───────
    output_abs = workspace_root / output
    write_mode = "overwrite_file" if output_abs.exists() else "create_file"

    config = WorkflowConfig(
        llm_provider=LLMProvider.OLLAMA,
        llm_model=capabilities.default_model,
        llm_base_url=env_cfg.base_url,
        llm_api_key=env_cfg.api_key,
        llm_max_tokens=4096,
        llm_timeout_seconds=120,
        commit_message=f"feat: {task[:72]}",
        write_mode=write_mode,
    )

    # ── 4. Build FounderGoal with repository context injected into description ─
    goal_id = str(uuid.uuid4())[:8]
    enriched_description = (
        f"{task}\n\n"
        f"Repository context:\n"
        f"{context_str}"
    )

    goal = FounderGoal(
        goal_id=goal_id,
        description=enriched_description,
        workspace_path=str(workspace_root),
        repository_path=repo,
        output_path=output,
    )

    # ── 5. Wire adapters and gateway ──────────────────────────────────────────
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

    llm_adapter = LlmAdapter()
    llm_adapter.register_provider(LLMProvider.OLLAMA, capabilities, driver=driver)

    fs_adapter = FilesystemAdapter(workspace_root=workspace_root)
    git_adapter = GitAdapter(workspace_root=workspace_root)
    job_engine = JobEngine(registry=SkillRegistry())
    op_engine = OperationEngine()

    workflow = EngineeringWorkflow(
        gateway=gateway,
        llm_adapter=llm_adapter,
        filesystem_adapter=fs_adapter,
        git_adapter=git_adapter,
        job_engine=job_engine,
        operation_engine=op_engine,
        config=config,
    )

    # ── 6. Execute ────────────────────────────────────────────────────────────
    typer.echo(f"Implementing: {task}")
    typer.echo(f"Output:       {output}")
    typer.echo(f"Repository:   {repo}")
    typer.echo(f"Model:        {config.llm_model}  ({env_cfg.mode.value})")
    typer.echo("")

    report = workflow.execute(goal)

    # ── 7. Print report ───────────────────────────────────────────────────────
    _print_report(report)

    if not report.success:
        raise typer.Exit(code=1)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _snapshot_to_context(snapshot: RepositorySnapshot) -> str:
    """Serialise a RepositorySnapshot into a compact LLM-readable context string.

    Produces a deterministic, token-efficient summary of the repository
    structure for injection into the Ollama code generation prompt. Limits
    source file listing to 30 files to stay within typical context budgets.
    """
    lines: list[str] = []

    if snapshot.primary_language:
        lines.append(f"Primary language: {snapshot.primary_language}")

    if snapshot.languages:
        lang_summary = ", ".join(
            f"{lang.language} ({lang.file_count})"
            for lang in snapshot.languages[:6]
        )
        lines.append(f"Languages: {lang_summary}")

    if snapshot.build_system:
        bs = snapshot.build_system
        lines.append(
            f"Build system: {bs.name} ({bs.config_file})"
            f"  |  build: {bs.build_command}  |  test: {bs.test_command}"
        )

    if snapshot.entry_points:
        ep_summary = ", ".join(
            f"{e.path} [{e.kind}]"
            for e in snapshot.entry_points[:6]
        )
        lines.append(f"Entry points: {ep_summary}")

    if snapshot.test_locations:
        tl_summary = ", ".join(
            f"{t.path} [{t.framework}, {t.file_count} files]"
            for t in snapshot.test_locations[:4]
        )
        lines.append(f"Test locations: {tl_summary}")

    # Source files — capped at 30 to avoid prompt bloat
    _SOURCE_EXTS = frozenset({
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx",
        ".rs", ".go", ".java", ".kt", ".rb", ".cs",
        ".c", ".cpp", ".h", ".hpp", ".swift", ".ex", ".exs",
    })
    source_files = [
        f.path for f in snapshot.files
        if f.extension in _SOURCE_EXTS and not f.is_directory
    ][:30]

    if source_files:
        lines.append(f"Source files ({len(source_files)} shown of {snapshot.file_count} total):")
        for path in source_files:
            lines.append(f"  {path}")

    lines.append(
        f"Repository: {snapshot.file_count} files, "
        f"{snapshot.directory_count} directories"
        + ("  |  git: yes" if snapshot.git_present else "")
    )

    return "\n".join(lines)


def _print_report(report) -> None:  # type: ignore[no-untyped-def]
    """Print a WorkflowExecutionReport to stdout."""
    status = "✓ SUCCESS" if report.success else "✗ FAILED"
    typer.echo(f"Status: {status}")
    typer.echo("")

    for step in report.steps:
        step_status = "✓" if step.adapter_success else "✗"
        typer.echo(f"  {step_status}  {step.adapter_type.value:<12}  {step.action_id}")

    if not report.success and report.error:
        typer.echo("")
        typer.echo(f"Error: {report.error}")

    typer.echo("")
    for key, value in report.metadata:
        typer.echo(f"  {key}: {value}")
