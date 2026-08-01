import logging
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import KnowledgeContext, KnowledgeDocument, Project

logger = logging.getLogger(__name__)


_DEFAULT_MAX_DOCS = 5

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those",
    "what", "which", "who", "whom", "how", "when", "where", "why",
    "and", "but", "or", "nor", "not", "no", "so", "if", "then",
    "of", "in", "to", "for", "with", "on", "at", "from", "by", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "up", "down",
})


class KnowledgeEngine:
    def __init__(self, knowledge_root: Path | None = None) -> None:
        self.knowledge_root = (
            Path(knowledge_root) if knowledge_root is not None else config.knowledge_root()
        )
        self.registry_path = self.knowledge_root / "registry.yaml"

    def select(
        self,
        documents: list[KnowledgeDocument],
        query: str | None,
        max_docs: int = _DEFAULT_MAX_DOCS,
    ) -> list[KnowledgeDocument]:
        """Select the most relevant documents for a query.

        Returns up to *max_docs* documents ranked by relevance.
        Falls back to manifest order when the query is empty or no
        documents match.
        """
        if not documents:
            return []

        if not query or not query.strip():
            return documents[:max_docs]

        terms = self._extract_terms(query)
        if not terms:
            return documents[:max_docs]

        scored: list[tuple[float, int, KnowledgeDocument]] = []
        for idx, doc in enumerate(documents):
            score = self._score_document(doc, terms)
            # idx preserves manifest order for tie-breaking
            scored.append((score, idx, doc))

        scored.sort(key=lambda t: (-t[0], t[1]))

        if scored[0][0] == 0:
            return documents[:max_docs]

        return [doc for score, _, doc in scored if score > 0][:max_docs]

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
    def _extract_terms(text: str) -> list[str]:
        lowered = text.lower()
        tokens = []
        current = []
        for ch in lowered:
            if ch.isalnum():
                current.append(ch)
            else:
                if current:
                    tokens.append("".join(current))
                    current = []
        if current:
            tokens.append("".join(current))
        return [t for t in tokens if len(t) >= 2 and t not in _STOP_WORDS]

    @staticmethod
    def _score_document(doc: KnowledgeDocument, terms: list[str]) -> float:
        title_lower = doc.title.lower()
        content_lower = doc.content.lower()
        score = 0.0
        for term in terms:
            if term in title_lower:
                score += 3.0
            if term in content_lower:
                score += 1.0
        return score

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
