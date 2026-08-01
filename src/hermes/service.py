from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone

from hermes.conductor import Conductor
from hermes.kernel.ceo_loop import CEOLoop
from hermes.kernel.decision_id import generate_decision_id
from hermes.kernel.decision_writer import DecisionWriter
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
from hermes.models.decision import Decision
from hermes.models.operation import InvalidTransitionError, transition_operation
from hermes.providers.ai_provider import AIProvider
from hermes.providers.ollama_provider import ChatMessage
from hermes.kernel.workspace_engine import WorkspaceNotFoundError  # noqa: F401 — re-exported for Gateway
from hermes.runtime.context_engine import ContextEngine

logger = logging.getLogger(__name__)

_MAX_KNOWLEDGE_DOCS_IN_PROMPT = 3

_ACTION_TO_STATUS = {
    "approve": "approved",
    "reject": "closed",
    "postpone": "proposed",
}

VALID_DECISION_ACTIONS = frozenset(_ACTION_TO_STATUS.keys())


class RecommendationNotFoundError(Exception):
    pass


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

    # -- Workspace validation --------------------------------------------------

    def validate_workspace(self, workspace_id: str) -> None:
        """Validate workspace exists. Raises WorkspaceNotFoundError if not."""
        self.context_engine.workspace_engine.validate(workspace_id)

    def list_workspaces(self) -> list[dict[str, str]]:
        """Return summary info for all registered workspaces."""
        return self.context_engine.workspace_engine.list_workspaces()

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
        self.validate_workspace(workspace_id)
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
        # Validation delegated to stream_chat
        return "".join(self.stream_chat(messages, workspace_id, profile_id))

    # -- Conversation-to-Operation Bridge --------------------------------------

    def create_operation_from_chat(self, workspace_id: str, request: str) -> dict:
        """Create an Operation from a conversation promotion.

        The Operation is created in 'created' status only.
        It is NOT executed and no Job is produced.
        """
        self.validate_workspace(workspace_id)
        if not self.operation_store:
            raise RuntimeError("OperationStore is required to create Operations")

        now = datetime.now(timezone.utc)
        op_id = generate_operation_id(
            self.operation_store.operations_dir(workspace_id)
        )
        operation = Operation(
            id=op_id,
            workspace_id=workspace_id,
            request=request,
            status="created",
            created_at=now,
            updated_at=now,
        )
        self.operation_store.save(operation)
        return self._serialize_operation(operation)

    # -- Operations & Jobs -----------------------------------------------------

    def list_operations(self, workspace_id: str) -> list[dict]:
        """Return all Operations for a workspace, ordered by ID."""
        self.validate_workspace(workspace_id)
        if not self.operation_store:
            return []
        return [
            self._serialize_operation(op)
            for op in self.operation_store.list(workspace_id)
        ]

    def get_operation(self, workspace_id: str, operation_id: str) -> dict | None:
        """Return a single Operation, or None if not found."""
        self.validate_workspace(workspace_id)
        if not self.operation_store:
            return None
        try:
            op = self.operation_store.load(workspace_id, operation_id)
        except OperationNotFoundError:
            return None
        return self._serialize_operation(op)

    def approve_operation(self, workspace_id: str, operation_id: str) -> dict:
        """Approve an escalated Operation, returning it to executing.

        Raises WorkspaceNotFoundError if workspace not found.
        Raises OperationNotFoundError if not found.
        Raises InvalidTransitionError if not in awaiting_escalation state.
        """
        self.validate_workspace(workspace_id)
        op = self.operation_store.load(workspace_id, operation_id)
        transition_operation(op, "executing")
        self.operation_store.save(op)
        return self._serialize_operation(op)

    def reject_operation(self, workspace_id: str, operation_id: str) -> dict:
        """Reject an escalated Operation.

        Raises WorkspaceNotFoundError if workspace not found.
        Raises OperationNotFoundError if not found.
        Raises InvalidTransitionError if not in awaiting_escalation state.
        """
        self.validate_workspace(workspace_id)
        op = self.operation_store.load(workspace_id, operation_id)
        transition_operation(op, "rejected")
        self.operation_store.save(op)
        return self._serialize_operation(op)

    # -- Executive Decision Loop -------------------------------------------------

    def act_on_recommendation(
        self,
        workspace_id: str,
        recommendation_id: str,
        action: str,
        create_operation: bool = False,
    ) -> dict:
        """Act on an Executive Brief recommendation to create a tracked Decision.

        Actions: approve → "approved", reject → "closed", postpone → "proposed".
        Appends the Decision to Decisions.md.  Optionally creates an Operation.
        Recommendations are immutable — the Decision only references them.
        """
        self.validate_workspace(workspace_id)

        if action not in VALID_DECISION_ACTIONS:
            raise ValueError(f"Invalid action: {action}. Must be one of: {', '.join(sorted(VALID_DECISION_ACTIONS))}")

        # Run CEO review to find the recommendation
        review = self._run_ceo_review(workspace_id)
        rec = None
        for r in review.engine_result.recommendations:
            if r.recommendation_id == recommendation_id:
                rec = r
                break

        if rec is None:
            raise RecommendationNotFoundError(
                f"Recommendation not found: {recommendation_id}"
            )

        # Resolve business directory for ID generation and file write
        business_dir = self.context_engine.workspace_engine.resolve_business_dir(
            workspace_id
        )
        decision_id = generate_decision_id(business_dir)

        now = datetime.now(timezone.utc)
        decision_date = now.strftime("%Y-%m")
        status = _ACTION_TO_STATUS[action]

        decision = Decision(
            decision_id=decision_id,
            business_id=review.business_id,
            title=rec.title,
            context=f"Source: {rec.source_type}/{rec.source_id}. {rec.context}",
            rationale=rec.rationale,
            status=status,
            decision_date=decision_date,
            owner="Founder",
            recommendation_id=rec.recommendation_id,
            review_id=review.review_id,
            brief_id=review.brief.brief_id,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )

        # Append to Decisions.md
        writer = DecisionWriter()
        writer.append_decision(business_dir, decision)

        result: dict = {"decision": self._serialize_decision(decision)}

        # Optionally create an Operation for approved decisions
        if create_operation and action == "approve" and self.operation_store:
            op_dict = self.create_operation_from_chat(
                workspace_id,
                f"[{decision_id}] {rec.title}",
            )
            result["operation"] = op_dict
        else:
            result["operation"] = None

        return result

    def list_decisions(self, workspace_id: str) -> list[dict]:
        """Return all Decisions from the business knowledge layer."""
        self.validate_workspace(workspace_id)
        review = self._run_ceo_review(workspace_id)
        return [
            {
                "id": d.decision_id,
                "title": d.title,
                "date": d.decision_date,
                "status": d.status,
                "rationale": d.rationale,
            }
            for d in review.data.decisions
        ]

    @staticmethod
    def _serialize_decision(d: Decision) -> dict:
        return {
            "id": d.decision_id,
            "business_id": d.business_id,
            "title": d.title,
            "context": d.context,
            "rationale": d.rationale,
            "status": d.status,
            "decision_date": d.decision_date,
            "owner": d.owner,
            "recommendation_id": d.recommendation_id,
            "review_id": d.review_id,
            "brief_id": d.brief_id,
            "created_at": d.created_at,
            "updated_at": d.updated_at,
        }

    def list_jobs(self, workspace_id: str) -> list[dict]:
        """Return all Jobs for a workspace, ordered by ID."""
        self.validate_workspace(workspace_id)
        if not self.job_store:
            return []
        return [
            self._serialize_job(j, include_output=False)
            for j in self.job_store.list(workspace_id)
        ]

    def get_job(self, workspace_id: str, job_id: str) -> dict | None:
        """Return a single Job with full output, or None if not found."""
        self.validate_workspace(workspace_id)
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

    # -- CEO Review & Dashboard ------------------------------------------------

    def _run_ceo_review(self, workspace_id: str) -> "CEOLoop.CEOReviewResult":  # noqa: F821
        """Run the CEO Loop for a workspace.  Returns a CEOReviewResult."""
        business_dir = self.context_engine.workspace_engine.resolve_business_dir(
            workspace_id
        )
        loop = CEOLoop()
        return loop.run(business_dir)

    def get_brief(self, workspace_id: str) -> dict:
        """Return the full Executive Brief from a CEO Review."""
        self.validate_workspace(workspace_id)
        review = self._run_ceo_review(workspace_id)
        return self._serialize_review(review)

    def get_dashboard(self, workspace_id: str) -> dict:
        """Return the Workspace Home operating summary (amendment 1)."""
        self.validate_workspace(workspace_id)

        ws_context = self.context_engine.workspace_engine.resolve(workspace_id)
        ws = ws_context.workspace
        workspace_info = {
            "name": ws.name or workspace_id,
            "mission": ws.mission or "",
        }
        repositories = len(ws_context.repositories)
        knowledge_docs = self.list_knowledge(workspace_id)

        # Live operation stats
        ops_active = 0
        ops_completed_today = 0
        ops_total = 0
        if self.operation_store:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ops = self.operation_store.list(workspace_id)
            ops_total = len(ops)
            for op in ops:
                if op.status in ("created", "executing"):
                    ops_active += 1
                if op.status == "completed" and op.updated_at.strftime("%Y-%m-%d") == today:
                    ops_completed_today += 1

        # CEO Review — generated once, reused for dashboard (amendment 2)
        review = self._run_ceo_review(workspace_id)
        brief = review.brief

        # KPI summary from business data
        kpis = review.data.kpis
        kpi_summary = {
            "total": len(kpis),
            "on_track": sum(1 for k in kpis if k.status == "on_track"),
            "at_risk": sum(1 for k in kpis if k.status == "at_risk"),
            "off_track": sum(1 for k in kpis if k.status == "off_track"),
        }

        # Top 3 priorities — "Today's Focus" (amendment 4)
        todays_focus = brief.priorities[:3]

        return {
            "workspace": workspace_info,
            "operations": {
                "active": ops_active,
                "completed_today": ops_completed_today,
                "total": ops_total,
            },
            "knowledge": {
                "count": len(knowledge_docs),
            },
            "repositories": {
                "count": repositories,
            },
            "kpis": kpi_summary,
            "todays_focus": todays_focus,
            "risks": brief.risks,
            "execution_status": review.execution_status,
            "warnings": review.all_warnings,
        }

    @staticmethod
    def _serialize_review(review) -> dict:
        """Serialize a CEOReviewResult for the API."""
        brief = review.brief
        data = review.data

        # KPI detail
        kpis = [
            {
                "id": k.kpi_id,
                "name": k.name,
                "goal_id": k.goal_id,
                "unit": k.unit,
                "current": k.current_value,
                "target": k.target_value,
                "frequency": k.frequency,
                "status": k.status,
            }
            for k in data.kpis
        ]

        # Goals
        goals = [
            {
                "id": g.goal_id,
                "title": g.title,
                "description": g.description,
                "target": g.target_value,
                "target_date": g.target_date,
                "owner": g.owner,
                "status": g.status,
            }
            for g in data.goals
        ]

        # Bottlenecks
        bottlenecks = [
            {
                "id": b.bottleneck_id,
                "title": b.title,
                "category": b.category,
                "impact": b.impact,
                "status": b.status,
            }
            for b in data.bottlenecks
        ]

        # Decisions (for Business Journal)
        decisions = [
            {
                "id": d.decision_id,
                "title": d.title,
                "date": d.decision_date,
                "status": d.status,
            }
            for d in data.decisions
        ]

        # Experiments (for Business Journal)
        experiments = [
            {
                "id": e.experiment_id,
                "title": e.title,
                "status": e.status,
                "start_date": e.start_date,
                "outcome": e.outcome,
            }
            for e in data.experiments
        ]

        # Recommendations with scores
        recommendations = []
        for rec in review.engine_result.recommendations:
            recommendations.append({
                "id": rec.recommendation_id,
                "title": rec.title,
                "context": rec.context,
                "rationale": rec.rationale,
                "source_type": rec.source_type,
                "source_id": rec.source_id,
                "priority_score": round(rec.priority_score, 2),
                "confidence": rec.confidence,
                "suggested_action": rec.suggested_action,
            })

        return {
            "brief": {
                "id": brief.brief_id,
                "business_id": brief.business_id,
                "reporting_period": brief.reporting_period,
                "generated_at": brief.generated_at,
                "summary": brief.summary,
                "priorities": brief.priorities,
                "recommendations_text": brief.recommendations,
                "risks": brief.risks,
                "status": brief.status,
            },
            "recommendations": recommendations,
            "kpis": kpis,
            "goals": goals,
            "bottlenecks": bottlenecks,
            "decisions": decisions,
            "experiments": experiments,
            "execution_status": review.execution_status,
            "warnings": review.all_warnings,
        }

    def list_knowledge(self, workspace_id: str) -> list[dict]:
        """Return metadata for all Knowledge Documents in a workspace."""
        self.validate_workspace(workspace_id)
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
        self.validate_workspace(workspace_id)
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
