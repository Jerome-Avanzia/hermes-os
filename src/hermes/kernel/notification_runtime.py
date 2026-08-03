"""NotificationRuntime — compute executive notifications from existing state.

Notifications are ephemeral — regenerated on each request from live
operations, heartbeats, and CEO review data.  Only acknowledgement
state is persisted separately.
"""

from __future__ import annotations

from datetime import datetime, timezone

from hermes.kernel.heartbeat_runtime import find_stale_operations, get_latest
from hermes.kernel.heartbeat_store import HeartbeatStore
from hermes.models.notification import Notification
from hermes.models.operation import Operation

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def generate_notifications(
    operations: list[Operation],
    heartbeat_store: HeartbeatStore | None,
    review: object | None,
    workspace_id: str,
    stale_threshold_hours: float = 24.0,
    now: datetime | None = None,
) -> list[Notification]:
    """Compute all notifications from current business state.

    Sources: operations (blocked, stale, escalated), CEO review
    (KPIs, bottlenecks, recommendations).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    notifications: list[Notification] = []

    # -- Operation-sourced notifications --
    active_statuses = {"created", "executing", "awaiting_escalation"}

    for op in operations:
        # Escalated operations → critical
        if op.status == "awaiting_escalation":
            notifications.append(Notification(
                id=f"notif-escalated-{op.id}",
                timestamp=now,
                severity="critical",
                category="operation_escalated",
                title=f"Operation awaiting approval: {op.request}",
                summary=f"Operation {op.id} requires Founder approval to proceed.",
                related_object_type="operation",
                related_object_id=op.id,
            ))

        # Blocked operations → critical (latest heartbeat status == blocked)
        if op.status in active_statuses and heartbeat_store is not None:
            latest = get_latest(heartbeat_store, workspace_id, op.id)
            if latest and latest.status == "blocked":
                blocker_text = f" Blocker: {latest.blocker}" if latest.blocker else ""
                notifications.append(Notification(
                    id=f"notif-blocked-{op.id}",
                    timestamp=now,
                    severity="critical",
                    category="operation_blocked",
                    title=f"Operation blocked: {op.request}",
                    summary=f"{latest.summary}{blocker_text}",
                    related_object_type="operation",
                    related_object_id=op.id,
                ))

    # Stale operations → warning
    if heartbeat_store is not None:
        stale_ops = find_stale_operations(
            operations, heartbeat_store, workspace_id,
            stale_threshold_hours, now=now,
        )
        for stale in stale_ops:
            notifications.append(Notification(
                id=f"notif-stale-{stale.operation_id}",
                timestamp=now,
                severity="warning",
                category="operation_stale",
                title=f"Operation stale: {stale.request} ({stale.elapsed_hours}h)",
                summary=f"No heartbeat for {stale.elapsed_hours} hours.",
                related_object_type="operation",
                related_object_id=stale.operation_id,
            ))

    # -- CEO Review-sourced notifications --
    if review is not None:
        _add_review_notifications(review, notifications, now)

    # Sort: critical first, then warning, then info; within same severity newest first
    notifications.sort(key=lambda n: (_SEVERITY_ORDER.get(n.severity, 9), n.id))

    return notifications


def _add_review_notifications(
    review: object,
    notifications: list[Notification],
    now: datetime,
) -> None:
    """Extract notifications from CEO review data."""
    data = getattr(review, "data", None)
    if data is None:
        return

    # KPIs
    for kpi in getattr(data, "kpis", []):
        if kpi.status == "off_track":
            notifications.append(Notification(
                id=f"notif-kpi-offtrack-{kpi.kpi_id}",
                timestamp=now,
                severity="warning",
                category="kpi_off_track",
                title=f"KPI off track: {kpi.name}",
                summary=f"Current: {kpi.current_value}, Target: {kpi.target_value}",
                related_object_type="kpi",
                related_object_id=kpi.kpi_id,
            ))
        elif kpi.status == "at_risk":
            notifications.append(Notification(
                id=f"notif-kpi-atrisk-{kpi.kpi_id}",
                timestamp=now,
                severity="info",
                category="kpi_at_risk",
                title=f"KPI at risk: {kpi.name}",
                summary=f"Current: {kpi.current_value}, Target: {kpi.target_value}",
                related_object_type="kpi",
                related_object_id=kpi.kpi_id,
            ))

    # Bottlenecks
    for bot in getattr(data, "bottlenecks", []):
        if getattr(bot, "status", "") == "active":
            notifications.append(Notification(
                id=f"notif-bottleneck-{bot.bottleneck_id}",
                timestamp=now,
                severity="warning",
                category="bottleneck",
                title=f"Bottleneck: {bot.title}",
                summary=f"Impact: {bot.impact}",
                related_object_type="bottleneck",
                related_object_id=bot.bottleneck_id,
            ))

    # Recommendations
    engine_result = getattr(review, "engine_result", None)
    if engine_result is not None:
        for rec in getattr(engine_result, "recommendations", []):
            notifications.append(Notification(
                id=f"notif-rec-{rec.recommendation_id}",
                timestamp=now,
                severity="info",
                category="recommendation",
                title=f"Recommendation: {rec.title}",
                summary=rec.context,
                related_object_type="recommendation",
                related_object_id=rec.recommendation_id,
            ))


def apply_acknowledgements(
    notifications: list[Notification],
    acknowledged_ids: set[str],
) -> list[Notification]:
    """Set acknowledged=True on notifications whose IDs are in the set."""
    for n in notifications:
        if n.id in acknowledged_ids:
            n.acknowledged = True
    return notifications


def filter_unread(notifications: list[Notification]) -> list[Notification]:
    """Return only unacknowledged notifications."""
    return [n for n in notifications if not n.acknowledged]
