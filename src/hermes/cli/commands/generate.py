import typer

from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.skill_loader import SkillNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.providers.claude_provider import ClaudeConfigurationError, ClaudeProvider
from hermes.service import HermesService


def generate(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
) -> None:
    """Ask Claude to draft a proposal for a task, grounded in Hermes' deterministic context."""
    try:
        provider = ClaudeProvider()
        result = HermesService().generate(task, provider=provider)
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
