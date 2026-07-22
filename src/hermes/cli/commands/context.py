import typer

from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models import Task
from hermes.runtime.context_engine import ContextEngine


def context(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
) -> None:
    """Resolve project, knowledge, and workspace for a task, and print a summary."""
    hermes_task = Task(id="cli-context", business="", request=task)

    try:
        result = ContextEngine().build(hermes_task)
    except (
        ProjectNotFoundError,
        ValueError,
        WorkspaceNotFoundError,
        FileNotFoundError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    required_capabilities = (
        ", ".join(capability.id for capability in result.capabilities) or "-"
    )

    typer.echo(f"Task: {task}")
    typer.echo(f"Project: {result.project}")
    typer.echo(f"Knowledge: {result.knowledge}")
    typer.echo(f"Workspace: {result.workspace}")
    typer.echo(f"Environment: {', '.join(result.workspace.environment) or '-'}")
    typer.echo(f"Required Capabilities: {required_capabilities}")
