from dataclasses import dataclass

from hermes.models.task import Task


@dataclass(slots=True)
class Result:
    task: Task
    status: str
    output: str
