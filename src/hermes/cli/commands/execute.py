import typer

from hermes.kernel.executor import Executor
from hermes.kernel.planner import Planner
from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.skill_loader import SkillLoader, SkillNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models import Task
from hermes.runtime.context_engine import ContextEngine


def execute(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
) -> None:
    """Execute a deterministic plan for a task up to the approval checkpoint."""
    hermes_task = Task(id="cli-execute", business="", request=task)

    try:
        context = ContextEngine().build(hermes_task)
        execution_plan = Planner().create(context)
        loaded_skills = SkillLoader().load(execution_plan)
        result = Executor().execute(execution_plan, loaded_skills)
    except (
        ProjectNotFoundError,
        ValueError,
        WorkspaceNotFoundError,
        SkillNotFoundError,
        FileNotFoundError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo("Project:")
    typer.echo(result.project.id)
    typer.echo("")
    typer.echo("Execution")
    typer.echo("")

    for title in result.completed_steps:
        typer.echo(f"✓ {title}")
        typer.echo("")

    pending_index = len(result.completed_steps)
    if pending_index < len(execution_plan.steps):
        pending_step = execution_plan.steps[pending_index]
        typer.echo(f"⏸ {pending_step.description}")
        typer.echo("")

    typer.echo("Status:")
    typer.echo(result.status)
