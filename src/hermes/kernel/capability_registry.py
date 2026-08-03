"""CapabilityRegistry — deterministic, indexed capability discovery.

Builds a cached index of all capabilities from skill manifests on disk.
Supports a deterministic lifecycle: build() → reload() → invalidate().

No filesystem watching.  The registry is rebuilt explicitly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import Capability, Task

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Indexed registry of organizational capabilities.

    Capabilities are discovered by their presence on disk — each
    ``skill.yaml`` manifest declares one or more capability IDs.
    The registry loads all manifests once via :meth:`build` and
    serves lookups from an in-memory index thereafter.
    """

    def __init__(self, skills_root: Path | None = None) -> None:
        self.skills_root = (
            Path(skills_root) if skills_root is not None else config.skills_root()
        )
        self._index: dict[str, Capability] = {}
        self._built = False

    # -- Lifecycle -------------------------------------------------------------

    def build(self) -> None:
        """Discover all skill manifests and build the capability index.

        Idempotent — calling build() when already built is a no-op.
        Use :meth:`reload` to force a rebuild.
        """
        if self._built:
            return
        self._index = self._build_index()
        self._built = True
        logger.info(
            "CapabilityRegistry built: %d capabilities indexed",
            len(self._index),
        )

    def reload(self) -> None:
        """Invalidate and rebuild the capability index from disk."""
        self._built = False
        self._index = {}
        self.build()

    def invalidate(self) -> None:
        """Clear the index.  Next access will require :meth:`build`."""
        self._index = {}
        self._built = False

    # -- Queries ---------------------------------------------------------------

    def list(self) -> list[Capability]:
        """Return all capabilities sorted by name."""
        self._ensure_built()
        return sorted(self._index.values(), key=lambda c: c.name)

    def get(self, capability_id: str) -> Capability | None:
        """Return a single Capability by ID, or None if not found."""
        self._ensure_built()
        return self._index.get(capability_id)

    def match(self, task: Task) -> list[Capability]:
        """Match capabilities to a task by keyword.

        Falls back to the kernel capability when no specialist matches.
        """
        self._ensure_built()
        haystack = f"{task.business} {task.request}".lower()

        matches = [
            cap for cap in self._index.values()
            if cap.keywords and self._keyword_match(cap.keywords, haystack)
        ]

        if not matches:
            kernel = self._index.get("kernel")
            if kernel:
                return [kernel]
            return []

        return matches

    # -- Internal --------------------------------------------------------------

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()

    def _build_index(self) -> dict[str, Capability]:
        index: dict[str, Capability] = {}
        for manifest, skill_path in self._discover_manifests():
            skill_id = manifest.get("id", skill_path.name)
            for capability_id in manifest.get("capabilities", []):
                if capability_id in index:
                    continue  # first manifest wins
                index[capability_id] = self._manifest_to_capability(
                    manifest, capability_id, skill_id,
                )
        return index

    def _discover_manifests(self) -> list[tuple[dict[str, Any], Path]]:
        if not self.skills_root.is_dir():
            return []
        result = []
        for path in sorted(self.skills_root.rglob("skill.yaml")):
            manifest = self._read_yaml(path)
            result.append((manifest, path.parent))
        return result

    @staticmethod
    def _manifest_to_capability(
        manifest: dict[str, Any],
        capability_id: str,
        skill_id: str,
    ) -> Capability:
        # Read sop_refs (new) with fallback to legacy sop_ref.
        sop_refs = manifest.get("sop_refs", [])
        sop_ref = manifest.get("sop_ref")
        if not sop_refs and sop_ref:
            sop_refs = [sop_ref]

        return Capability(
            id=capability_id,
            name=manifest.get("name", capability_id),
            version=manifest.get("version", ""),
            provides=manifest.get("provides", []),
            keywords=manifest.get("keywords", []),
            description=manifest.get("description", ""),
            inputs=manifest.get("inputs", []),
            outputs=manifest.get("outputs", []),
            sop_ref=sop_ref,
            sop_refs=sop_refs,
            status=manifest.get("status", "active"),
            skill_id=skill_id,
            owner=manifest.get("owner"),
            depends_on=manifest.get("depends_on", []),
        )

    @staticmethod
    def _keyword_match(keywords: list[str], haystack: str) -> bool:
        return any(keyword.lower() in haystack for keyword in keywords)

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
