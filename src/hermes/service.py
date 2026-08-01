from collections.abc import Iterator
from dataclasses import replace

from hermes.conductor import Conductor
from hermes.kernel.executor import Executor
from hermes.kernel.file_content_reader import FileContentReader
from hermes.kernel.file_selector import FileSelector
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.kernel.workspace_reader import WorkspaceReader
from hermes.models import DiagnosticsReport, ExecutionResult, Task
from hermes.providers.ai_provider import AIProvider
from hermes.providers.ollama_provider import ChatMessage
from hermes.runtime.context_engine import ContextEngine

_MAX_KNOWLEDGE_DOCS_IN_PROMPT = 3


class HermesService:
    def __init__(
        self,
        context_engine: ContextEngine | None = None,
        planner: Planner | None = None,
        skill_loader: SkillLoader | None = None,
        workspace_reader: WorkspaceReader | None = None,
        file_selector: FileSelector | None = None,
        file_content_reader: FileContentReader | None = None,
        executor: Executor | None = None,
        conductor: Conductor | None = None,
    ) -> None:
        self.context_engine = context_engine or ContextEngine()
        self.planner = planner or Planner()
        self.skill_loader = skill_loader or SkillLoader()
        self.workspace_reader = workspace_reader or WorkspaceReader()
        self.file_selector = file_selector or FileSelector()
        self.file_content_reader = file_content_reader or FileContentReader()
        self.executor = executor or Executor()
        self.conductor = conductor

    def generate(
        self, task: str, provider: AIProvider | None = None, project: str = ""
    ) -> ExecutionResult:
        hermes_task = Task(id="hermes-service", business=project, request=task)

        context = self.context_engine.build(hermes_task)
        plan = self.planner.create(context)
        skills = self.skill_loader.load(plan)

        full_snapshot = self.workspace_reader.read(context.workspace)
        workspace_snapshot = self.file_selector.select(full_snapshot, hermes_task)
        file_contents, chars_read, chars_truncated = self.file_content_reader.read_with_stats(
            workspace_snapshot
        )

        knowledge_docs = context.knowledge.documents[:_MAX_KNOWLEDGE_DOCS_IN_PROMPT]
        knowledge_chars = sum(
            len(f"## {doc.title}\n\n{doc.content}") for doc in knowledge_docs
        )
        file_content_chars = sum(len(fc.content) for fc in file_contents)
        prompt_chars = knowledge_chars + file_content_chars

        diagnostics = DiagnosticsReport(
            project_id=context.project.id,
            repositories=[r.name for r in context.workspace.repositories],
            knowledge_documents=[doc.title for doc in context.knowledge.documents],
            files_scanned=len(full_snapshot.files),
            files_selected=[f.path for f in workspace_snapshot.files],
            files_read=len(file_contents),
            chars_read=chars_read,
            chars_truncated=chars_truncated,
            knowledge_chars=knowledge_chars,
            file_content_chars=file_content_chars,
            prompt_chars=prompt_chars,
        )

        result = self.executor.execute(
            plan, skills, workspace_snapshot, provider=provider, file_contents=file_contents
        )
        return replace(result, diagnostics=diagnostics)

    def stream_chat(
        self,
        messages: list[ChatMessage],
        workspace_id: str,
        profile_id: str | None = None,
    ) -> Iterator[str]:
        """Stream a conversation through the full context assembly pipeline.

        Gateway → HermesService → ContextEngine → Conductor → Provider.
        """
        if self.conductor is None:
            raise RuntimeError("HermesService requires a Conductor for chat")

        query = self._extract_last_user_message(messages)
        context = self.context_engine.build_conversation(workspace_id, profile_id, query=query)
        return self.conductor.stream_chat_with_context(messages, context)

    @staticmethod
    def _extract_last_user_message(messages: list[ChatMessage]) -> str | None:
        for msg in reversed(messages):
            if msg.role == "user" and msg.content and msg.content.strip():
                return msg.content
        return None

    def chat(
        self,
        messages: list[ChatMessage],
        workspace_id: str,
        profile_id: str | None = None,
    ) -> str:
        """Non-streaming conversation through the full context assembly pipeline."""
        return "".join(self.stream_chat(messages, workspace_id, profile_id))
