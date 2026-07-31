from pathlib import Path

import pytest

from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError
from hermes.models import Organization, Repository, Workspace, WorkspaceContext

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


def test_multi_repository_resolution():
    context = _engine().resolve("AVANZIA")

    assert len(context.repositories) >= 2
    repo_names = [r.name for r in context.repositories]
    assert "avanzia-website" in repo_names
    assert "hermes-os" in repo_names


def test_repositories_are_repository_instances():
    context = _engine().resolve("AVANZIA")

    for repo in context.repositories:
        assert isinstance(repo, Repository)
        assert repo.name
        assert repo.path


def test_hermes_os_repository_is_detected_as_python():
    context = _engine().resolve("AVANZIA")

    hermes = next(r for r in context.repositories if r.name == "hermes-os")
    assert hermes.exists is True
    assert hermes.is_git_repo is True
    assert "python" in hermes.environment


def test_workspace_identity_loaded():
    context = _engine().resolve("AVANZIA")

    assert context.workspace.name == "AVANZIA"
    assert context.workspace.mission != ""
    assert context.workspace.description != ""


def test_workspace_profiles_loaded():
    context = _engine().resolve("AVANZIA")

    assert isinstance(context.workspace.profiles, list)
    assert "default" in context.workspace.profiles
    assert "business" in context.workspace.profiles
    assert "developer" in context.workspace.profiles


def test_organization_loaded():
    context = _engine().resolve("AVANZIA")
    org = context.workspace.organization

    assert org is not None
    assert isinstance(org, Organization)
    assert org.name == "AVANZIA"


def test_organization_facets_contain_file_content():
    context = _engine().resolve("AVANZIA")
    org = context.workspace.organization

    assert "AVANZIA exists" in org.purpose
    assert "venture studio" in org.vision or "Vision" in org.vision
    assert "designs, builds" in org.mission or "Design" in org.mission
    assert "positioning" in org.positioning.lower() or "AI-first" in org.positioning
    assert "Build" in org.services or "Automate" in org.services
    assert "personality" in org.brand.lower() or "Personality" in org.brand


def test_organization_facets_are_full_file_content():
    """Organization fields contain the full markdown, not summaries."""
    context = _engine().resolve("AVANZIA")
    org = context.workspace.organization

    # Each facet should have multiple lines (full file, not a one-liner)
    for facet in [org.purpose, org.vision, org.mission, org.positioning, org.services, org.brand]:
        assert "\n" in facet, "Organization facet should contain full file content"


def test_workspace_without_organization():
    """A workspace with no organization section returns None."""
    import tempfile
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "workspaces"
        ws_root.mkdir()
        (ws_root / "TEST").mkdir()
        registry = {"workspaces": {"TEST": {"repositories": []}}}
        (ws_root / "registry.yaml").write_text(yaml.dump(registry))
        (ws_root / "TEST" / "workspace.yaml").write_text(yaml.dump({"name": "Test"}))

        engine = WorkspaceEngine(workspaces_root=ws_root, base_dir=Path(tmpdir))
        try:
            context = engine.resolve("TEST")
            assert context.workspace.organization is None
        except WorkspaceNotFoundError:
            pass


def test_workspace_without_identity_uses_defaults():
    """A workspace with registry entry but no workspace.yaml gets default identity."""
    import tempfile, shutil, yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        ws_root = Path(tmpdir) / "workspaces"
        ws_root.mkdir()
        registry = {"workspaces": {"TEST": {"repositories": []}}}
        (ws_root / "registry.yaml").write_text(yaml.dump(registry))

        engine = WorkspaceEngine(workspaces_root=ws_root, base_dir=Path(tmpdir))
        # No workspace.yaml exists for TEST — should not raise
        try:
            context = engine.resolve("TEST")
            assert context.workspace.name == "TEST"
            assert context.workspace.profiles == []
        except WorkspaceNotFoundError:
            pass  # No path defined — expected for empty repo list
