from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Opportunity:
    """Canonical Hermes OS Opportunity object (specs/opportunity.md)."""

    opportunity_id: str
    business_id: str
    title: str
    description: str
    expected_impact: str
    estimated_effort: str
    owner: str
    status: str
    strategy_id: str | None = None
    goal_id: str | None = None
    decision_id: str | None = None
    experiment_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
