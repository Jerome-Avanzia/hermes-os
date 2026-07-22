import typer

from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError


def _format_status(is_clean: bool | None) -> str:
    if is_clean is None:
        return "-"
    return "clean" if is_clean else "dirty"


def workspace(
    project: str = typer.Argument(..., help="Project id, e.g. AVANZIA"),
) -> None:
    """Display workspace information for a project."""
    try:
        context = WorkspaceEngine().resolve(project)
    except (WorkspaceNotFoundError, FileNotFoundError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Workspace: {context.workspace.project_id}")
    typer.echo(f"Path: {context.workspace.path}")
    typer.echo(f"Exists: {context.exists}")
    typer.echo(f"Git repository: {context.is_git_repo}")
    typer.echo(f"Branch: {context.branch or '-'}")
    typer.echo(f"Status: {_format_status(context.is_clean)}")
    typer.echo(f"Environment: {', '.join(context.environment) or '-'}")
