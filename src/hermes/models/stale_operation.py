"""StaleOperation — an active Operation whose latest heartbeat exceeds the freshness threshold."""

from __future__ import annotations

from dataclasses import dataclass

from hermes.models.heartbeat import Heartbeat


@dataclass(slots=True)
class StaleOperation:
    operation_id: str
    workspace_id: str
    request: str
    status: str
    latest_heartbeat: Heartbeat | None
    elapsed_hours: float             # Hours since last heartbeat (or since created_at if none)
