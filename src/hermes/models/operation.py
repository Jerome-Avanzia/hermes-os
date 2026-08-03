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

# Persistent statuses written to Operations.md (Business Knowledge layer).
# awaiting_escalation is an internal workspace lifecycle state, not persisted.
BK_OPERATION_STATUSES = frozenset({
    "created",
    "executing",
    "completed",
    "failed",
    "rejected",
})

# Structured outcome classification for analytics.
OUTCOME_CLASSIFICATIONS = frozenset({
    "success",
    "partial",
    "failure",
    "cancelled",
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
    outcome: str | None = None
    outcome_classification: str | None = None
    decision_id: str | None = None
    recommendation_id: str | None = None
    review_id: str | None = None
    sop_id: str | None = None
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
