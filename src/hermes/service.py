from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone

from hermes.conductor import Conductor
from hermes.kernel.executor import Executor
from hermes.kernel.file_content_reader import FileContentReader
from hermes.kernel.file_selector import FileSelector
from hermes.kernel.job_id import generate_job_id
from hermes.kernel.job_store import JobNotFoundError, JobStore
from hermes.kernel.operation_id import generate_operation_id
from hermes.kernel.operation_store import OperationNotFoundError, OperationStore
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.kernel.workspace_reader import WorkspaceReader
from hermes.models import DiagnosticsReport, ExecutionResult, Job, Operation, Task
from hermes.models.operation import InvalidTransitionError, transition_operation
from hermes.providers.ai_provider import AIProvider
from hermes.providers.ollama_provider import ChatMessage
from hermes.runtime.context_engine import ContextEngine

logger = logging.getLogger(__name__)

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
        operation_store: OperationStore | None = None,
        job_store: JobStore | None = None,
    ) -> None:
        self.context_engine = context_engine or ContextEngine()
        self.planner = planner or Planner()
        self.skill_loader = skill_loader or SkillLoader()
        self.workspace_reader = workspace_reader or WorkspaceReader()
        self.file_selector = file_selector or FileSelector()
        self.file_content_reader = file_content_reader or FileContentReader()
        self.executor = executor or Executor()
        self.conductor = conductor
        self.operation_store = operation_store
        self.job_store = job_store

    def generate(
        self, task: str, provider: AIProvider | None = None, project: str = ""
    ) -> ExecutionResult:
        hermes_task = Task(id="hermes-service", business=project, request=task)

        context = self.context_engine.build(hermes_task)

        # Create and transition Operation if stores are configured
        operation = None
        if self.operation_store and self.job_store:
            workspace_id = context.project.id
            now = datetime.now(timezone.utc)
            op_id = generate_operation_id(
                self.operation_store.operations_dir(workspace_id)
            )
            operation = Operation(
                id=op_id,
                workspace_id=workspace_id,
                request=task,
                status="created",
                created_at=now,
                updated_at=now,
            )
            self.operation_store.save(operation)
            transition_operation(operation, "executing")
            self.operation_store.save(operation)

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

        try:
            result = self.executor.execute(
                plan, skills, workspace_snapshot, provider=provider, file_contents=file_contents
            )
        except Exception:
            if operation and self.operation_store:
                transition_operation(operation, "failed")
                self.operation_store.save(operation)
            raise

        # Create Job from result if stores are configured
        if operation and self.job_store:
            now = datetime.now(timezone.utc)
            job_id = generate_job_id(
                self.job_store.jobs_dir(operation.workspace_id)
            )
            job = Job(
                id=job_id,
                workspace_id=operation.workspace_id,
                operation_id=operation.id,
                status=result.status,
                started_at=result.started_at,
                finished_at=result.finished_at,
                completed_steps=list(result.completed_steps),
                generated_output=result.generated_output,
                created_at=now,
                updated_at=now,
            )
            self.job_store.save(job)
            transition_operation(operation, "completed")
            self.operation_store.save(operation)

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

    # -- Operations & Jobs -----------------------------------------------------

    def list_operations(self, workspace_id: str) -> list[dict]:
        """Return all Operations for a workspace, ordered by ID."""
        if not self.operation_store:
            return []
        return [
            self._serialize_operation(op)
            for op in self.operation_store.list(workspace_id)
        ]

    def get_operation(self, workspace_id: str, operation_id: str) -> dict | None:
        """Return a single Operation, or None if not found."""
        if not self.operation_store:
            return None
        try:
            op = self.operation_store.load(workspace_id, operation_id)
        except OperationNotFoundError:
            return None
        return self._serialize_operation(op)

    def approve_operation(self, workspace_id: str, operation_id: str) -> dict:
        """Approve an escalated Operation, returning it to executing.

        Raises OperationNotFoundError if not found.
        Raises InvalidTransitionError if not in awaiting_escalation state.
        """
        op = self.operation_store.load(workspace_id, operation_id)
        transition_operation(op, "executing")
        self.operation_store.save(op)
        return self._serialize_operation(op)

    def reject_operation(self, workspace_id: str, operation_id: str) -> dict:
        """Reject an escalated Operation.

        Raises OperationNotFoundError if not found.
        Raises InvalidTransitionError if not in awaiting_escalation state.
        """
        op = self.operation_store.load(workspace_id, operation_id)
        transition_operation(op, "rejected")
        self.operation_store.save(op)
        return self._serialize_operation(op)

    def list_jobs(self, workspace_id: str) -> list[dict]:
        """Return all Jobs for a workspace, ordered by ID."""
        if not self.job_store:
            return []
        return [
            self._serialize_job(j, include_output=False)
            for j in self.job_store.list(workspace_id)
        ]

    def get_job(self, workspace_id: str, job_id: str) -> dict | None:
        """Return a single Job with full output, or None if not found."""
        if not self.job_store:
            return None
        try:
            j = self.job_store.load(workspace_id, job_id)
        except JobNotFoundError:
            return None
        return self._serialize_job(j, include_output=True)

    @staticmethod
    def _serialize_operation(op: Operation) -> dict:
        return {
            "id": op.id,
            "workspace_id": op.workspace_id,
            "request": op.request,
            "status": op.status,
            "created_at": op.created_at.isoformat(),
            "updated_at": op.updated_at.isoformat(),
        }

    @staticmethod
    def _serialize_job(j: Job, include_output: bool = False) -> dict:
        data = {
            "id": j.id,
            "workspace_id": j.workspace_id,
            "operation_id": j.operation_id,
            "status": j.status,
            "completed_steps": j.completed_steps,
            "started_at": j.started_at.isoformat(),
            "finished_at": j.finished_at.isoformat(),
            "created_at": j.created_at.isoformat(),
            "updated_at": j.updated_at.isoformat(),
        }
        if include_output:
            data["generated_output"] = j.generated_output
        return data

    # -- Dashboard & Knowledge ------------------------------------------------

    def get_dashboard(self, workspace_id: str) -> dict:
        """Return a CEO-oriented workspace operating summary."""
        from hermes.kernel.workspace_engine import WorkspaceNotFoundError

        try:
            ws_context = self.context_engine.workspace_engine.resolve(workspace_id)
            ws = ws_context.workspace
            workspace_info = {
                "name": ws.name or workspace_id,
                "mission": ws.mission or "",
            }
            repositories = len(ws_context.repositories)
        except WorkspaceNotFoundError:
            workspace_info = {"name": workspace_id, "mission": ""}
            repositories = 0

        knowledge_docs = self.list_knowledge(workspace_id)

        return {
            "attention": {
                "count": 0,
                "items": [],
            },
            "operations": {
                "active": 0,
                "completed_today": 0,
                "total": 0,
            },
            "knowledge": {
                "count": len(knowledge_docs),
            },
            "repositories": {
                "count": repositories,
            },
            "workspace": workspace_info,
        }

    def list_knowledge(self, workspace_id: str) -> list[dict]:
        """Return metadata for all Knowledge Documents in a workspace."""
        try:
            context = self.context_engine.knowledge_engine.load(workspace_id)
        except (ValueError, FileNotFoundError):
            return []
        return [
            {
                "id": doc.id,
                "title": doc.title,
                "size": len(doc.content),
                "path": doc.path,
            }
            for doc in context.documents
        ]

    def get_knowledge(self, workspace_id: str, document_id: str) -> dict | None:
        """Return a single Knowledge Document with full content, or None."""
        try:
            context = self.context_engine.knowledge_engine.load(workspace_id)
        except (ValueError, FileNotFoundError):
            return None
        for doc in context.documents:
            if doc.id == document_id:
                return {
                    "id": doc.id,
                    "title": doc.title,
                    "size": len(doc.content),
                    "path": doc.path,
                    "content": doc.content,
                }
        return None
