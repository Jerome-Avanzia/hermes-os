from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Experiment:
    """Canonical Hermes OS Experiment object (specs/experiment.md)."""

    experiment_id: str
    business_id: str
    title: str
    hypothesis: str
    success_criteria: str
    status: str
    opportunity_id: str | None = None
    decision_id: str | None = None
    outcome: str | None = None
    owner: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
