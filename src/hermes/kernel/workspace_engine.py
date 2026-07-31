import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import Organization, Repository, Workspace, WorkspaceContext

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

        # Load workspace identity from workspace.yaml if present.
        identity = self._load_workspace_identity(project_id)

        workspace = Workspace(
            project_id=project_id,
            path=str(workspace_path),
            name=identity.get("name", project_id),
            description=identity.get("description", ""),
            mission=identity.get("mission", ""),
            profiles=identity.get("profiles", []),
            organization=identity.get("organization"),
        )

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

    def _load_workspace_identity(self, project_id: str) -> dict[str, Any]:
        ws_yaml = self.workspaces_root / project_id / "workspace.yaml"
        if not ws_yaml.is_file():
            return {}
        try:
            data = self._read_yaml(ws_yaml)
            org = self._load_organization(data)
            return {
                "name": data.get("name", project_id),
                "description": data.get("description", ""),
                "mission": data.get("mission", ""),
                "profiles": data.get("profiles", []),
                "organization": org,
            }
        except Exception:
            logger.warning("Failed to read workspace identity: %s", ws_yaml, exc_info=True)
            return {}

    def _load_organization(self, ws_data: dict[str, Any]) -> Organization | None:
        org_refs = ws_data.get("organization")
        if not org_refs or not isinstance(org_refs, dict):
            return None

        facets: dict[str, str] = {}
        for facet, rel_path in org_refs.items():
            path = self.workspaces_root.parent / rel_path
            if path.is_file():
                try:
                    facets[facet] = path.read_text(encoding="utf-8")
                except Exception:
                    logger.warning("Failed to read organization file: %s", path, exc_info=True)

        name = ws_data.get("name", "")
        return Organization(
            name=name,
            purpose=facets.get("purpose", ""),
            vision=facets.get("vision", ""),
            mission=facets.get("mission", ""),
            positioning=facets.get("positioning", ""),
            services=facets.get("services", ""),
            brand=facets.get("brand", ""),
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
