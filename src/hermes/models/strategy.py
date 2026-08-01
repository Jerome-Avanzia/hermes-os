from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Strategy:
    """Canonical Hermes OS Strategy object (specs/strategy.md)."""

    strategy_id: str
    business_id: str
    title: str
    objective: str
    status: str
    owner: str | None = None
    review_frequency: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
