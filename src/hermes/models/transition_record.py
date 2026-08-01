from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TransitionRecord:
    """A single lifecycle transition event (DEC-0005)."""

    timestamp: str
    entity_type: str
    entity_id: str
    from_status: str
    to_status: str
    triggered_by: str
    reason: str
