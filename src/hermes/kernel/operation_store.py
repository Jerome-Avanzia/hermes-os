"""Persistent storage for Operations.

Operations are stored as YAML files in workspaces/{workspace_id}/operations/.
Unknown fields are preserved across load/save cycles.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from hermes.models import Operation

logger = logging.getLogger(__name__)

_KNOWN_FIELDS = frozenset({
    "version", "id", "workspace_id", "request", "status",
    "created_at", "updated_at",
    "outcome", "outcome_classification",
    "decision_id", "recommendation_id", "review_id",
})

_PERSISTENCE_VERSION = 1


class OperationNotFoundError(Exception):
    pass


class OperationStore:
    def __init__(self, workspaces_root: Path = Path("workspaces")) -> None:
        self.workspaces_root = Path(workspaces_root)

    def operations_dir(self, workspace_id: str) -> Path:
        return self.workspaces_root / workspace_id / "operations"

    def save(self, operation: Operation) -> None:
        """Persist an Operation to YAML."""
        ops_dir = self.operations_dir(operation.workspace_id)
        ops_dir.mkdir(parents=True, exist_ok=True)
        path = ops_dir / f"{operation.id}.yaml"

        data: dict[str, Any] = dict(operation.extra_fields)
        data.update({
            "version": _PERSISTENCE_VERSION,
            "id": operation.id,
            "workspace_id": operation.workspace_id,
            "request": operation.request,
            "status": operation.status,
            "created_at": operation.created_at.isoformat(),
            "updated_at": operation.updated_at.isoformat(),
        })
        # Persist optional fields only when set
        for opt in ("outcome", "outcome_classification",
                     "decision_id", "recommendation_id", "review_id"):
            val = getattr(operation, opt)
            if val is not None:
                data[opt] = val

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info("Saved operation %s to %s", operation.id, path)

    def load(self, workspace_id: str, operation_id: str) -> Operation:
        """Load an Operation from YAML."""
        path = self.operations_dir(workspace_id) / f"{operation_id}.yaml"
        if not path.is_file():
            raise OperationNotFoundError(
                f"Operation not found: {operation_id} in workspace {workspace_id}"
            )

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        extra = {k: v for k, v in data.items() if k not in _KNOWN_FIELDS}

        return Operation(
            id=data["id"],
            workspace_id=data["workspace_id"],
            request=data["request"],
            status=data["status"],
            created_at=_parse_datetime(data["created_at"]),
            updated_at=_parse_datetime(data["updated_at"]),
            outcome=data.get("outcome"),
            outcome_classification=data.get("outcome_classification"),
            decision_id=data.get("decision_id"),
            recommendation_id=data.get("recommendation_id"),
            review_id=data.get("review_id"),
            extra_fields=extra,
        )

    def list(self, workspace_id: str) -> list[Operation]:
        """List all Operations for a workspace."""
        ops_dir = self.operations_dir(workspace_id)
        if not ops_dir.is_dir():
            return []

        operations = []
        for path in sorted(ops_dir.iterdir()):
            if path.suffix == ".yaml":
                try:
                    operations.append(self.load(workspace_id, path.stem))
                except Exception:
                    logger.warning("Failed to load operation: %s", path, exc_info=True)

        return operations


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
