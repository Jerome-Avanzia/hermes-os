from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Lesson:
    """Canonical Hermes OS Lesson object (specs/lesson.md)."""

    lesson_id: str
    business_id: str
    title: str
    summary: str
    source: str
    recommendation: str
    date: str
    decision_id: str | None = None
    experiment_id: str | None = None
    owner: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
