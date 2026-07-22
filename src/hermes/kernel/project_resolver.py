from pathlib import Path
from typing import Any

import yaml

from hermes.models import Project, Task

DEFAULT_KNOWLEDGE_ROOT = Path("knowledge")


class ProjectNotFoundError(Exception):
    pass


class ProjectResolver:
    def __init__(self, knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT) -> None:
        self.knowledge_root = Path(knowledge_root)
        self.registry_path = self.knowledge_root / "registry.yaml"

    def resolve(self, task: Task) -> Project:
        registry = self._read_yaml(self.registry_path)
        projects = registry.get("projects", {})
        haystack = f"{task.business} {task.request}".lower()

        for project_id, entry in projects.items():
            if self._matches(project_id, entry, haystack):
                return Project(
                    id=project_id,
                    name=entry.get("name", project_id),
                    path=str(self.knowledge_root / entry["path"]),
                )

        raise ProjectNotFoundError(
            f"No registered project matches task {task.id!r} "
            f"(business={task.business!r}, request={task.request!r})"
        )

    @staticmethod
    def _matches(project_id: str, entry: dict[str, Any], haystack: str) -> bool:
        candidates = {project_id, str(entry.get("name", ""))}
        candidates.update(entry.get("aliases", []))

        return any(
            candidate and candidate.lower() in haystack for candidate in candidates
        )

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
