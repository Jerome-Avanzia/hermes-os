"""GoalRegistry — deterministic goal discovery from Business Knowledge.

Mirrors DepartmentRegistry and PeopleRegistry lifecycle:
build() -> reload() -> invalidate().

Goals are discovered from ``businesses_root/*/Goals.md`` via
BusinessDataLoader.
"""

from __future__ import annotations

import logging
from pathlib import Path

from hermes import config
from hermes.kernel.business_data_loader import BusinessDataLoader
from hermes.models.goal import Goal

logger = logging.getLogger(__name__)

GOAL_STATUSES = frozenset({"planned", "active", "completed", "cancelled"})


class GoalRegistry:
    """Indexed registry of strategic goals across all businesses."""

    def __init__(self, businesses_root: Path | None = None) -> None:
        self.businesses_root = (
            Path(businesses_root)
            if businesses_root is not None
            else config.businesses_root()
        )
        self._index: dict[str, Goal] = {}
        self._built = False

    # -- Lifecycle -------------------------------------------------------------

    def build(self) -> None:
        """Discover goals from all businesses and build the index.

        Idempotent — calling build() when already built is a no-op.
        """
        if self._built:
            return
        self._index = self._build_index()
        self._built = True
        logger.info(
            "GoalRegistry built: %d goals indexed",
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

    def list(self) -> list[Goal]:
        """Return all goals sorted by title."""
        self._ensure_built()
        return sorted(self._index.values(), key=lambda g: g.title)

    def get(self, goal_id: str) -> Goal | None:
        """Return a single Goal by ID, or None."""
        self._ensure_built()
        return self._index.get(goal_id)

    # -- Internal --------------------------------------------------------------

    def _ensure_built(self) -> None:
        if not self._built:
            self.build()

    def _build_index(self) -> dict[str, Goal]:
        index: dict[str, Goal] = {}
        if not self.businesses_root.is_dir():
            return index

        loader = BusinessDataLoader()
        for biz_dir in sorted(self.businesses_root.iterdir()):
            if not biz_dir.is_dir():
                continue
            goals_path = biz_dir / "Goals.md"
            if not goals_path.is_file():
                continue

            business_id = "biz_" + biz_dir.name.lower()
            warnings: list[str] = []
            goals = loader._load_goals(goals_path, business_id, warnings)

            for warning in warnings:
                logger.warning("GoalRegistry: %s: %s", biz_dir.name, warning)

            for goal in goals:
                if goal.goal_id not in index:
                    index[goal.goal_id] = goal

        return index
