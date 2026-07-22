import typer

from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError


def _format_status(is_clean: bool | None) -> str:
    if is_clean is None:
        return "-"
    return "clean" if is_clean else "dirty"


def inspect(
    project: str = typer.Argument(..., help="Project id, e.g. AVANZIA"),
) -> None:
    """Display a combined overview: project, workspace, git status, environment, and knowledge."""
    try:
        knowledge_context = KnowledgeEngine().load(project)
        workspace_context = WorkspaceEngine().resolve(project)
    except (ValueError, WorkspaceNotFoundError, FileNotFoundError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(
        f"Project: {knowledge_context.project.name} ({knowledge_context.project.id})"
    )
    typer.echo(f"Workspace: {workspace_context.workspace.path}")
    typer.echo(f"Exists: {workspace_context.exists}")
    typer.echo(f"Git repository: {workspace_context.is_git_repo}")
    typer.echo(f"Branch: {workspace_context.branch or '-'}")
    typer.echo(f"Status: {_format_status(workspace_context.is_clean)}")
    typer.echo(f"Environment: {', '.join(workspace_context.environment) or '-'}")
    typer.echo(f"Knowledge documents: {len(knowledge_context.documents)}")
