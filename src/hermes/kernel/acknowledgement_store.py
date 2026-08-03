"""Persistent acknowledgement state for notifications.

A single YAML file per workspace tracks which notification IDs
have been acknowledged by the Founder.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PERSISTENCE_VERSION = 1


class AcknowledgementStore:
    def __init__(self, workspaces_root: Path = Path("workspaces")) -> None:
        self.workspaces_root = Path(workspaces_root)

    def _ack_path(self, workspace_id: str) -> Path:
        return self.workspaces_root / workspace_id / "notifications_ack.yaml"

    def load(self, workspace_id: str) -> set[str]:
        """Return the set of acknowledged notification IDs."""
        path = self._ack_path(workspace_id)
        if not path.is_file():
            return set()

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        entries = data.get("acknowledged", [])
        return {e["id"] for e in entries if isinstance(e, dict) and "id" in e}

    def acknowledge(self, workspace_id: str, notification_id: str) -> None:
        """Add a notification ID to the acknowledged set."""
        path = self._ack_path(workspace_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.is_file():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {"version": _PERSISTENCE_VERSION, "acknowledged": []}

        entries = data.get("acknowledged", [])
        existing_ids = {e["id"] for e in entries if isinstance(e, dict) and "id" in e}

        if notification_id in existing_ids:
            return  # Already acknowledged — idempotent

        entries.append({
            "id": notification_id,
            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
        })

        data["version"] = _PERSISTENCE_VERSION
        data["acknowledged"] = entries

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info("Acknowledged notification %s in workspace %s", notification_id, workspace_id)
