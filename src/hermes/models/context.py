from dataclasses import dataclass

from hermes.models.task import Task


@dataclass(slots=True)
class Context:
    task: Task
    capabilities: list[str]
