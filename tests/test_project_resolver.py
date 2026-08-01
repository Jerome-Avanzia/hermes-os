from pathlib import Path

import pytest

from hermes.kernel.project_resolver import ProjectNotFoundError, ProjectResolver
from hermes.models import Project, Task

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_ROOT = REPO_ROOT / "knowledge"


def _resolver() -> ProjectResolver:
    return ProjectResolver(knowledge_root=KNOWLEDGE_ROOT)


def test_resolves_by_project_name_case_insensitive():
    task = Task(id="t1", business="avanzia", request="Update the homepage copy")
    project = _resolver().resolve(task)

    assert isinstance(project, Project)
    assert project.id == "AVANZIA"


def test_resolves_by_alias():
    task = Task(id="t2", business="AVZ", request="Update the homepage copy")
    project = _resolver().resolve(task)

    assert project.id == "AVANZIA"


@pytest.mark.parametrize("term", [
    "hermes",
    "hermes-os",
    "avanzia-website",
    "serelo",
])
def test_resolves_by_ecosystem_alias(term):
    task = Task(id="t", business="", request=f"Review the {term} project")
    project = _resolver().resolve(task)

    assert project.id == "AVANZIA"


@pytest.mark.parametrize("term", [
    "aka",
    "akosmicanimals",
])
def test_resolves_akosmicanimals_alias(term):
    task = Task(id="t", business="", request=f"Review the {term} project")
    project = _resolver().resolve(task)

    assert project.id == "AKOSMICANIMALS"


def test_resolves_hermes_kernel_task():
    task = Task(id="t", business="", request="Review the Hermes kernel architecture")
    project = _resolver().resolve(task)

    assert project.id == "AVANZIA"


def test_resolves_avanzia_website_task():
    task = Task(id="t", business="", request="Review the AVANZIA website")
    project = _resolver().resolve(task)

    assert project.id == "AVANZIA"


def test_resolves_serelo_task():
    task = Task(id="t", business="", request="Review the Serelo project")
    project = _resolver().resolve(task)

    assert project.id == "AVANZIA"


def test_unknown_project_raises():
    task = Task(id="t3", business="Nonexistent Co", request="Do something")

    with pytest.raises(ProjectNotFoundError):
        _resolver().resolve(task)
