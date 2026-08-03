"""Persistent storage for Heartbeats.

Heartbeats are stored as YAML files in workspaces/{workspace_id}/heartbeats/.
Heartbeats are immutable after creation — corrections are new heartbeats.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml

from hermes.models.heartbeat import Heartbeat

logger = logging.getLogger(__name__)

_PERSISTENCE_VERSION = 1


class HeartbeatNotFoundError(Exception):
    pass


class HeartbeatStore:
    def __init__(self, workspaces_root: Path = Path("workspaces")) -> None:
        self.workspaces_root = Path(workspaces_root)

    def heartbeats_dir(self, workspace_id: str) -> Path:
        return self.workspaces_root / workspace_id / "heartbeats"

    def save(self, heartbeat: Heartbeat) -> None:
        """Persist a Heartbeat to YAML."""
        hb_dir = self.heartbeats_dir(heartbeat.workspace_id)
        hb_dir.mkdir(parents=True, exist_ok=True)
        path = hb_dir / f"{heartbeat.id}.yaml"

        data = {
            "version": _PERSISTENCE_VERSION,
            "id": heartbeat.id,
            "operation_id": heartbeat.operation_id,
            "workspace_id": heartbeat.workspace_id,
            "timestamp": heartbeat.timestamp.isoformat(),
            "status": heartbeat.status,
            "summary": heartbeat.summary,
            "author": heartbeat.author,
            "details": heartbeat.details,
            "blocker": heartbeat.blocker,
            "next_action": heartbeat.next_action,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info("Saved heartbeat %s to %s", heartbeat.id, path)

    def load(self, workspace_id: str, heartbeat_id: str) -> Heartbeat:
        """Load a Heartbeat from YAML."""
        path = self.heartbeats_dir(workspace_id) / f"{heartbeat_id}.yaml"
        if not path.is_file():
            raise HeartbeatNotFoundError(
                f"Heartbeat not found: {heartbeat_id} in workspace {workspace_id}"
            )

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return Heartbeat(
            id=data["id"],
            operation_id=data["operation_id"],
            workspace_id=data["workspace_id"],
            timestamp=_parse_datetime(data["timestamp"]),
            status=data["status"],
            summary=data["summary"],
            author=data.get("author", ""),
            details=data.get("details", ""),
            blocker=data.get("blocker", ""),
            next_action=data.get("next_action", ""),
        )

    def list(self, workspace_id: str) -> list[Heartbeat]:
        """List all Heartbeats for a workspace, sorted by filename."""
        hb_dir = self.heartbeats_dir(workspace_id)
        if not hb_dir.is_dir():
            return []

        heartbeats = []
        for path in sorted(hb_dir.iterdir()):
            if path.suffix == ".yaml":
                try:
                    heartbeats.append(self.load(workspace_id, path.stem))
                except Exception:
                    logger.warning("Failed to load heartbeat: %s", path, exc_info=True)

        return heartbeats

    def list_by_operation(self, workspace_id: str, operation_id: str) -> list[Heartbeat]:
        """List all Heartbeats for a specific Operation, newest first."""
        hbs = [
            hb for hb in self.list(workspace_id)
            if hb.operation_id == operation_id
        ]
        hbs.sort(key=lambda h: h.timestamp, reverse=True)
        return hbs


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
