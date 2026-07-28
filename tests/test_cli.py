from typer.testing import CliRunner

from hermes.cli.main import app

runner = CliRunner()


def test_inspect_known_project():
    result = runner.invoke(app, ["inspect", "AVANZIA"])

    assert result.exit_code == 0
    assert "Project: AVANZIA" in result.stdout
    assert "Knowledge documents: 12" in result.stdout


def test_inspect_unknown_project():
    result = runner.invoke(app, ["inspect", "NONEXISTENT"])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_workspace_known_project():
    result = runner.invoke(app, ["workspace", "AVANZIA"])

    assert result.exit_code == 0
    assert "Git repository: True" in result.stdout
    assert "node" in result.stdout


def test_knowledge_known_project():
    result = runner.invoke(app, ["knowledge", "AVANZIA"])

    assert result.exit_code == 0
    assert "Documents: 12" in result.stdout
    assert "01-purpose" in result.stdout


def test_context_resolves_project_from_task_text():
    result = runner.invoke(app, ["context", "Update the AVANZIA homepage copy"])

    assert result.exit_code == 0
    assert "Project: Project(id=AVANZIA" in result.stdout
    assert "Knowledge: Knowledge(project=AVANZIA, documents=12)" in result.stdout
    assert "Required Capabilities: copywriting" in result.stdout


def test_context_output_does_not_print_document_content():
    result = runner.invoke(app, ["context", "Update the AVANZIA homepage copy"])

    assert result.exit_code == 0
    assert "AVANZIA exists to help entrepreneurs" not in result.stdout


def test_context_unknown_project_exits_nonzero():
    result = runner.invoke(app, ["context", "Do something unrelated"])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_plan_for_homepage_copy_task():
    result = runner.invoke(app, ["plan", "Write homepage copy for AVANZIA"])

    assert result.exit_code == 0
    assert "[copywriting] Apply capability: Copywriting" in result.stdout
    assert "[approval] Await user approval" in result.stdout


def test_plan_unknown_project_exits_nonzero():
    result = runner.invoke(app, ["plan", "Do something unrelated"])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_skills_for_python_task():
    result = runner.invoke(app, ["skills", "AVANZIA: refactor the Python backend"])

    assert result.exit_code == 0
    assert "Required Capabilities" in result.stdout
    assert "- python" in result.stdout
    assert "Loaded Skills" in result.stdout
    assert "- Python" in result.stdout


def test_skills_unknown_project_exits_nonzero():
    result = runner.invoke(app, ["skills", "Do something unrelated"])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_execute_for_python_task_stops_at_approval():
    result = runner.invoke(app, ["execute", "AVANZIA: refactor the Python backend"])

    assert result.exit_code == 0
    assert "✓ Python" in result.stdout
    assert "⏸ Await user approval" in result.stdout
    assert "Status:" in result.stdout
    assert "awaiting_approval" in result.stdout


def test_execute_unknown_project_exits_nonzero():
    result = runner.invoke(app, ["execute", "Do something unrelated"])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_read_known_project():
    result = runner.invoke(app, ["read", "AVANZIA"])

    assert result.exit_code == 0
    assert "Files read:" in result.stdout
    assert "Extensions" in result.stdout
    assert ".tsx" in result.stdout or ".ts" in result.stdout


def test_read_unknown_project_exits_nonzero():
    result = runner.invoke(app, ["read", "NONEXISTENT"])

    assert result.exit_code == 1
    assert "Error" in result.output


def test_generate_missing_api_key_exits_cleanly(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(
        app, ["generate", "AVANZIA: refactor the Python backend"]
    )

    assert result.exit_code == 1
    assert "Error" in result.output
    assert "ANTHROPIC_API_KEY" in result.output


# --- --diagnostics flag (no API key needed — flag is independent of generation) ---

def test_diagnostics_flag_missing_api_key_exits_with_error(monkeypatch):
    """--diagnostics does not bypass the API key requirement."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(
        app, ["generate", "AVANZIA: refactor the Python backend", "--diagnostics"]
    )

    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


# --- DiagnosticsReport formatter ---

from hermes.cli.commands.generate import _format_diagnostics
from hermes.models import DiagnosticsReport


def _sample_report(**overrides) -> DiagnosticsReport:
    defaults = dict(
        project_id="AVANZIA",
        repositories=["hermes-os", "avanzia-website"],
        knowledge_documents=["Vision.md", "Mission.md", "Engineering.md"],
        files_scanned=120,
        files_selected=["executor.py", "context_engine.py", "claude_provider.py"],
        files_read=3,
        chars_read=30_775,
        chars_truncated=0,
        knowledge_chars=7_200,
        file_content_chars=30_775,
        prompt_chars=37_975,
    )
    defaults.update(overrides)
    return DiagnosticsReport(**defaults)


def test_diagnostics_format_contains_project():
    output = _format_diagnostics(_sample_report())
    assert "AVANZIA" in output


def test_diagnostics_format_contains_project_section_header():
    output = _format_diagnostics(_sample_report())
    assert "Project" in output


def test_diagnostics_format_contains_repositories():
    output = _format_diagnostics(_sample_report())
    assert "hermes-os" in output
    assert "avanzia-website" in output


def test_diagnostics_format_contains_knowledge_documents():
    output = _format_diagnostics(_sample_report())
    assert "Vision.md" in output
    assert "Mission.md" in output
    assert "Engineering.md" in output


def test_diagnostics_format_contains_selected_files():
    output = _format_diagnostics(_sample_report())
    assert "executor.py" in output
    assert "context_engine.py" in output
    assert "claude_provider.py" in output


def test_diagnostics_format_shows_files_read_count():
    output = _format_diagnostics(_sample_report(files_read=15))
    assert "15 files" in output


def test_diagnostics_format_shows_chars_read():
    output = _format_diagnostics(_sample_report(chars_read=30_775))
    assert "30,775 chars" in output


def test_diagnostics_format_shows_truncated_chars_when_nonzero():
    output = _format_diagnostics(_sample_report(chars_truncated=5_000))
    assert "5,000 chars truncated" in output


def test_diagnostics_format_omits_truncated_line_when_zero():
    output = _format_diagnostics(_sample_report(chars_truncated=0))
    assert "truncated" not in output


def test_diagnostics_format_shows_knowledge_size():
    output = _format_diagnostics(_sample_report(knowledge_chars=7_200))
    assert "7,200 chars" in output


def test_diagnostics_format_shows_file_content_size():
    output = _format_diagnostics(_sample_report(file_content_chars=30_775))
    assert "30,775 chars" in output


def test_diagnostics_format_shows_prompt_total():
    output = _format_diagnostics(_sample_report(prompt_chars=37_975))
    assert "37,975 chars" in output


def test_diagnostics_format_shows_checkmarks_for_repositories():
    output = _format_diagnostics(_sample_report())
    assert "✓ hermes-os" in output


def test_diagnostics_format_shows_dash_for_empty_repositories():
    output = _format_diagnostics(_sample_report(repositories=[]))
    assert "-" in output


def test_diagnostics_format_shows_dash_for_empty_knowledge():
    output = _format_diagnostics(_sample_report(knowledge_documents=[]))
    assert "-" in output
