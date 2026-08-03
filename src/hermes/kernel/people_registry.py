"""PeopleRegistry — deterministic organizational people discovery.

Mirrors DepartmentRegistry and CapabilityRegistry lifecycle:
build() → reload() → invalidate().

People are discovered from ``people_root/*/person.yaml``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models.person import Person

logger = logging.getLogger(__name__)


class PeopleRegistry:
    """Indexed registry of organizational people."""

    def __init__(self, people_root: Path | None = None) -> None:
        self.people_root = (
            Path(people_root)
            if people_root is not None
            else config.people_root()
        )
        self._index: dict[str, Person] = {}
        self._built = False

    # -- Lifecycle -------------------------------------------------------------

    def build(self) -> None:
        """Discover person manifests and build the index.

        Idempotent — calling build() when already built is a no-op.
        """
        if self._built:
            return
        self._index = self._build_index()
        self._built = True
        logger.info(
            "PeopleRegistry built: %d people indexed",
            len(self._index),
        )

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

    def list(self) -> list[Person]:
        """Return all people sorted by name."""
        self._ensure_built()
        return sorted(self._index.values(), key=lambda p: p.name)

    def get(self, person_id: str) -> Person | None:
        """Return a single Person by ID, or None."""
        self._ensure_built()
        return self._index.get(person_id)

    # -- Internal --------------------------------------------------------------

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()

    def _build_index(self) -> dict[str, Person]:
        index: dict[str, Person] = {}
        if not self.people_root.is_dir():
            return index

        for person_dir in sorted(self.people_root.iterdir()):
            if not person_dir.is_dir():
                continue
            manifest_path = person_dir / "person.yaml"
            if not manifest_path.is_file():
                continue
            manifest = self._read_yaml(manifest_path)
            person_id = manifest.get("id", person_dir.name)
            if person_id not in index:
                index[person_id] = self._manifest_to_person(manifest, person_id)

        return index

    @staticmethod
    def _manifest_to_person(
        manifest: dict[str, Any], person_id: str,
    ) -> Person:
        return Person(
            id=person_id,
            name=manifest.get("name", person_id),
            title=manifest.get("title", ""),
            department_ids=manifest.get("department_ids", []),
            email=manifest.get("email"),
            status=manifest.get("status", "active"),
            responsibilities=manifest.get("responsibilities", []),
            owner_aliases=manifest.get("owner_aliases", []),
        )

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
