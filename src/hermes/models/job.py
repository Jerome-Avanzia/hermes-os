from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Job:
    """A discrete execution run within an Operation (ADR-0004)."""

    id: str
    workspace_id: str
    operation_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    completed_steps: list[str]
    generated_output: str | None
    created_at: datetime
    updated_at: datetime
    extra_fields: dict[str, Any] = field(default_factory=dict)
