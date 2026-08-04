"""Architecture Knowledge — exposes Hermes architecture as a knowledge source.

Sprint 48: Makes architecture documents, decision records, engineering
standards, specifications, contracts, and governance documents available
as first-class KnowledgeDocument instances through a declarative registry.

Uses the existing KnowledgeDocument model.  No new database, no embeddings,
no vector store.  The filesystem is the registry (Principle 3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from hermes.models.knowledge_document import KnowledgeDocument

logger = logging.getLogger(__name__)


# ── Source categories ───────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class ArchitectureSource:
    """A declared category of architecture knowledge."""

    category: str               # e.g. "architecture", "decisions", "standards"
    directory: str              # relative to repo root, e.g. "docs/architecture"
    pattern: str                # glob pattern, e.g. "*.md"
    description: str            # human-readable category description


# Declarative registry — pure data, no embedded logic.
# Each entry maps a category name to where its documents live.
ARCHITECTURE_SOURCES: tuple[ArchitectureSource, ...] = (
    ArchitectureSource(
        category="architecture",
        directory="docs/architecture",
        pattern="*.md",
        description="Architecture documents (constitution, executive intelligence, debt register)",
    ),
    ArchitectureSource(
        category="governance",
        directory="docs",
        pattern="*.md",
        description="Governance and design documents (principles, playbook, architecture, specs)",
    ),
    ArchitectureSource(
        category="decisions",
        directory="decisions",
        pattern="*.md",
        description="Architecture Decision Records and governance decisions",
    ),
    ArchitectureSource(
        category="standards",
        directory="standards",
        pattern="*.md",
        description="Engineering standards and policies",
    ),
    ArchitectureSource(
        category="specifications",
        directory="specs",
        pattern="*.md",
        description="Business object specifications",
    ),
    ArchitectureSource(
        category="contracts",
        directory="contracts",
        pattern="*.schema.json",
        description="Canonical JSON Schema contracts",
    ),
)


# ── Architecture Knowledge loader ──────────────────────────────────────────


class ArchitectureKnowledge:
    """Discovers and loads architecture documents from declared sources.

    Follows the same patterns as KnowledgeEngine: loads from disk,
    returns KnowledgeDocument instances, uses no external services.
    """

    def __init__(self, repo_root: Path | None = None) -> None:
        self._repo_root = repo_root or self._detect_repo_root()

    def load(
        self,
        categories: list[str] | None = None,
    ) -> list[KnowledgeDocument]:
        """Load architecture documents from declared sources.

        When *categories* is provided, only those categories are loaded.
        When None, all categories are loaded.

        Returns documents sorted by category order then filename.
        """
        sources = ARCHITECTURE_SOURCES
        if categories is not None:
            requested = frozenset(categories)
            sources = tuple(s for s in sources if s.category in requested)

        documents: list[KnowledgeDocument] = []
        seen_paths: set[Path] = set()

        for source in sources:
            source_dir = self._repo_root / source.directory
            if not source_dir.is_dir():
                logger.debug(
                    "Architecture source directory not found: %s", source_dir,
                )
                continue

            paths = sorted(source_dir.glob(source.pattern))
            for path in paths:
                if not path.is_file():
                    continue
                # Deduplicate: docs/architecture/*.md overlaps with docs/*.md
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)

                doc = self._load_document(path, source.category)
                if doc is not None:
                    documents.append(doc)

        logger.info(
            "Loaded %d architecture knowledge document(s) from %d source(s)",
            len(documents),
            len(sources),
        )
        return documents

    def list_sources(self) -> list[dict]:
        """Return metadata about all declared architecture sources."""
        result = []
        for source in ARCHITECTURE_SOURCES:
            source_dir = self._repo_root / source.directory
            count = 0
            if source_dir.is_dir():
                count = sum(
                    1 for p in source_dir.glob(source.pattern) if p.is_file()
                )
            result.append({
                "category": source.category,
                "directory": source.directory,
                "description": source.description,
                "document_count": count,
                "available": source_dir.is_dir(),
            })
        return result

    def _load_document(
        self, path: Path, category: str,
    ) -> KnowledgeDocument | None:
        """Load a single document, returning None on read failure."""
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("Failed to read architecture document: %s", path)
            return None

        # ID: category prefix ensures uniqueness across sources
        doc_id = f"arch:{category}:{path.stem}"
        title = self._extract_title(content, fallback=path.stem)

        return KnowledgeDocument(
            id=doc_id,
            title=title,
            path=str(path),
            content=content,
        )

    @staticmethod
    def _extract_title(content: str, fallback: str) -> str:
        """Extract the first Markdown heading, or fall back to filename stem."""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped.removeprefix("# ").strip()
        return fallback

    @staticmethod
    def _detect_repo_root() -> Path:
        """Walk up from this file to find the repository root.

        Looks for pyproject.toml as the sentinel.
        """
        current = Path(__file__).resolve().parent
        for _ in range(10):
            if (current / "pyproject.toml").is_file():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        # Fallback: assume 3 levels up from kernel/
        return Path(__file__).resolve().parents[3]
