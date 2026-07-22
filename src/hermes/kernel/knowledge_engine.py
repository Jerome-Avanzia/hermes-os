import logging
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import KnowledgeContext, KnowledgeDocument, Project

logger = logging.getLogger(__name__)


class KnowledgeEngine:
    def __init__(self, knowledge_root: Path | None = None) -> None:
        self.knowledge_root = (
            Path(knowledge_root) if knowledge_root is not None else config.knowledge_root()
        )
        self.registry_path = self.knowledge_root / "registry.yaml"

    def load(self, project_id: str) -> KnowledgeContext:
        registry = self._read_yaml(self.registry_path)
        projects = registry.get("projects", {})

        if project_id not in projects:
            logger.warning("Unknown project requested: %s", project_id)
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

        logger.info(
            "Loaded %d knowledge document(s) for project %s", len(documents), project_id
        )
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
