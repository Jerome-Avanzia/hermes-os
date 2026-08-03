"""DepartmentRegistry — deterministic department discovery.

Mirrors CapabilityRegistry and SOPRegistry lifecycle:
build() → reload() → invalidate().

Departments are discovered from ``departments_root/*/department.yaml``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models.department import Department

logger = logging.getLogger(__name__)


class DepartmentRegistry:
    """Indexed registry of organizational departments."""

    def __init__(self, departments_root: Path | None = None) -> None:
        self.departments_root = (
            Path(departments_root)
            if departments_root is not None
            else config.departments_root()
        )
        self._index: dict[str, Department] = {}
        self._built = False

    # -- Lifecycle -------------------------------------------------------------

    def build(self) -> None:
        """Discover department manifests and build the index.

        Idempotent — calling build() when already built is a no-op.
        """
        if self._built:
            return
        self._index = self._build_index()
        self._built = True
        logger.info(
            "DepartmentRegistry built: %d departments indexed",
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

    def list(self) -> list[Department]:
        """Return all departments sorted by name."""
        self._ensure_built()
        return sorted(self._index.values(), key=lambda d: d.name)

    def get(self, department_id: str) -> Department | None:
        """Return a single Department by ID, or None."""
        self._ensure_built()
        return self._index.get(department_id)

    # -- Internal --------------------------------------------------------------

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()

    def _build_index(self) -> dict[str, Department]:
        index: dict[str, Department] = {}
        if not self.departments_root.is_dir():
            return index

        for dept_dir in sorted(self.departments_root.iterdir()):
            if not dept_dir.is_dir():
                continue
            manifest_path = dept_dir / "department.yaml"
            if not manifest_path.is_file():
                continue
            manifest = self._read_yaml(manifest_path)
            dept_id = manifest.get("id", dept_dir.name)
            if dept_id not in index:
                index[dept_id] = self._manifest_to_department(manifest, dept_id)

        return index

    @staticmethod
    def _manifest_to_department(
        manifest: dict[str, Any], dept_id: str,
    ) -> Department:
        return Department(
            id=dept_id,
            name=manifest.get("name", dept_id),
            description=manifest.get("description", ""),
            mission=manifest.get("mission", ""),
            owner=manifest.get("owner"),
            status=manifest.get("status", "active"),
            tags=manifest.get("tags", []),
        )

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
