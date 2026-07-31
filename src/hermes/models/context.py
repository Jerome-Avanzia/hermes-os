from dataclasses import dataclass

from hermes.models.capability import Capability
from hermes.models.knowledge_context import KnowledgeContext
from hermes.models.project import Project
from hermes.models.task import Task
from hermes.models.workspace_context import WorkspaceContext


@dataclass(slots=True)
class Context:
    task: Task
    project: Project
    knowledge: KnowledgeContext
    workspace: WorkspaceContext
    capabilities: list[Capability]

    def __str__(self) -> str:
        capability_ids = ", ".join(capability.id for capability in self.capabilities)
        return (
            f"Context(task={self.task.id}, project={self.project}, "
            f"knowledge={self.knowledge}, workspace={self.workspace}, "
            f"capabilities=[{capability_ids or '-'}])"
        )
