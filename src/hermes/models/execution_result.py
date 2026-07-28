from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from hermes.models.project import Project
from hermes.models.task import Task

if TYPE_CHECKING:
    from hermes.models.diagnostics_report import DiagnosticsReport


@dataclass(slots=True)
class ExecutionResult:
    task: Task
    project: Project
    completed_steps: list[str]
    status: str
    started_at: datetime
    finished_at: datetime
    generated_output: str | None
    diagnostics: DiagnosticsReport | None = field(default=None)
