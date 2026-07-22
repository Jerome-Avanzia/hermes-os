from pathlib import Path

import pytest

from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError
from hermes.models import Workspace, WorkspaceContext

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACES_ROOT = REPO_ROOT / "workspaces"


def _engine() -> WorkspaceEngine:
    return WorkspaceEngine(workspaces_root=WORKSPACES_ROOT, base_dir=REPO_ROOT)


def test_workspace_resolution():
    context = _engine().resolve("AVANZIA")

    assert isinstance(context, WorkspaceContext)
    assert isinstance(context.workspace, Workspace)
    assert context.workspace.project_id == "AVANZIA"
    assert context.exists is True


def test_git_detection():
    context = _engine().resolve("AVANZIA")

    assert context.is_git_repo is True
    assert context.branch is not None
    assert isinstance(context.is_clean, bool)


def test_environment_detection():
    context = _engine().resolve("AVANZIA")

    assert "node" in context.environment
    assert "npm" in context.environment
    assert "docker" not in context.environment
    assert "pnpm" not in context.environment


def test_unknown_project_raises():
    with pytest.raises(WorkspaceNotFoundError):
        _engine().resolve("NONEXISTENT")
