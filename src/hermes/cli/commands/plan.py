import typer

from hermes.kernel.planner import Planner
from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models import Task
from hermes.runtime.context_engine import ContextEngine


def plan(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
) -> None:
    """Build a deterministic execution plan for a task."""
    hermes_task = Task(id="cli-plan", business="", request=task)

    try:
        context = ContextEngine().build(hermes_task)
    except (
        ProjectNotFoundError,
        ValueError,
        WorkspaceNotFoundError,
        FileNotFoundError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    execution_plan = Planner().create(context)

    typer.echo(f"Task: {task}")
    typer.echo(f"Project: {context.project}")
    typer.echo("Steps:")
    for index, step in enumerate(execution_plan.steps, start=1):
        label = step.capability_id or "approval"
        typer.echo(f"  {index}. [{label}] {step.description}")
