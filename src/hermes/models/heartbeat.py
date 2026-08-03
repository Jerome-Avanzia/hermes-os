"""Heartbeat — lightweight execution status update for an active Operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


HEARTBEAT_STATUSES = frozenset({"active", "blocked", "waiting"})


@dataclass(slots=True)
class Heartbeat:
    id: str                          # e.g. "HB-001"
    operation_id: str
    workspace_id: str
    timestamp: datetime              # UTC
    status: str                      # active | blocked | waiting
    summary: str                     # What changed / what's happening
    author: str = ""                 # Who posted this heartbeat
    details: str = ""                # Optional longer context
    blocker: str = ""                # What's blocking, if status == blocked
    next_action: str = ""            # What happens next
