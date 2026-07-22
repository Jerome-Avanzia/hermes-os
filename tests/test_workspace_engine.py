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


def test_capability_detection():
    context = _engine().resolve("AVANZIA")

    assert "node" in context.capabilities
    assert "npm" in context.capabilities
    assert "docker" not in context.capabilities
    assert "pnpm" not in context.capabilities


def test_unknown_project_raises():
    with pytest.raises(WorkspaceNotFoundError):
        _engine().resolve("NONEXISTENT")
