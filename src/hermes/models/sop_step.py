"""SOPStep — a single step within a Standard Operating Procedure."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SOPStep:
    id: str                          # slug-based, e.g. "step-draft-review"
    index: int                       # zero-based order
    title: str                       # e.g. "Draft Review"
    description: str = ""            # text after em-dash
    completed: bool = False
    completed_at: str | None = None  # ISO8601 string
