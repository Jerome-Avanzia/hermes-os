from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.kernel.project_resolver import ProjectResolver
from hermes.models import Context, Task


class ContextEngine:
    def __init__(
        self,
        project_resolver: ProjectResolver | None = None,
        knowledge_engine: KnowledgeEngine | None = None,
    ) -> None:
        self.project_resolver = project_resolver or ProjectResolver()
        self.knowledge_engine = knowledge_engine or KnowledgeEngine()

    def build(self, task: Task) -> Context:
        project = self.project_resolver.resolve(task)
        knowledge = self.knowledge_engine.load(project.id)

        return Context(
            task=task,
            capabilities=[],
            knowledge=knowledge,
        )
