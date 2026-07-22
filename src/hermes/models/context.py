from dataclasses import dataclass

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
