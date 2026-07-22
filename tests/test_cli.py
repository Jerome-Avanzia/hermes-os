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
    assert "Project: AVANZIA" in result.stdout
    assert "Knowledge documents: 12" in result.stdout


def test_context_unknown_project_exits_nonzero():
    result = runner.invoke(app, ["context", "Do something unrelated"])

    assert result.exit_code == 1
    assert "Error" in result.output
