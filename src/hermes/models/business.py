from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Business:
    """Canonical Hermes OS Business object (specs/business.md)."""

    business_id: str
    name: str
    mission: str
    status: str | None = None
    created_at: str | None = None
    vision: str | None = None
    owner: str | None = None
    updated_at: str | None = None
