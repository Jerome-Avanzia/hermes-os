import typer

from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError
from hermes.kernel.workspace_reader import WorkspaceReader


def read(
    project: str = typer.Argument(..., help="Project id, e.g. AVANZIA"),
) -> None:
    """Read a read-only snapshot of a project's workspace files."""
    try:
        workspace_context = WorkspaceEngine().resolve(project)
    except (WorkspaceNotFoundError, FileNotFoundError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    snapshot = WorkspaceReader().read(workspace_context)

    typer.echo("Project:")
    typer.echo(workspace_context.workspace.project_id)
    typer.echo("")
    typer.echo("Workspace Snapshot")
    typer.echo("")
    typer.echo("Files read:")
    typer.echo(str(len(snapshot.files)))
    typer.echo("")

    if snapshot.files:
        largest = max(snapshot.files, key=lambda file: file.size)
        typer.echo("Largest file:")
        typer.echo(largest.path)
        typer.echo("")

    typer.echo("Extensions")
    typer.echo("")
    for extension in sorted({file.extension for file in snapshot.files}):
        typer.echo(extension)
