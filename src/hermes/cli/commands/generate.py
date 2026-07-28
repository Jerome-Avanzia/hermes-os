import typer

from hermes.kernel.project_resolver import ProjectNotFoundError
from hermes.kernel.skill_loader import SkillNotFoundError
from hermes.kernel.workspace_engine import WorkspaceNotFoundError
from hermes.models.diagnostics_report import DiagnosticsReport
from hermes.providers.claude_provider import ClaudeConfigurationError, ClaudeProvider
from hermes.service import HermesService


def _format_diagnostics(report: DiagnosticsReport) -> str:
    lines: list[str] = []

    lines.append("Project")
    lines.append("--------")
    lines.append("")
    lines.append(report.project_id)
    lines.append("")

    lines.append("Repositories")
    lines.append("------------")
    lines.append("")
    if report.repositories:
        for repo in report.repositories:
            lines.append(f"✓ {repo}")
    else:
        lines.append("-")
    lines.append("")

    lines.append("Knowledge")
    lines.append("---------")
    lines.append("")
    if report.knowledge_documents:
        for title in report.knowledge_documents:
            lines.append(f"✓ {title}")
    else:
        lines.append("-")
    lines.append("")

    lines.append("Selected files")
    lines.append("--------------")
    lines.append("")
    if report.files_selected:
        import os
        for path in report.files_selected:
            lines.append(f"✓ {os.path.basename(path)}")
    else:
        lines.append("-")
    lines.append("")

    lines.append("Content")
    lines.append("")
    lines.append(f"{report.files_read} files")
    lines.append("")
    lines.append(f"{report.chars_read:,} chars")
    if report.chars_truncated:
        lines.append(f"{report.chars_truncated:,} chars truncated")
    lines.append("")

    lines.append("Prompt")
    lines.append("")
    lines.append(f"Knowledge:\n{report.knowledge_chars:,} chars")
    lines.append("")
    lines.append(f"Files:\n{report.file_content_chars:,} chars")
    lines.append("")
    lines.append(f"Prompt total:\n{report.prompt_chars:,} chars")

    return "\n".join(lines)


def generate(
    task: str = typer.Argument(
        ..., help="Free-text task, e.g. 'Update the AVANZIA homepage copy'"
    ),
    diagnostics: bool = typer.Option(
        False, "--diagnostics", help="Print context diagnostics after generation."
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

    if diagnostics and result.diagnostics:
        typer.echo("")
        typer.echo(_format_diagnostics(result.diagnostics))
