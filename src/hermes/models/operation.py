from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


OPERATION_STATUSES = frozenset({
    "created",
    "executing",
    "awaiting_escalation",
    "completed",
    "rejected",
    "failed",
})

VALID_TRANSITIONS: dict[str, list[str]] = {
    "created": ["executing"],
    "executing": ["completed", "awaiting_escalation", "failed"],
    "awaiting_escalation": ["executing", "rejected"],
}


class InvalidTransitionError(Exception):
    pass


@dataclass(slots=True)
class Operation:
    """A tracked, lifecycle-bound unit of work within a Workspace (ADR-0004)."""

    id: str
    workspace_id: str
    request: str
    status: str
    created_at: datetime
    updated_at: datetime
    extra_fields: dict[str, Any] = field(default_factory=dict)


def transition_operation(operation: Operation, new_status: str) -> None:
    """Validate and apply a lifecycle transition in place.

    Raises InvalidTransitionError if the transition is not allowed.
    """
    allowed = VALID_TRANSITIONS.get(operation.status, [])
    if new_status not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from '{operation.status}' to '{new_status}'"
        )
    operation.status = new_status
    operation.updated_at = datetime.now(operation.created_at.tzinfo)
