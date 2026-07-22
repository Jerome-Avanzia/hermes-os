from dataclasses import dataclass

from hermes.models.knowledge_context import KnowledgeContext
from hermes.models.task import Task


@dataclass(slots=True)
class Context:
    task: Task
    capabilities: list[str]
    knowledge: KnowledgeContext
