from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Bottleneck:
    """Canonical Hermes OS Bottleneck object (specs/bottleneck.md)."""

    bottleneck_id: str
    business_id: str
    title: str
    category: str
    impact: str
    status: str
    description: str | None = None
    owner: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
