"""Notification — ephemeral executive attention item computed from business state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


NOTIFICATION_SEVERITIES = frozenset({"info", "warning", "critical"})

NOTIFICATION_CATEGORIES = frozenset({
    "operation_blocked",
    "operation_stale",
    "operation_escalated",
    "kpi_off_track",
    "kpi_at_risk",
    "recommendation",
    "bottleneck",
})


@dataclass(slots=True)
class Notification:
    id: str                          # deterministic, e.g. "notif-blocked-OP-20260803-001"
    timestamp: datetime              # when the condition was detected
    severity: str                    # critical | warning | info
    category: str                    # from NOTIFICATION_CATEGORIES
    title: str
    summary: str
    related_object_type: str         # "operation" | "kpi" | "recommendation" | "bottleneck"
    related_object_id: str           # the ID of the related object
    acknowledged: bool = False
