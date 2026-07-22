from dataclasses import dataclass
from datetime import datetime

from hermes.models.project import Project
from hermes.models.task import Task


@dataclass(slots=True)
class ExecutionResult:
    task: Task
    project: Project
    completed_steps: list[str]
    status: str
    started_at: datetime
    finished_at: datetime
