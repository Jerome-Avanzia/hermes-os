from pathlib import Path
from typing import Any

import yaml

from hermes.models import KnowledgeContext, KnowledgeDocument, Project

DEFAULT_KNOWLEDGE_ROOT = Path("knowledge")


class KnowledgeEngine:
    def __init__(self, knowledge_root: Path = DEFAULT_KNOWLEDGE_ROOT) -> None:
        self.knowledge_root = Path(knowledge_root)
        self.registry_path = self.knowledge_root / "registry.yaml"

    def load(self, project_id: str) -> KnowledgeContext:
        registry = self._read_yaml(self.registry_path)
        projects = registry.get("projects", {})

        if project_id not in projects:
            raise ValueError(f"Unknown project: {project_id}")

        entry = projects[project_id]
        project_path = self.knowledge_root / entry["path"]

        project = Project(
            id=project_id,
            name=entry.get("name", project_id),
            path=str(project_path),
        )

        manifest = self._read_yaml(project_path / "manifest.yaml")
        filenames = manifest.get("documents", [])

        documents = [
            self._load_document(project_path, filename) for filename in filenames
        ]

        return KnowledgeContext(project=project, documents=documents)

    def _load_document(self, project_path: Path, filename: str) -> KnowledgeDocument:
        document_path = project_path / filename
        content = document_path.read_text(encoding="utf-8")

        return KnowledgeDocument(
            id=Path(filename).stem,
            title=self._extract_title(content, fallback=Path(filename).stem),
            path=str(document_path),
            content=content,
        )

    @staticmethod
    def _extract_title(content: str, fallback: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped.removeprefix("# ").strip()
        return fallback

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
