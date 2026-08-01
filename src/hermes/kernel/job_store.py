"""Persistent storage for Jobs.

Jobs are stored as YAML files in workspaces/{workspace_id}/jobs/.
Unknown fields are preserved across load/save cycles.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from hermes.models import Job

logger = logging.getLogger(__name__)

_KNOWN_FIELDS = frozenset({
    "version", "id", "workspace_id", "operation_id", "status",
    "started_at", "finished_at", "completed_steps", "generated_output",
    "created_at", "updated_at",
})

_PERSISTENCE_VERSION = 1


class JobNotFoundError(Exception):
    pass


class JobStore:
    def __init__(self, workspaces_root: Path = Path("workspaces")) -> None:
        self.workspaces_root = Path(workspaces_root)

    def jobs_dir(self, workspace_id: str) -> Path:
        return self.workspaces_root / workspace_id / "jobs"

    def save(self, job: Job) -> None:
        """Persist a Job to YAML."""
        j_dir = self.jobs_dir(job.workspace_id)
        j_dir.mkdir(parents=True, exist_ok=True)
        path = j_dir / f"{job.id}.yaml"

        data: dict[str, Any] = dict(job.extra_fields)
        data.update({
            "version": _PERSISTENCE_VERSION,
            "id": job.id,
            "workspace_id": job.workspace_id,
            "operation_id": job.operation_id,
            "status": job.status,
            "started_at": job.started_at.isoformat(),
            "finished_at": job.finished_at.isoformat(),
            "completed_steps": list(job.completed_steps),
            "generated_output": job.generated_output,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        })

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)

        logger.info("Saved job %s to %s", job.id, path)

    def load(self, workspace_id: str, job_id: str) -> Job:
        """Load a Job from YAML."""
        path = self.jobs_dir(workspace_id) / f"{job_id}.yaml"
        if not path.is_file():
            raise JobNotFoundError(
                f"Job not found: {job_id} in workspace {workspace_id}"
            )

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        extra = {k: v for k, v in data.items() if k not in _KNOWN_FIELDS}

        return Job(
            id=data["id"],
            workspace_id=data["workspace_id"],
            operation_id=data["operation_id"],
            status=data["status"],
            started_at=_parse_datetime(data["started_at"]),
            finished_at=_parse_datetime(data["finished_at"]),
            completed_steps=data.get("completed_steps", []),
            generated_output=data.get("generated_output"),
            created_at=_parse_datetime(data["created_at"]),
            updated_at=_parse_datetime(data["updated_at"]),
            extra_fields=extra,
        )

    def list(self, workspace_id: str) -> list[Job]:
        """List all Jobs for a workspace, sorted by filename."""
        j_dir = self.jobs_dir(workspace_id)
        if not j_dir.is_dir():
            return []

        jobs = []
        for path in sorted(j_dir.iterdir()):
            if path.suffix == ".yaml":
                try:
                    jobs.append(self.load(workspace_id, path.stem))
                except Exception:
                    logger.warning("Failed to load job: %s", path, exc_info=True)

        return jobs

    def list_by_operation(self, workspace_id: str, operation_id: str) -> list[Job]:
        """List all Jobs for a specific Operation."""
        return [
            j for j in self.list(workspace_id)
            if j.operation_id == operation_id
        ]


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
