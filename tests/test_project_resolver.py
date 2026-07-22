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


def test_unknown_project_raises():
    task = Task(id="t3", business="Nonexistent Co", request="Do something")

    with pytest.raises(ProjectNotFoundError):
        _resolver().resolve(task)
