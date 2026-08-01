from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Goal:
    """Canonical Hermes OS Goal object (specs/goal.md)."""

    goal_id: str
    business_id: str
    title: str
    description: str
    target_value: str
    target_date: str
    owner: str
    status: str
    strategy_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
