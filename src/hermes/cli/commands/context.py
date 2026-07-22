import typer

from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models import Task
from hermes.runtime.context_engine import ContextEngine


def _format_status(is_clean: bool | None) -> str:
    if is_clean is None:
        return "-"
    return "clean" if is_clean else "dirty"


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

    typer.echo(f"Task: {task}")
    typer.echo(f"Project: {result.project.name} ({result.project.id})")
    typer.echo(f"Knowledge documents: {len(result.knowledge.documents)}")
    typer.echo(f"Workspace: {result.workspace.workspace.path}")
    typer.echo(f"Git repository: {result.workspace.is_git_repo}")
    typer.echo(f"Branch: {result.workspace.branch or '-'}")
    typer.echo(f"Status: {_format_status(result.workspace.is_clean)}")
    typer.echo(f"Capabilities: {', '.join(result.workspace.capabilities) or '-'}")
