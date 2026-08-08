"""EngineeringJob — persistent state for an async engineering task dispatched via the REST API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EngineeringJob:
    job_id: str
    workspace_id: str
    task: str
    repo: str
    status: str              # "pending" | "running" | "completed" | "failed"
    dispatched_at: str       # ISO 8601
    completed_at: str | None
    commit_sha: str | None
    files_changed: tuple[str, ...] | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "workspace_id": self.workspace_id,
            "task": self.task,
            "repo": self.repo,
            "status": self.status,
            "dispatched_at": self.dispatched_at,
            "completed_at": self.completed_at,
            "commit_sha": self.commit_sha,
            "files_changed": list(self.files_changed) if self.files_changed is not None else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineeringJob":
        files_changed_raw = data.get("files_changed")
        files_changed = tuple(files_changed_raw) if files_changed_raw is not None else None
        return cls(
            job_id=data["job_id"],
            workspace_id=data["workspace_id"],
            task=data["task"],
            repo=data["repo"],
            status=data["status"],
            dispatched_at=data["dispatched_at"],
            completed_at=data.get("completed_at"),
            commit_sha=data.get("commit_sha"),
            files_changed=files_changed,
            error=data.get("error"),
        )
