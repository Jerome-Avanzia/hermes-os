from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExecutiveBrief:
    """Canonical Hermes OS Executive Brief object (specs/executive-brief.md)."""

    brief_id: str
    business_id: str
    reporting_period: str
    generated_at: str
    summary: str
    priorities: list[str]
    recommendations: list[str]
    status: str
    risks: list[str] = field(default_factory=list)
    created_at: str | None = None
