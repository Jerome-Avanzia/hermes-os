import typer

from hermes.kernel.knowledge_engine import KnowledgeEngine


def knowledge(
    project: str = typer.Argument(..., help="Project id, e.g. AVANZIA"),
) -> None:
    """List available knowledge documents for a project."""
    try:
        context = KnowledgeEngine().load(project)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Project: {context.project.name} ({context.project.id})")
    typer.echo(f"Documents: {len(context.documents)}")
    for document in context.documents:
        typer.echo(f"  - {document.id}: {document.title}")
