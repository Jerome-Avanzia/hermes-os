import logging

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.context_manager import ContextManager
from hermes.kernel.knowledge_engine import KnowledgeEngine
from hermes.kernel.model_router import ModelRouter
from hermes.kernel.profile_loader import ProfileLoader
from hermes.kernel.prompt_compression import PromptCompression
from hermes.kernel.project_resolver import ProjectResolver
from hermes.kernel.workspace_engine import WorkspaceEngine
from hermes.models import Context, KnowledgeContext, KnowledgeDocument, Project, Task
from hermes.models.context_package import ContextPackage
from hermes.models.prompt_package import PromptPackage
from hermes.models.routing_decision import (
    ModelEntry,
    RoutingDecision,
    RoutingPolicy,
)

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
        self.context_manager = ContextManager(
            knowledge_engine=self.knowledge_engine,
            capability_engine=self.capability_engine,
        )
        self.prompt_compression = PromptCompression()

    def assemble_context(
        self,
        query: str,
        workspace_id: str,
        max_knowledge: int = 10,
        max_capabilities: int = 5,
    ) -> ContextPackage:
        """Assemble a typed ContextPackage for the given query.

        Delegates to ContextManager for deterministic context selection.
        """
        return self.context_manager.assemble(
            query=query,
            workspace_id=workspace_id,
            max_knowledge=max_knowledge,
            max_capabilities=max_capabilities,
        )

    def compress_prompt(
        self,
        query: str,
        workspace_id: str,
        budget: str | int = "8k",
        max_knowledge: int = 10,
        max_capabilities: int = 5,
    ) -> PromptPackage:
        """Assemble context and compress into a PromptPackage.

        End-to-end pipeline: Context Manager → Prompt Compression.
        """
        package = self.assemble_context(
            query=query,
            workspace_id=workspace_id,
            max_knowledge=max_knowledge,
            max_capabilities=max_capabilities,
        )

        # Build document content map from the knowledge engine
        documents = self._load_document_map(package)

        return self.prompt_compression.compress(
            package=package,
            documents=documents,
            budget=budget,
        )

    def _load_document_map(
        self, package: ContextPackage,
    ) -> dict[str, KnowledgeDocument]:
        """Load document content for all references in a ContextPackage."""
        doc_map: dict[str, KnowledgeDocument] = {}

        try:
            context = self.knowledge_engine.load_with_architecture(
                package.workspace_id,
            )
            for doc in context.documents:
                doc_map[doc.id] = doc
        except (ValueError, FileNotFoundError):
            pass

        return doc_map

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
