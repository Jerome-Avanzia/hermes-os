"""OperationProgress — step-by-step progress for an SOP-linked Operation."""

from __future__ import annotations

from dataclasses import dataclass

from hermes.models.sop_step import SOPStep


@dataclass(slots=True)
class OperationProgress:
    sop_id: str
    total_steps: int
    completed_steps: int
    completion_pct: int        # 0–100
    steps: list[SOPStep]
    all_complete: bool
    current_step: str | None   # ID of next incomplete step, or None
