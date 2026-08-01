from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class KPI:
    """Canonical Hermes OS KPI object (specs/kpi.md)."""

    kpi_id: str
    business_id: str
    goal_id: str
    name: str
    unit: str
    current_value: float
    target_value: float
    frequency: str
    status: str
    owner: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
