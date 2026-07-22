import typer

from hermes.kernel.executor import Executor
from hermes.kernel.planner import Planner
from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.skill_loader import SkillLoader, SkillNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.kernel.workspace_reader import WorkspaceReader
from hermes.models import Task
from hermes.providers.claude_provider import ClaudeConfigurationError, ClaudeProvider
from hermes.runtime.context_engine import ContextEngine


def generate(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
) -> None:
    """Ask Claude to draft a proposal for a task, grounded in Hermes' deterministic context."""
    hermes_task = Task(id="cli-generate", business="", request=task)

    try:
        context = ContextEngine().build(hermes_task)
        workspace_snapshot = WorkspaceReader().read(context.workspace)
        execution_plan = Planner().create(context)
        loaded_skills = SkillLoader().load(execution_plan)
        provider = ClaudeProvider()
        result = Executor().execute(
            execution_plan, loaded_skills, workspace_snapshot, provider=provider
        )
    except (
        ProjectNotFoundError,
        ValueError,
        WorkspaceNotFoundError,
        SkillNotFoundError,
        ClaudeConfigurationError,
        FileNotFoundError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(result.generated_output or "")
