"""HeartbeatRuntime — append, retrieve, freshness, and stale detection.

Pure functions operating on HeartbeatStore and Operation data.
No timers, no scheduling, no background workers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hermes.kernel.heartbeat_id import generate_heartbeat_id
from hermes.kernel.heartbeat_store import HeartbeatStore
from hermes.models.heartbeat import HEARTBEAT_STATUSES, Heartbeat
from hermes.models.operation import Operation
from hermes.models.stale_operation import StaleOperation


class InvalidHeartbeatStatusError(Exception):
    pass


def append_heartbeat(
    store: HeartbeatStore,
    operation_id: str,
    workspace_id: str,
    status: str,
    summary: str,
    author: str = "",
    details: str = "",
    blocker: str = "",
    next_action: str = "",
) -> Heartbeat:
    """Create and persist a new immutable Heartbeat."""
    if status not in HEARTBEAT_STATUSES:
        raise InvalidHeartbeatStatusError(
            f"Invalid heartbeat status: {status}. "
            f"Must be one of: {', '.join(sorted(HEARTBEAT_STATUSES))}"
        )

    hb_id = generate_heartbeat_id(store.heartbeats_dir(workspace_id))
    now = datetime.now(timezone.utc)

    heartbeat = Heartbeat(
        id=hb_id,
        operation_id=operation_id,
        workspace_id=workspace_id,
        timestamp=now,
        status=status,
        summary=summary,
        author=author,
        details=details,
        blocker=blocker,
        next_action=next_action,
    )
    store.save(heartbeat)
    return heartbeat


def get_latest(
    store: HeartbeatStore, workspace_id: str, operation_id: str,
) -> Heartbeat | None:
    """Return the most recent Heartbeat for an Operation, or None."""
    hbs = store.list_by_operation(workspace_id, operation_id)
    return hbs[0] if hbs else None


def list_heartbeats(
    store: HeartbeatStore, workspace_id: str, operation_id: str,
) -> list[Heartbeat]:
    """Return all Heartbeats for an Operation, newest first."""
    return store.list_by_operation(workspace_id, operation_id)


def compute_freshness(
    heartbeat: Heartbeat, now: datetime | None = None,
) -> timedelta:
    """Return elapsed time since the heartbeat timestamp."""
    if now is None:
        now = datetime.now(timezone.utc)
    return now - heartbeat.timestamp


def find_stale_operations(
    operations: list[Operation],
    store: HeartbeatStore,
    workspace_id: str,
    threshold_hours: float = 24.0,
    now: datetime | None = None,
) -> list[StaleOperation]:
    """Identify active Operations whose latest heartbeat exceeds the threshold.

    Active means status in (created, executing, awaiting_escalation).
    Operations with no heartbeats use created_at as the reference time.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    active_statuses = {"created", "executing", "awaiting_escalation"}
    threshold = timedelta(hours=threshold_hours)
    stale: list[StaleOperation] = []

    for op in operations:
        if op.status not in active_statuses:
            continue

        latest = get_latest(store, workspace_id, op.id)
        if latest is not None:
            elapsed = now - latest.timestamp
        else:
            elapsed = now - op.created_at

        if elapsed >= threshold:
            stale.append(StaleOperation(
                operation_id=op.id,
                workspace_id=workspace_id,
                request=op.request,
                status=op.status,
                latest_heartbeat=latest,
                elapsed_hours=round(elapsed.total_seconds() / 3600, 1),
            ))

    return stale
