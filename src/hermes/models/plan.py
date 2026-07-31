from dataclasses import dataclass

from hermes.models.task import Task


@dataclass(slots=True)
class Plan:
    task: Task
    steps: list[str]
