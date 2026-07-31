import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import Repository, Workspace, WorkspaceContext

DEFAULT_WORKSPACES_ROOT = Path("workspaces")

ENVIRONMENT_FILES = {
    "package.json": "node",
    "pyproject.toml": "python",
    "docker-compose.yml": "docker",
    "compose.yaml": "docker",
    "requirements.txt": "python",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
}

logger = logging.getLogger(__name__)


class WorkspaceNotFoundError(Exception):
    pass


class WorkspaceEngine:
    def __init__(
        self,
        workspaces_root: Path = DEFAULT_WORKSPACES_ROOT,
        base_dir: Path | None = None,
    ) -> None:
        self.workspaces_root = Path(workspaces_root)
        self.registry_path = self.workspaces_root / "registry.yaml"
        self.base_dir = Path(base_dir) if base_dir is not None else config.repositories_root()

    def resolve(self, project_id: str) -> WorkspaceContext:
        registry = self._read_yaml(self.registry_path)
        entries = registry.get("workspaces", {})

        if project_id not in entries:
            logger.warning("No registered workspace for project: %s", project_id)
            raise WorkspaceNotFoundError(
                f"No registered workspace for project: {project_id}"
            )

        entry = entries[project_id]
        repositories: list[Repository] = []

        if "repositories" in entry:
            for repo_entry in entry["repositories"]:
                repo_path = (self.base_dir / repo_entry["path"]).resolve()
                repositories.append(self._resolve_repository(repo_entry["name"], repo_path))

        # Derive backward-compatible singular fields from the first repository
        # or from the legacy single-path format.
        if repositories:
            primary = repositories[0]
            workspace_path = Path(primary.path)
            exists = primary.exists
            is_git_repo = primary.is_git_repo
            branch = primary.branch
            is_clean = primary.is_clean
            environment = primary.environment
        elif "path" in entry:
            workspace_path = (self.base_dir / entry["path"]).resolve()
            exists = workspace_path.is_dir()
            is_git_repo = exists and (workspace_path / ".git").is_dir()
            branch = self._current_branch(workspace_path) if is_git_repo else None
            is_clean = self._is_clean(workspace_path) if is_git_repo else None
            environment = self._detect_environment(workspace_path) if exists else []
        else:
            raise WorkspaceNotFoundError(
                f"No path or repositories defined for project: {project_id}"
            )

        workspace = Workspace(project_id=project_id, path=str(workspace_path))

        logger.info(
            "Resolved workspace for %s: repositories=%d exists=%s git=%s",
            project_id,
            len(repositories),
            exists,
            is_git_repo,
        )

        return WorkspaceContext(
            workspace=workspace,
            exists=exists,
            is_git_repo=is_git_repo,
            branch=branch,
            is_clean=is_clean,
            environment=environment,
            repositories=repositories,
        )

    def _resolve_repository(self, name: str, path: Path) -> Repository:
        exists = path.is_dir()
        is_git_repo = exists and (path / ".git").is_dir()
        branch = self._current_branch(path) if is_git_repo else None
        is_clean = self._is_clean(path) if is_git_repo else None
        environment = self._detect_environment(path) if exists else []

        return Repository(
            name=name,
            path=str(path),
            exists=exists,
            is_git_repo=is_git_repo,
            branch=branch,
            is_clean=is_clean,
            environment=environment,
        )

    @staticmethod
    def _detect_environment(path: Path) -> list[str]:
        environment = []
        for filename, technology in ENVIRONMENT_FILES.items():
            if (path / filename).is_file() and technology not in environment:
                environment.append(technology)
        return environment

    @staticmethod
    def _current_branch(path: Path) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    @staticmethod
    def _is_clean(path: Path) -> bool | None:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() == ""

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
