from dataclasses import dataclass

from hermes.models.context import Context
from hermes.models.execution_step import ExecutionStep
from hermes.models.project import Project
from hermes.models.task import Task


@dataclass(slots=True)
class ExecutionPlan:
    task: Task
    project: Project
    context: Context
    steps: list[ExecutionStep]
