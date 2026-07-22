from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.kernel.project_resolver import ProjectResolver
from hermes.kernel.workspace_engine import WorkspaceEngine
from hermes.models import Context, Task


class ContextEngine:
    def __init__(
        self,
        project_resolver: ProjectResolver | None = None,
        knowledge_engine: KnowledgeEngine | None = None,
        workspace_engine: WorkspaceEngine | None = None,
        capability_engine: CapabilityEngine | None = None,
    ) -> None:
        self.project_resolver = project_resolver or ProjectResolver()
        self.knowledge_engine = knowledge_engine or KnowledgeEngine()
        self.workspace_engine = workspace_engine or WorkspaceEngine()
        self.capability_engine = capability_engine or CapabilityEngine()

    def build(self, task: Task) -> Context:
        project = self.project_resolver.resolve(task)
        knowledge = self.knowledge_engine.load(project.id)
        workspace = self.workspace_engine.resolve(project.id)
        capabilities = self.capability_engine.match(task)

        return Context(
            task=task,
            project=project,
            knowledge=knowledge,
            workspace=workspace,
            capabilities=capabilities,
        )
