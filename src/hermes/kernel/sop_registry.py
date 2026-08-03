"""SOPRegistry — deterministic discovery of Standard Operating Procedures.

Mirrors CapabilityRegistry lifecycle: build() → reload() → invalidate().

SOP IDs are filesystem-derived: ``{skill_id}/{filename-stem}``.
If a file is renamed on disk, its ID changes.  This is an explicit
design decision — the filesystem is the source of truth.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models.sop import SOP

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_MAX_DESCRIPTION_LEN = 200


class SOPRegistry:
    """Indexed registry of Standard Operating Procedures.

    SOPs are discovered by walking ``skills_root/**/sops/*.md``.
    Each SOP belongs to the skill whose directory contains it.
    """

    def __init__(self, skills_root: Path | None = None) -> None:
        self.skills_root = (
            Path(skills_root) if skills_root is not None else config.skills_root()
        )
        self._index: dict[str, SOP] = {}
        self._built = False

    # -- Lifecycle -------------------------------------------------------------

    def build(self) -> None:
        """Discover all SOP files and build the index.

        Idempotent — calling build() when already built is a no-op.
        """
        if self._built:
            return
        self._index = self._build_index()
        self._built = True
        logger.info("SOPRegistry built: %d SOPs indexed", len(self._index))

    def reload(self) -> None:
        """Invalidate and rebuild from disk."""
        self._built = False
        self._index = {}
        self.build()

    def invalidate(self) -> None:
        """Clear the index.  Next access triggers build()."""
        self._index = {}
        self._built = False

    # -- Queries ---------------------------------------------------------------

    def list(self) -> list[SOP]:
        """Return all SOPs sorted by title."""
        self._ensure_built()
        return sorted(self._index.values(), key=lambda s: s.title)

    def get(self, sop_id: str) -> SOP | None:
        """Return a single SOP by ID, or None."""
        self._ensure_built()
        return self._index.get(sop_id)

    def list_by_skill(self, skill_id: str) -> list[SOP]:
        """Return all SOPs belonging to a specific skill."""
        self._ensure_built()
        return sorted(
            [s for s in self._index.values() if s.skill_id == skill_id],
            key=lambda s: s.title,
        )

    # -- Internal --------------------------------------------------------------

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()

    def _build_index(self) -> dict[str, SOP]:
        index: dict[str, SOP] = {}
        if not self.skills_root.is_dir():
            return index

        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            sops_dir = skill_dir / "sops"
            if not sops_dir.is_dir():
                continue

            manifest = self._read_skill_manifest(skill_dir)
            skill_id = manifest.get("id", skill_dir.name)

            for md_path in sorted(sops_dir.glob("*.md")):
                sop = self._parse_sop(md_path, skill_id, manifest)
                if sop.id not in index:
                    index[sop.id] = sop

        return index

    def _read_skill_manifest(self, skill_dir: Path) -> dict[str, Any]:
        manifest_path = skill_dir / "skill.yaml"
        if not manifest_path.is_file():
            return {"id": skill_dir.name}
        with open(manifest_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {"id": skill_dir.name}

    def _parse_sop(
        self,
        md_path: Path,
        skill_id: str,
        manifest: dict[str, Any],
    ) -> SOP:
        raw = md_path.read_text(encoding="utf-8")
        stem = md_path.stem
        sop_id = f"{skill_id}/{stem}"

        # Extract optional YAML frontmatter.
        frontmatter: dict[str, Any] = {}
        body = raw
        fm_match = _FRONTMATTER_RE.match(raw)
        if fm_match:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
            body = raw[fm_match.end():]

        title = self._extract_title(body, stem)
        description = self._extract_description(body)

        return SOP(
            id=sop_id,
            title=title,
            skill_id=skill_id,
            filename=md_path.name,
            content=raw,
            description=description,
            version=frontmatter.get("version", ""),
            status=frontmatter.get("status", manifest.get("status", "active")),
            owner=frontmatter.get("owner", manifest.get("owner")),
            category=frontmatter.get("category"),
        )

    @staticmethod
    def _extract_title(body: str, fallback_stem: str) -> str:
        m = _H1_RE.search(body)
        if m:
            return m.group(1).strip()
        return fallback_stem.replace("-", " ").title()

    @staticmethod
    def _extract_description(body: str) -> str:
        """Return first non-empty paragraph after the H1."""
        lines = body.split("\n")
        past_h1 = False
        paragraph: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not past_h1:
                if stripped.startswith("# "):
                    past_h1 = True
                continue
            if stripped == "":
                if paragraph:
                    break
                continue
            paragraph.append(stripped)

        text = " ".join(paragraph)
        if len(text) > _MAX_DESCRIPTION_LEN:
            text = text[:_MAX_DESCRIPTION_LEN - 3] + "..."
        return text
