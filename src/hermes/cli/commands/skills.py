import typer

from hermes.kernel.planner import Planner
from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.skill_loader import SkillLoader, SkillNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models import Task
from hermes.runtime.context_engine import ContextEngine


def skills(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
) -> None:
    """Resolve required capabilities for a task and load the skills that satisfy them."""
    hermes_task = Task(id="cli-skills", business="", request=task)

    try:
        context = ContextEngine().build(hermes_task)
        execution_plan = Planner().create(context)
        loaded_skills = SkillLoader().load(execution_plan)
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
    typer.echo(context.project.id)
    typer.echo("")
    typer.echo("Required Capabilities")
    typer.echo("")
    for capability in context.capabilities:
        typer.echo(f"- {capability.id}")
    typer.echo("")
    typer.echo("Loaded Skills")
    typer.echo("")
    for skill in loaded_skills:
        typer.echo(f"- {skill.name}")
