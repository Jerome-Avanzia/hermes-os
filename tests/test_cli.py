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
