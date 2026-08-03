"""CapabilityEngine — compatibility façade delegating to CapabilityRegistry.

Preserves the existing interface used by ContextEngine while all
discovery and matching logic lives in CapabilityRegistry.
"""

from __future__ import annotations

from pathlib import Path

from hermes.kernel.capability_registry import CapabilityRegistry
from hermes.models import Capability, Task


class CapabilityEngine:
    def __init__(self, skills_root: Path | None = None) -> None:
        self._registry = CapabilityRegistry(skills_root=skills_root)

    @property
    def registry(self) -> CapabilityRegistry:
        """Expose the underlying registry for direct access."""
        return self._registry

    def match(self, task: Task) -> list[Capability]:
        """Match capabilities to a task by keyword.

        Delegates to CapabilityRegistry.match().
        """
        return self._registry.match(task)
