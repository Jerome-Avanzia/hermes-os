import logging

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.kernel.profile_loader import ProfileLoader
from hermes.kernel.project_resolver import ProjectResolver
from hermes.kernel.workspace_engine import WorkspaceEngine
from hermes.models import Context, KnowledgeContext, Project, Task

logger = logging.getLogger(__name__)


class ContextEngine:
    def __init__(
        self,
        project_resolver: ProjectResolver | None = None,
        knowledge_engine: KnowledgeEngine | None = None,
        workspace_engine: WorkspaceEngine | None = None,
        capability_engine: CapabilityEngine | None = None,
        profile_loader: ProfileLoader | None = None,
    ) -> None:
        self.project_resolver = project_resolver or ProjectResolver()
        self.knowledge_engine = knowledge_engine or KnowledgeEngine()
        self.workspace_engine = workspace_engine or WorkspaceEngine()
        self.capability_engine = capability_engine or CapabilityEngine()
        self.profile_loader = profile_loader or ProfileLoader()

    def build(self, task: Task) -> Context:
        project = self.project_resolver.resolve(task)
        knowledge = self.knowledge_engine.load(project.id)
        workspace = self.workspace_engine.resolve(project.id)
        capabilities = self.capability_engine.match(task)

        logger.info(
            "Built context for task %s: project=%s capabilities=%d",
            task.id,
            project.id,
            len(capabilities),
        )

        return Context(
            task=task,
            project=project,
            knowledge=knowledge,
            workspace=workspace,
            capabilities=capabilities,
        )

    def build_conversation(
        self,
        workspace_id: str,
        profile_id: str | None = None,
        query: str | None = None,
    ) -> Context:
        """Assemble context for a conversation.

        Resolves workspace (identity, organization, operational state),
        profile, and knowledge into a single Context.  When *query* is
        provided, knowledge documents are selected by relevance.
        """
        workspace_context = self.workspace_engine.resolve(workspace_id)

        if profile_id:
            profile = self.profile_loader.get(profile_id)
        else:
            profile = self.profile_loader.get_default()

        project = Project(
            id=workspace_id,
            name=workspace_context.workspace.name or workspace_id,
            path=workspace_context.workspace.path,
        )

        try:
            knowledge = self.knowledge_engine.load_with_architecture(workspace_id)
        except (ValueError, FileNotFoundError):
            logger.info("No knowledge found for workspace %s", workspace_id)
            knowledge = KnowledgeContext(project=project, documents=[])

        if knowledge.documents:
            selected = self.knowledge_engine.select(knowledge.documents, query)
            knowledge = KnowledgeContext(project=knowledge.project, documents=selected)

        task = Task(id="conversation", business=workspace_id, request="")

        logger.info(
            "Built conversation context: workspace=%s profile=%s knowledge=%d",
            workspace_id,
            profile.id,
            len(knowledge.documents),
        )

        return Context(
            task=task,
            project=project,
            knowledge=knowledge,
            workspace=workspace_context,
            capabilities=[],
            profile=profile,
        )
