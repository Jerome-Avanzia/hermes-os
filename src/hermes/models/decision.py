from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Decision:
    """Canonical Hermes OS Decision object (specs/decision.md)."""

    decision_id: str
    business_id: str
    title: str
    context: str
    rationale: str
    status: str
    decision_date: str
    strategy_id: str | None = None
    goal_id: str | None = None
    owner: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
