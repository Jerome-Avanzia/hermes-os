from dataclasses import dataclass

from hermes.models.execution_step import ExecutionStep
from hermes.models.task import Task


@dataclass(slots=True)
class ExecutionPlan:
    task: Task
    steps: list[ExecutionStep]
