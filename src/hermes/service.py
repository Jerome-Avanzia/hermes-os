from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone

from hermes import config
from hermes.conductor import Conductor
from hermes.context.context_graph import ContextGraph, GraphData, SUPPORTED_TYPES
from hermes.kernel.ceo_loop import CEOLoop
from hermes.kernel.decision_id import generate_decision_id
from hermes.kernel.decision_writer import DecisionWriter
from hermes.kernel.department_registry import DepartmentRegistry
from hermes.kernel.acknowledgement_store import AcknowledgementStore
from hermes.kernel.heartbeat_runtime import (
    InvalidHeartbeatStatusError,
    append_heartbeat,
    find_stale_operations,
    get_latest,
    list_heartbeats,
)
from hermes.kernel.heartbeat_store import HeartbeatStore
from hermes.kernel.goal_registry import GoalRegistry
from hermes.kernel.people_registry import PeopleRegistry
from hermes.kernel.notification_runtime import (
    apply_acknowledgements,
    filter_unread,
    generate_notifications,
)
from hermes.kernel.operation_id_bk import generate_bk_operation_id
from hermes.kernel.operation_runtime import (
    StepNotActionableError,
    StepNotFoundError,
    complete_step as runtime_complete_step,
    get_progress,
)
from hermes.kernel.operation_writer import OperationWriter
from hermes.kernel.sop_registry import SOPRegistry
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
from hermes.runtime.docker_provider import DockerProvider
from hermes.runtime.github_provider import GitHubProvider
from hermes.runtime.github_runtime import GitHubRuntime
from hermes.runtime.host_provider import HostProvider
from hermes.runtime.infrastructure_runtime import InfrastructureRuntime
from hermes.runtime.n8n_provider import N8nProvider
from hermes.runtime.n8n_runtime import N8nRuntime
from hermes.runtime.nocodb_provider import NocodbProvider
from hermes.runtime.nocodb_runtime import NocodbRuntime
from hermes.runtime.llm_runtime import LlmRuntime
from hermes.runtime.ollama_provider import OllamaProvider
from hermes.runtime.openai_provider import OpenAIProvider
from hermes.runtime.anthropic_provider import AnthropicProvider
from hermes.runtime.openrouter_provider import OpenRouterProvider
from hermes.runtime.gemini_provider import GeminiProvider
from hermes.runtime.traefik_provider import TraefikProvider

logger = logging.getLogger(__name__)

_MAX_KNOWLEDGE_DOCS_IN_PROMPT = 3

_SENTINEL = object()  # distinguishes "no review passed" from "review is None"

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
        sop_registry: SOPRegistry | None = None,
        department_registry: DepartmentRegistry | None = None,
        heartbeat_store: HeartbeatStore | None = None,
        acknowledgement_store: AcknowledgementStore | None = None,
        people_registry: PeopleRegistry | None = None,
        goal_registry: GoalRegistry | None = None,
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
        self.sop_registry = sop_registry or SOPRegistry()
        self.department_registry = department_registry or DepartmentRegistry()
        self.heartbeat_store = heartbeat_store
        self.acknowledgement_store = acknowledgement_store
        self.people_registry = people_registry or PeopleRegistry()
        self.goal_registry = goal_registry or GoalRegistry()
        self._context_graph: ContextGraph | None = None
        self._github_runtime: GitHubRuntime | None = None
        self._infrastructure_runtime: InfrastructureRuntime | None = None
        self._n8n_runtime: N8nRuntime | None = None
        self._nocodb_runtime: NocodbRuntime | None = None
        self._llm_runtime: LlmRuntime | None = None

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

    # -- Operation Completion (Sprint 30) ----------------------------------------

    def complete_operation(
        self,
        workspace_id: str,
        operation_id: str,
        outcome: str,
        outcome_classification: str = "success",
    ) -> dict:
        """Complete an executing Operation and write it to Operations.md.

        Transitions the workspace Operation to 'completed', then appends
        a historical record to the business's Operations.md file.
        """
        self.validate_workspace(workspace_id)
        op = self.operation_store.load(workspace_id, operation_id)
        transition_operation(op, "completed")
        op.outcome = outcome
        op.outcome_classification = outcome_classification
        self.operation_store.save(op)

        # Write to Business Knowledge (Operations.md)
        business_dir = self.context_engine.workspace_engine.resolve_business_dir(
            workspace_id
        )
        bk_id = generate_bk_operation_id(business_dir)
        writer = OperationWriter()
        writer.append_operation(business_dir, op, bk_id)

        return {
            "operation": self._serialize_operation(op),
            "bk_operation_id": bk_id,
        }

    def fail_operation(
        self,
        workspace_id: str,
        operation_id: str,
        outcome: str,
        outcome_classification: str = "failure",
    ) -> dict:
        """Fail an executing Operation and write it to Operations.md.

        Transitions the workspace Operation to 'failed', then appends
        a historical record to the business's Operations.md file.
        """
        self.validate_workspace(workspace_id)
        op = self.operation_store.load(workspace_id, operation_id)
        transition_operation(op, "failed")
        op.outcome = outcome
        op.outcome_classification = outcome_classification
        self.operation_store.save(op)

        # Write to Business Knowledge (Operations.md)
        business_dir = self.context_engine.workspace_engine.resolve_business_dir(
            workspace_id
        )
        bk_id = generate_bk_operation_id(business_dir)
        writer = OperationWriter()
        writer.append_operation(business_dir, op, bk_id)

        return {
            "operation": self._serialize_operation(op),
            "bk_operation_id": bk_id,
        }

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
            now_op = datetime.now(timezone.utc)
            op_id = generate_operation_id(
                self.operation_store.operations_dir(workspace_id)
            )
            operation = Operation(
                id=op_id,
                workspace_id=workspace_id,
                request=f"[{decision_id}] {rec.title}",
                status="created",
                created_at=now_op,
                updated_at=now_op,
                decision_id=decision_id,
                recommendation_id=rec.recommendation_id,
                review_id=review.review_id,
            )
            self.operation_store.save(operation)
            result["operation"] = self._serialize_operation(operation)
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

    # -- Operation SOP Runtime (Sprint 34) ---------------------------------------

    def get_operation_progress(
        self, workspace_id: str, operation_id: str,
    ) -> dict | None:
        """Return SOP step progress for an Operation, or None if no SOP linked."""
        self.validate_workspace(workspace_id)
        if not self.operation_store:
            return None
        op = self.operation_store.load(workspace_id, operation_id)
        if not op.sop_id:
            return None
        sop = self.sop_registry.get(op.sop_id)
        if sop is None:
            return None
        progress = get_progress(op, sop)
        return self._serialize_progress(progress)

    def complete_operation_step(
        self, workspace_id: str, operation_id: str, step_id: str,
    ) -> dict:
        """Mark the next SOP step complete. Sequential enforcement applies.

        Raises OperationNotFoundError, StepNotFoundError, StepNotActionableError.
        """
        self.validate_workspace(workspace_id)
        if not self.operation_store:
            raise RuntimeError("OperationStore is required")
        op = self.operation_store.load(workspace_id, operation_id)
        if not op.sop_id:
            raise StepNotFoundError("Operation has no SOP linked")
        sop = self.sop_registry.get(op.sop_id)
        if sop is None:
            raise StepNotFoundError(f"SOP not found: {op.sop_id}")
        progress = runtime_complete_step(op, sop, step_id)
        self.operation_store.save(op)
        return self._serialize_progress(progress)

    def link_sop_to_operation(
        self, workspace_id: str, operation_id: str, sop_id: str,
    ) -> dict:
        """Link an SOP to an Operation. Returns the updated Operation."""
        self.validate_workspace(workspace_id)
        if not self.operation_store:
            raise RuntimeError("OperationStore is required")
        op = self.operation_store.load(workspace_id, operation_id)
        # Verify SOP exists
        sop = self.sop_registry.get(sop_id)
        if sop is None:
            raise StepNotFoundError(f"SOP not found: {sop_id}")
        op.sop_id = sop_id
        self.operation_store.save(op)
        return self._serialize_operation(op)

    @staticmethod
    def _serialize_progress(p) -> dict:
        return {
            "sop_id": p.sop_id,
            "total_steps": p.total_steps,
            "completed_steps": p.completed_steps,
            "completion_pct": p.completion_pct,
            "all_complete": p.all_complete,
            "current_step": p.current_step,
            "steps": [
                {
                    "id": s.id,
                    "index": s.index,
                    "title": s.title,
                    "description": s.description,
                    "completed": s.completed,
                    "completed_at": s.completed_at,
                }
                for s in p.steps
            ],
        }

    # -- Operation Heartbeats (Sprint 35) ----------------------------------------

    def list_operation_heartbeats(
        self, workspace_id: str, operation_id: str,
    ) -> list[dict]:
        """Return all Heartbeats for an Operation, newest first."""
        self.validate_workspace(workspace_id)
        if not self.heartbeat_store:
            return []
        # Verify operation exists
        if self.operation_store:
            self.operation_store.load(workspace_id, operation_id)
        return [
            self._serialize_heartbeat(hb)
            for hb in list_heartbeats(self.heartbeat_store, workspace_id, operation_id)
        ]

    def create_heartbeat(
        self,
        workspace_id: str,
        operation_id: str,
        status: str,
        summary: str,
        author: str = "",
        details: str = "",
        blocker: str = "",
        next_action: str = "",
    ) -> dict:
        """Append an immutable Heartbeat to an Operation."""
        self.validate_workspace(workspace_id)
        if not self.heartbeat_store:
            raise RuntimeError("HeartbeatStore is required")
        # Verify operation exists
        if self.operation_store:
            self.operation_store.load(workspace_id, operation_id)
        hb = append_heartbeat(
            self.heartbeat_store,
            operation_id=operation_id,
            workspace_id=workspace_id,
            status=status,
            summary=summary,
            author=author,
            details=details,
            blocker=blocker,
            next_action=next_action,
        )
        return self._serialize_heartbeat(hb)

    def list_stale_operations(
        self, workspace_id: str, threshold_hours: float | None = None,
    ) -> list[dict]:
        """Return active Operations whose latest heartbeat exceeds the freshness threshold."""
        self.validate_workspace(workspace_id)
        if not self.operation_store or not self.heartbeat_store:
            return []
        if threshold_hours is None:
            threshold_hours = config.stale_threshold_hours()
        ops = self.operation_store.list(workspace_id)
        stale = find_stale_operations(
            ops, self.heartbeat_store, workspace_id, threshold_hours,
        )
        return [self._serialize_stale_operation(s) for s in stale]

    @staticmethod
    def _serialize_heartbeat(hb) -> dict:
        return {
            "id": hb.id,
            "operation_id": hb.operation_id,
            "workspace_id": hb.workspace_id,
            "timestamp": hb.timestamp.isoformat(),
            "status": hb.status,
            "summary": hb.summary,
            "author": hb.author,
            "details": hb.details,
            "blocker": hb.blocker,
            "next_action": hb.next_action,
        }

    @staticmethod
    def _serialize_stale_operation(s) -> dict:
        data = {
            "operation_id": s.operation_id,
            "workspace_id": s.workspace_id,
            "request": s.request,
            "status": s.status,
            "elapsed_hours": s.elapsed_hours,
        }
        if s.latest_heartbeat is not None:
            data["latest_heartbeat"] = {
                "id": s.latest_heartbeat.id,
                "timestamp": s.latest_heartbeat.timestamp.isoformat(),
                "status": s.latest_heartbeat.status,
                "summary": s.latest_heartbeat.summary,
            }
        else:
            data["latest_heartbeat"] = None
        return data

    # -- Executive Notifications (Sprint 36) -------------------------------------

    def _generate_notifications(
        self, workspace_id: str,
        ops: list | None = None,
        review: object | None = _SENTINEL,
    ) -> list:
        """Compute notifications from current state and apply acknowledgements.

        When *ops* and *review* are supplied the caller's already-loaded
        data is reused — no duplicate CEO Loop execution.
        """
        if ops is None:
            ops = self.operation_store.list(workspace_id) if self.operation_store else []
        if review is _SENTINEL:
            try:
                review = self._run_ceo_review(workspace_id)
            except Exception:
                review = None  # notifications still work from operations

        from hermes.kernel.notification_runtime import generate_notifications as gen
        threshold = config.stale_threshold_hours()
        notifications = gen(
            ops, self.heartbeat_store, review, workspace_id, threshold,
        )

        if self.acknowledgement_store:
            acked = self.acknowledgement_store.load(workspace_id)
            apply_acknowledgements(notifications, acked)

        return notifications

    def list_notifications(self, workspace_id: str) -> dict:
        """Return all current notifications with unread count."""
        self.validate_workspace(workspace_id)
        notifications = self._generate_notifications(workspace_id)
        unread = sum(1 for n in notifications if not n.acknowledged)
        return {
            "notifications": [self._serialize_notification(n) for n in notifications],
            "unread_count": unread,
        }

    def list_unread_notifications(self, workspace_id: str) -> dict:
        """Return only unacknowledged notifications."""
        self.validate_workspace(workspace_id)
        notifications = self._generate_notifications(workspace_id)
        unread = filter_unread(notifications)
        return {
            "notifications": [self._serialize_notification(n) for n in unread],
            "unread_count": len(unread),
        }

    def acknowledge_notification(
        self, workspace_id: str, notification_id: str,
    ) -> dict:
        """Acknowledge a notification. Idempotent."""
        self.validate_workspace(workspace_id)
        if not self.acknowledgement_store:
            raise RuntimeError("AcknowledgementStore is required")
        self.acknowledgement_store.acknowledge(workspace_id, notification_id)
        return {"acknowledged": True}

    @staticmethod
    def _serialize_notification(n) -> dict:
        return {
            "id": n.id,
            "timestamp": n.timestamp.isoformat(),
            "severity": n.severity,
            "category": n.category,
            "title": n.title,
            "summary": n.summary,
            "related_object_type": n.related_object_type,
            "related_object_id": n.related_object_id,
            "acknowledged": n.acknowledged,
        }

    # -- Capability Runtime (Sprint 31) -----------------------------------------

    def list_capabilities(self, workspace_id: str) -> list[dict]:
        """Return all organizational capabilities from the registry."""
        self.validate_workspace(workspace_id)
        registry = self.context_engine.capability_engine.registry
        return [self._serialize_capability_summary(c) for c in registry.list()]

    def get_capability(self, workspace_id: str, capability_id: str) -> dict | None:
        """Return full detail for a single capability, or None."""
        self.validate_workspace(workspace_id)
        registry = self.context_engine.capability_engine.registry
        cap = registry.get(capability_id)
        if cap is None:
            return None
        return self._serialize_capability(cap)

    @staticmethod
    def _serialize_capability_summary(c) -> dict:
        return {
            "id": c.id,
            "name": c.name,
            "version": c.version,
            "description": c.description,
            "status": c.status,
            "provides": c.provides,
            "department_id": c.department_id,
        }

    @staticmethod
    def _serialize_capability(c) -> dict:
        return {
            "id": c.id,
            "name": c.name,
            "version": c.version,
            "description": c.description,
            "status": c.status,
            "provides": c.provides,
            "inputs": c.inputs,
            "outputs": c.outputs,
            "keywords": c.keywords,
            "sop_ref": c.sop_ref,
            "sop_refs": c.sop_refs,
            "skill_id": c.skill_id,
            "owner": c.owner,
            "department_id": c.department_id,
            "depends_on": c.depends_on,
        }

    # -- SOP Runtime (Sprint 32) ------------------------------------------------

    def list_sops(self, workspace_id: str) -> list[dict]:
        """Return all SOPs from the registry (summaries, no content)."""
        self.validate_workspace(workspace_id)
        return [self._serialize_sop_summary(s) for s in self.sop_registry.list()]

    def get_sop(self, workspace_id: str, sop_id: str) -> dict | None:
        """Return full SOP detail with markdown content, or None."""
        self.validate_workspace(workspace_id)
        sop = self.sop_registry.get(sop_id)
        if sop is None:
            return None
        return self._serialize_sop(sop)

    @staticmethod
    def _serialize_sop_summary(s) -> dict:
        return {
            "id": s.id,
            "title": s.title,
            "skill_id": s.skill_id,
            "description": s.description,
            "status": s.status,
            "owner": s.owner,
            "category": s.category,
        }

    @staticmethod
    def _serialize_sop(s) -> dict:
        return {
            "id": s.id,
            "title": s.title,
            "skill_id": s.skill_id,
            "filename": s.filename,
            "content": s.content,
            "description": s.description,
            "version": s.version,
            "status": s.status,
            "owner": s.owner,
            "category": s.category,
        }

    # -- Department Runtime (Sprint 33) -----------------------------------------

    def list_departments(self, workspace_id: str) -> list[dict]:
        """Return all departments with aggregate metrics."""
        self.validate_workspace(workspace_id)
        cap_registry = self.context_engine.capability_engine.registry
        all_caps = cap_registry.list()
        departments = self.department_registry.list()
        result = []
        for dept in departments:
            caps = [c for c in all_caps if c.department_id == dept.id]
            active_caps = [c for c in caps if c.status == "active"]
            sop_count = sum(len(c.sop_refs) for c in caps)
            d = self._serialize_department_summary(dept)
            d["capability_count"] = len(caps)
            d["active_capability_count"] = len(active_caps)
            d["sop_count"] = sop_count
            result.append(d)
        return result

    def get_department(self, workspace_id: str, department_id: str) -> dict | None:
        """Return department detail with capabilities and SOP count."""
        self.validate_workspace(workspace_id)
        dept = self.department_registry.get(department_id)
        if dept is None:
            return None
        cap_registry = self.context_engine.capability_engine.registry
        caps = [c for c in cap_registry.list() if c.department_id == dept.id]
        active_caps = [c for c in caps if c.status == "active"]
        sop_count = sum(len(c.sop_refs) for c in caps)
        d = self._serialize_department(dept)
        d["capabilities"] = [
            {"id": c.id, "name": c.name, "status": c.status}
            for c in caps
        ]
        d["capability_count"] = len(caps)
        d["active_capability_count"] = len(active_caps)
        d["sop_count"] = sop_count
        return d

    @staticmethod
    def _serialize_department_summary(dept) -> dict:
        return {
            "id": dept.id,
            "name": dept.name,
            "description": dept.description,
            "owner": dept.owner,
            "status": dept.status,
        }

    @staticmethod
    def _serialize_department(dept) -> dict:
        return {
            "id": dept.id,
            "name": dept.name,
            "description": dept.description,
            "mission": dept.mission,
            "owner": dept.owner,
            "status": dept.status,
            "tags": dept.tags,
        }

    # -- People (Sprint 38) ----------------------------------------------------

    def _owner_matches(self, owner_str: str | None, person) -> bool:
        """Check if an owner string matches a person by ID or owner_aliases."""
        if not owner_str:
            return False
        lower = owner_str.strip().lower()
        if lower == person.id.lower():
            return True
        return any(alias.lower() == lower for alias in person.owner_aliases)

    def _compute_workload(self, person, workspace_id: str) -> dict:
        """Compute ownership counts for a person across all domains."""
        departments = self.department_registry.list()
        caps = self.context_engine.capability_engine.registry.list()
        ops = self.operation_store.list(workspace_id) if self.operation_store else []

        owned_depts = [d for d in departments if self._owner_matches(d.owner, person)]
        owned_caps = [c for c in caps if self._owner_matches(c.owner, person)]
        active_ops = [
            o for o in ops
            if self._owner_matches(o.extra_fields.get("owner"), person)
            and o.status in ("created", "executing")
        ]
        blocked_ops = []
        if self.heartbeat_store:
            for op in active_ops:
                latest = get_latest(self.heartbeat_store, workspace_id, op.id)
                if latest and latest.status == "blocked":
                    blocked_ops.append(op)

        return {
            "owned_departments": len(owned_depts),
            "owned_capabilities": len(owned_caps),
            "active_operations": len(active_ops),
            "blocked_operations": len(blocked_ops),
            "_depts": owned_depts,
            "_caps": owned_caps,
            "_ops": active_ops,
        }

    def list_people(self, workspace_id: str) -> list[dict]:
        """Return all people with computed workload metrics."""
        self.validate_workspace(workspace_id)
        people = self.people_registry.list()
        result = []
        for person in people:
            workload = self._compute_workload(person, workspace_id)
            d = self._serialize_person_summary(person)
            d["owned_departments"] = workload["owned_departments"]
            d["owned_capabilities"] = workload["owned_capabilities"]
            d["active_operations"] = workload["active_operations"]
            d["blocked_operations"] = workload["blocked_operations"]
            result.append(d)
        return result

    def get_person(self, workspace_id: str, person_id: str) -> dict | None:
        """Return full person detail with ownership breakdown."""
        self.validate_workspace(workspace_id)
        person = self.people_registry.get(person_id)
        if person is None:
            return None
        workload = self._compute_workload(person, workspace_id)
        d = self._serialize_person(person)
        d["departments"] = [
            {"id": dept.id, "name": dept.name}
            for dept in workload["_depts"]
        ]
        d["capabilities"] = [
            {"id": c.id, "name": c.name}
            for c in workload["_caps"]
        ]
        d["operations"] = [
            {"id": o.id, "request": o.request, "status": o.status}
            for o in workload["_ops"]
        ]
        d["workload"] = {
            "owned_departments": workload["owned_departments"],
            "owned_capabilities": workload["owned_capabilities"],
            "active_operations": workload["active_operations"],
            "blocked_operations": workload["blocked_operations"],
        }
        return d

    @staticmethod
    def _serialize_person_summary(person) -> dict:
        return {
            "id": person.id,
            "name": person.name,
            "title": person.title,
            "department_ids": person.department_ids,
            "status": person.status,
        }

    @staticmethod
    def _serialize_person(person) -> dict:
        return {
            "id": person.id,
            "name": person.name,
            "title": person.title,
            "department_ids": person.department_ids,
            "email": person.email,
            "status": person.status,
            "responsibilities": person.responsibilities,
            "owner_aliases": person.owner_aliases,
        }

    # -- Goals (Sprint 39) ----------------------------------------------------

    def list_goals(self, workspace_id: str) -> list[dict]:
        """Return all goals with summary cross-reference counts."""
        self.validate_workspace(workspace_id)
        goals = self.goal_registry.list()
        review = self._run_ceo_review(workspace_id)
        data = review.data

        result = []
        for goal in goals:
            linked_kpis = [k for k in data.kpis if k.goal_id == goal.goal_id]
            linked_decisions = [d for d in data.decisions if d.goal_id == goal.goal_id]
            d = self._serialize_goal_summary(goal)
            d["kpi_count"] = len(linked_kpis)
            d["decision_count"] = len(linked_decisions)
            result.append(d)
        return result

    def get_goal(self, workspace_id: str, goal_id: str) -> dict | None:
        """Return full goal detail with cross-references and traceability."""
        self.validate_workspace(workspace_id)
        goal = self.goal_registry.get(goal_id)
        if goal is None:
            return None

        review = self._run_ceo_review(workspace_id)
        data = review.data

        # Cross-reference: KPIs linked to this goal
        linked_kpis = [k for k in data.kpis if k.goal_id == goal.goal_id]

        # Cross-reference: Decisions linked to this goal
        linked_decisions = [d for d in data.decisions if d.goal_id == goal.goal_id]
        decision_ids = {d.decision_id for d in linked_decisions}

        # Cross-reference: Operations via Decision chain (Amendment 5)
        ops = self.operation_store.list(workspace_id) if self.operation_store else []
        linked_ops = [
            o for o in ops
            if o.decision_id and o.decision_id in decision_ids
        ]

        # Cross-reference: Capabilities via owner match (Amendment 4)
        cap_registry = self.context_engine.capability_engine.registry
        all_caps = cap_registry.list()
        linked_caps = [
            c for c in all_caps
            if self._owner_matches(c.owner, type("_P", (), {"id": goal.owner, "owner_aliases": []})())
            or any(
                c.owner and c.owner.strip().lower() == goal.owner.strip().lower()
                for _ in [None]
            )
        ]
        # Simpler: match capabilities whose owner matches the goal owner string
        linked_caps = [
            c for c in all_caps
            if c.owner and c.owner.strip().lower() == goal.owner.strip().lower()
        ]

        # Traceability validation (Amendment 5):
        # Every active operation associated with this goal must trace through
        # Goal -> Decision -> Operation chain.
        traceability_warnings = []
        bk_ops = [o for o in data.operations if o.status in ("created", "executing")]
        for bk_op in bk_ops:
            op_owner = bk_op.extra_fields.get("owner", "")
            if op_owner and op_owner.strip().lower() == goal.owner.strip().lower():
                # Check if this BK operation has a decision_id linking to this goal
                if not bk_op.decision_id or bk_op.decision_id not in decision_ids:
                    traceability_warnings.append(
                        f"Operation {bk_op.id} appears related to goal {goal.goal_id} "
                        f"by owner but lacks Goal->Decision->Operation chain"
                    )

        d = self._serialize_goal(goal)
        d["kpis"] = [
            {
                "id": k.kpi_id,
                "name": k.name,
                "current": k.current_value,
                "target": k.target_value,
                "unit": k.unit,
                "status": k.status,
            }
            for k in linked_kpis
        ]
        d["decisions"] = [
            {
                "id": dec.decision_id,
                "title": dec.title,
                "status": dec.status,
                "date": dec.decision_date,
            }
            for dec in linked_decisions
        ]
        d["operations"] = [
            {
                "id": o.id,
                "request": o.request,
                "status": o.status,
            }
            for o in linked_ops
        ]
        d["capabilities"] = [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
            }
            for c in linked_caps
        ]
        d["traceability_warnings"] = traceability_warnings
        return d

    @staticmethod
    def _serialize_goal_summary(goal) -> dict:
        return {
            "id": goal.goal_id,
            "title": goal.title,
            "owner": goal.owner,
            "status": goal.status,
            "target_date": goal.target_date,
        }

    @staticmethod
    def _serialize_goal(goal) -> dict:
        return {
            "id": goal.goal_id,
            "business_id": goal.business_id,
            "title": goal.title,
            "description": goal.description,
            "target_value": goal.target_value,
            "target_date": goal.target_date,
            "owner": goal.owner,
            "status": goal.status,
            "strategy_id": goal.strategy_id,
        }

    # -- GitHub Runtime (Sprint 41) -----------------------------------------------

    def _get_github_runtime(self) -> GitHubRuntime:
        """Lazily build the GitHubRuntime from config."""
        if self._github_runtime is None:
            provider = GitHubProvider(
                org=config.github_org(),
                token=config.github_token(),
            )
            self._github_runtime = GitHubRuntime(provider)
        return self._github_runtime

    def github_health(self) -> dict:
        """Return GitHub integration health status (Amendment 1, 6)."""
        runtime = self._get_github_runtime()
        h = runtime.health()
        result: dict = {
            "configured": h.configured,
            "authenticated": h.authenticated,
            "reachable": h.reachable,
            "org": h.org,
        }
        if h.rate_limit:
            result["rate_limit"] = {
                "limit": h.rate_limit.limit,
                "remaining": h.rate_limit.remaining,
                "reset": h.rate_limit.reset,
            }
        return result

    def list_repositories(self) -> list[dict]:
        """Return all org repositories as serialized dicts."""
        runtime = self._get_github_runtime()
        repos = runtime.list_repositories()
        return [self._serialize_repo(r) for r in repos]

    def get_repository(self, repo_id: str) -> dict | None:
        """Return a single repository by slug, or None."""
        runtime = self._get_github_runtime()
        repo = runtime.get_repository(repo_id)
        if repo is None:
            return None
        return self._serialize_repo(repo)

    @staticmethod
    def _serialize_repo(r) -> dict:
        return {
            "id": r.id,
            "name": r.name,
            "provider": r.provider,
            "url": r.url,
            "description": r.description,
            "default_branch": r.default_branch,
            "language": r.language,
            "visibility": r.visibility,
            "archived": r.archived,
            "open_issues_count": r.open_issues_count,
            "open_prs_count": r.open_prs_count,
            "last_push_at": r.last_push_at,
            "topics": r.topics,
        }

    # -- Infrastructure Runtime (Sprint 42) --------------------------------------

    def _get_infrastructure_runtime(self) -> InfrastructureRuntime:
        """Lazily build the InfrastructureRuntime from config."""
        if self._infrastructure_runtime is None:
            providers = [
                DockerProvider(host=config.docker_host()),
                TraefikProvider(api_url=config.traefik_url()),
                HostProvider(),
            ]
            self._infrastructure_runtime = InfrastructureRuntime(providers)
        return self._infrastructure_runtime

    def infrastructure_health(self) -> dict:
        """Return aggregated infrastructure health status."""
        runtime = self._get_infrastructure_runtime()
        h = runtime.health()
        return {
            "configured": h.configured,
            "last_refresh": h.last_refresh,
            "providers": [
                {
                    "name": p.provider_name,
                    "configured": p.configured,
                    "reachable": p.reachable,
                    "detail": p.detail,
                }
                for p in h.providers
            ],
        }

    def list_services(self) -> list[dict]:
        """Return all infrastructure services as serialized dicts."""
        runtime = self._get_infrastructure_runtime()
        services = runtime.list_services()
        return [self._serialize_service(s) for s in services]

    def get_service(self, service_id: str) -> dict | None:
        """Return a single service by ID, or None."""
        runtime = self._get_infrastructure_runtime()
        svc = runtime.get_service(service_id)
        if svc is None:
            return None
        return self._serialize_service(svc)

    @staticmethod
    def _serialize_service(s) -> dict:
        return {
            "id": s.id,
            "name": s.name,
            "type": s.type,
            "status": s.status,
            "health": s.health,
            "image": s.image,
            "image_tag": s.image_tag,
            "repository_ref": s.repository_ref,
            "container_name": s.container_name,
            "uptime": s.uptime,
            "started_at": s.started_at,
            "cpu_percent": s.cpu_percent,
            "memory_usage": s.memory_usage,
            "memory_limit": s.memory_limit,
            "resource_state": s.resource_state,
            "ports": s.ports,
            "labels": s.labels,
            "urls": s.urls,
        }

    # -- n8n Runtime (Sprint 43) -------------------------------------------------

    def _get_n8n_runtime(self) -> N8nRuntime:
        """Lazily build the N8nRuntime from config."""
        if self._n8n_runtime is None:
            provider = N8nProvider(
                api_url=config.n8n_url(),
                api_key=config.n8n_api_key(),
            )
            self._n8n_runtime = N8nRuntime(provider)
        return self._n8n_runtime

    def n8n_health(self) -> dict:
        """Return n8n integration health status."""
        runtime = self._get_n8n_runtime()
        h = runtime.health()
        return {
            "configured": h.configured,
            "authenticated": h.authenticated,
            "reachable": h.reachable,
            "api_version": h.api_version,
            "workflow_count": h.workflow_count,
            "active_count": h.active_count,
            "failed_recently": h.failed_recently,
            "last_sync": h.last_sync,
            "refresh_duration_ms": h.refresh_duration_ms,
        }

    def list_workflows(self) -> list[dict]:
        """Return all n8n workflows as serialized dicts."""
        runtime = self._get_n8n_runtime()
        workflows = runtime.list_workflows()
        return [self._serialize_workflow(w) for w in workflows]

    def get_workflow(self, workflow_id: str) -> dict | None:
        """Return a single workflow by Hermes ID, or None."""
        runtime = self._get_n8n_runtime()
        wf = runtime.get_workflow(workflow_id)
        if wf is None:
            return None
        return self._serialize_workflow(wf)

    @staticmethod
    def _serialize_workflow(w) -> dict:
        return {
            "id": w.id,
            "name": w.name,
            "provider_id": w.provider_id,
            "active": w.active,
            "tags": w.tags,
            "trigger_types": w.trigger_types,
            "node_count": w.node_count,
            "execution_count": w.execution_count,
            "last_execution": w.last_execution,
            "last_success": w.last_success,
            "last_failure": w.last_failure,
            "status": w.status,
            "attention_state": w.attention_state,
            "created_at": w.created_at,
            "updated_at": w.updated_at,
        }

    # -- NocoDB Runtime (Sprint 44) -----------------------------------------------

    def _get_nocodb_runtime(self) -> NocodbRuntime:
        """Lazily build the NocodbRuntime from config."""
        if self._nocodb_runtime is None:
            provider = NocodbProvider(
                api_url=config.nocodb_url(),
                api_token=config.nocodb_token(),
            )
            self._nocodb_runtime = NocodbRuntime(provider)
        return self._nocodb_runtime

    def nocodb_health(self) -> dict:
        """Return NocoDB integration health status."""
        runtime = self._get_nocodb_runtime()
        h = runtime.health()
        return {
            "configured": h.configured,
            "authenticated": h.authenticated,
            "reachable": h.reachable,
            "database_count": h.database_count,
            "table_count": h.table_count,
            "record_count": h.record_count,
            "last_sync": h.last_sync,
            "refresh_duration_ms": h.refresh_duration_ms,
        }

    def list_databases(self) -> list[dict]:
        """Return all databases as serialized dicts."""
        runtime = self._get_nocodb_runtime()
        databases = runtime.list_databases()
        return [self._serialize_database(db) for db in databases]

    def get_database(self, database_id: str) -> dict | None:
        """Return a single database by Hermes ID with tables, or None."""
        runtime = self._get_nocodb_runtime()
        db = runtime.get_database(database_id)
        if db is None:
            return None
        result = self._serialize_database(db)
        # Include tables when fetching a single database
        tables = runtime.list_tables_for_database(db)
        result["tables"] = [self._serialize_table(t) for t in tables]
        return result

    def list_tables(self) -> list[dict]:
        """Return all tables across all databases as serialized dicts."""
        runtime = self._get_nocodb_runtime()
        tables = runtime.list_tables()
        return [self._serialize_table(t) for t in tables]

    def get_table(self, table_id: str) -> dict | None:
        """Return a single table by Hermes ID, or None."""
        runtime = self._get_nocodb_runtime()
        tbl = runtime.get_table(table_id)
        if tbl is None:
            return None
        return self._serialize_table(tbl)

    @staticmethod
    def _serialize_database(db) -> dict:
        return {
            "id": db.id,
            "name": db.name,
            "provider_id": db.provider_id,
            "provider": db.provider,
            "table_count": db.table_count,
            "record_count": db.record_count,
            "health_state": db.health_state,
        }

    @staticmethod
    def _serialize_table(t) -> dict:
        return {
            "id": t.id,
            "name": t.name,
            "provider_id": t.provider_id,
            "database_id": t.database_id,
            "record_count": t.record_count,
            "column_count": t.column_count,
            "columns": [
                {"name": c.name, "type": c.type, "nullable": c.nullable, "primary": c.primary}
                for c in t.columns
            ],
            "primary_key": t.primary_key,
            "last_updated": t.last_updated,
            "attention_state": t.attention_state,
        }

    # -- LLM Runtime (Sprint 45) --------------------------------------------------

    def _get_llm_runtime(self) -> LlmRuntime:
        """Lazily build the LlmRuntime from config."""
        if self._llm_runtime is None:
            providers = [
                OllamaProvider(api_url=config.ollama_url()),
                OpenAIProvider(api_key=config.openai_api_key()),
                AnthropicProvider(api_key=config.anthropic_api_key()),
                OpenRouterProvider(api_key=config.openrouter_api_key()),
                GeminiProvider(api_key=config.gemini_api_key()),
            ]
            self._llm_runtime = LlmRuntime(
                providers=providers,
                default_provider=config.llm_default_provider(),
                default_model=config.llm_default_model(),
            )
        return self._llm_runtime

    def llm_health(self) -> dict:
        """Return LLM integration health status."""
        runtime = self._get_llm_runtime()
        h = runtime.health()
        return {
            "configured": h.configured,
            "provider_count": h.provider_count,
            "healthy_count": h.healthy_count,
            "model_count": h.model_count,
            "default_provider": h.default_provider,
            "default_model": h.default_model,
            "providers": [
                {"id": p.id, "name": p.name, "health_state": p.health_state}
                for p in h.providers
            ],
            "last_sync": h.last_sync,
            "refresh_duration_ms": h.refresh_duration_ms,
        }

    def list_llm_providers(self) -> list[dict]:
        """Return all LLM providers as serialized dicts."""
        runtime = self._get_llm_runtime()
        providers = runtime.list_providers()
        return [self._serialize_llm_provider(p) for p in providers]

    def get_llm_provider(self, provider_id: str) -> dict | None:
        """Return a single LLM provider by Hermes ID with models, or None."""
        runtime = self._get_llm_runtime()
        info = runtime.get_provider(provider_id)
        if info is None:
            return None
        result = self._serialize_llm_provider(info)
        # Find the underlying provider to get its models
        for p in runtime._providers:
            if p.name == provider_id and p.configured:
                models = runtime.list_models_for_provider(p)
                result["models"] = [self._serialize_llm_model(m) for m in models]
                break
        else:
            result["models"] = []
        return result

    def list_llm_models(self) -> list[dict]:
        """Return all LLM models across all providers as serialized dicts."""
        runtime = self._get_llm_runtime()
        models = runtime.list_models()
        return [self._serialize_llm_model(m) for m in models]

    def get_llm_model(self, model_id: str) -> dict | None:
        """Return a single LLM model by Hermes ID, or None."""
        runtime = self._get_llm_runtime()
        model = runtime.get_model(model_id)
        if model is None:
            return None
        return self._serialize_llm_model(model)

    @staticmethod
    def _serialize_llm_provider(p) -> dict:
        return {
            "id": p.id,
            "name": p.name,
            "provider_type": p.provider_type,
            "configured": p.configured,
            "authenticated": p.authenticated,
            "reachable": p.reachable,
            "default_model": p.default_model,
            "model_count": p.model_count,
            "health_state": p.health_state,
            "priority": p.priority,
        }

    @staticmethod
    def _serialize_llm_model(m) -> dict:
        return {
            "id": m.id,
            "name": m.name,
            "provider_id": m.provider_id,
            "provider": m.provider,
            "family": m.family,
            "context_window": m.context_window,
            "capabilities": {
                "streaming": m.capabilities.streaming,
                "tools": m.capabilities.tools,
                "reasoning": m.capabilities.reasoning,
                "vision": m.capabilities.vision,
            },
            "status": m.status,
            "attention_state": m.attention_state,
        }

    # -- Context Graph (Sprint 40) ---------------------------------------------

    def _get_context_graph(self) -> ContextGraph:
        """Lazily build the ContextGraph from existing registries."""
        if self._context_graph is None:
            self._context_graph = ContextGraph(
                goal_registry=self.goal_registry,
                people_registry=self.people_registry,
                department_registry=self.department_registry,
                capability_registry=self.context_engine.capability_engine.registry,
                sop_registry=self.sop_registry,
            )
        return self._context_graph

    def get_context(
        self, workspace_id: str, object_type: str, object_id: str,
    ) -> dict | None:
        """Resolve the context graph for a business object.

        Returns None if the object is not found.
        Raises ValueError for unsupported object_type.
        """
        self.validate_workspace(workspace_id)

        # Assemble per-request data
        ops = self.operation_store.list(workspace_id) if self.operation_store else []

        try:
            review = self._run_ceo_review(workspace_id)
            kpis = review.data.kpis
            decisions = review.data.decisions
        except Exception:
            kpis = []
            decisions = []
            review = None

        notifications = self._generate_notifications(
            workspace_id, ops=ops, review=review,
        )

        # Fetch repositories for context graph
        runtime = self._get_github_runtime()
        repos = runtime.list_repositories() if runtime.configured else []

        # Fetch services for context graph (Sprint 42)
        infra_runtime = self._get_infrastructure_runtime()
        services = infra_runtime.list_services() if infra_runtime.configured else []

        # Fetch workflows for context graph (Sprint 43)
        n8n_runtime = self._get_n8n_runtime()
        workflows = n8n_runtime.list_workflows() if n8n_runtime.configured else []

        # Fetch databases and tables for context graph (Sprint 44)
        nocodb_runtime = self._get_nocodb_runtime()
        databases = nocodb_runtime.list_databases() if nocodb_runtime.configured else []
        tables = nocodb_runtime.list_tables() if nocodb_runtime.configured else []

        # Fetch LLM providers and models for context graph (Sprint 45)
        llm_runtime = self._get_llm_runtime()
        llm_providers = llm_runtime.list_providers() if llm_runtime.configured else []
        llm_models = llm_runtime.list_models() if llm_runtime.configured else []

        data = GraphData(
            workspace_id=workspace_id,
            operations=ops,
            kpis=kpis,
            decisions=decisions,
            notifications=notifications,
            heartbeat_store=self.heartbeat_store,
            repositories=repos,
            services=services,
            workflows=workflows,
            databases=databases,
            tables=tables,
            llm_providers=llm_providers,
            models=llm_models,
        )

        return self._get_context_graph().resolve(object_type, object_id, data)

    # -- Impact Analysis (Sprint 46) -------------------------------------------

    def impact(
        self,
        workspace_id: str,
        object_type: str,
        object_id: str,
        max_depth: int = 3,
        direction: str = "forward",
    ) -> dict:
        """Compute impact analysis for a business object.

        Returns a serialized ImpactReport dict.
        Raises ValueError for unsupported object_type.
        """
        from hermes.context.impact_engine import ImpactEngine
        from hermes.context.risk_engine import RiskEngine

        self.validate_workspace(workspace_id)

        # Assemble per-request data (same as get_context)
        ops = self.operation_store.list(workspace_id) if self.operation_store else []

        try:
            review = self._run_ceo_review(workspace_id)
            kpis = review.data.kpis
            decisions = review.data.decisions
        except Exception:
            kpis = []
            decisions = []
            review = None

        notifications = self._generate_notifications(
            workspace_id, ops=ops, review=review,
        )

        runtime = self._get_github_runtime()
        repos = runtime.list_repositories() if runtime.configured else []

        infra_runtime = self._get_infrastructure_runtime()
        services = infra_runtime.list_services() if infra_runtime.configured else []

        n8n_runtime = self._get_n8n_runtime()
        workflows = n8n_runtime.list_workflows() if n8n_runtime.configured else []

        nocodb_runtime = self._get_nocodb_runtime()
        databases = nocodb_runtime.list_databases() if nocodb_runtime.configured else []
        tables = nocodb_runtime.list_tables() if nocodb_runtime.configured else []

        llm_runtime = self._get_llm_runtime()
        llm_providers = llm_runtime.list_providers() if llm_runtime.configured else []
        llm_models = llm_runtime.list_models() if llm_runtime.configured else []

        data = GraphData(
            workspace_id=workspace_id,
            operations=ops,
            kpis=kpis,
            decisions=decisions,
            notifications=notifications,
            heartbeat_store=self.heartbeat_store,
            repositories=repos,
            services=services,
            workflows=workflows,
            databases=databases,
            tables=tables,
            llm_providers=llm_providers,
            models=llm_models,
        )

        engine = ImpactEngine(self._get_context_graph(), RiskEngine())
        report = engine.analyze(object_type, object_id, data, max_depth, direction)
        return self._serialize_impact_report(report)

    @staticmethod
    def _serialize_impact_report(report) -> dict:
        """Convert an ImpactReport to a JSON-serializable dict."""

        def _ser_obj(o) -> dict:
            return {
                "object_type": o.object_type,
                "object_id": o.object_id,
                "name": o.name,
                "risk_level": o.risk_level,
                "risk_reasons": o.risk_reasons,
                "depth": o.depth,
                "path": o.path,
                "relationship_reason": o.relationship_reason,
            }

        affected = {}
        for otype, objs in report.affected.items():
            affected[otype] = [_ser_obj(o) for o in objs]

        coverage = {
            "types_analyzed": report.summary.coverage.types_analyzed,
            "types_available": report.summary.coverage.types_available,
            "objects_analyzed": report.summary.coverage.objects_analyzed,
            "relationships_traversed": report.summary.coverage.relationships_traversed,
            "unknown_references": report.summary.coverage.unknown_references,
            "broken_links": report.summary.coverage.broken_links,
        }

        return {
            "source": _ser_obj(report.source),
            "summary": {
                "source_type": report.summary.source_type,
                "source_id": report.summary.source_id,
                "source_name": report.summary.source_name,
                "estimated_impact": report.summary.estimated_impact,
                "affected_goals": report.summary.affected_goals,
                "affected_operations": report.summary.affected_operations,
                "affected_people": report.summary.affected_people,
                "critical_dependencies": report.summary.critical_dependencies,
                "blocking_risks": report.summary.blocking_risks,
                "recommended_checks": report.summary.recommended_checks,
                "safe_to_proceed": report.summary.safe_to_proceed,
                "coverage": coverage,
            },
            "affected": affected,
            "total_affected": report.total_affected,
            "max_depth_reached": report.max_depth_reached,
            "cycle_detected": report.cycle_detected,
            "direction": report.direction,
        }

    # -- Readiness (Sprint 47) -------------------------------------------------

    def readiness(
        self,
        workspace_id: str,
        scenario: str = "deployment",
    ) -> dict:
        """Compute executive readiness evaluation for a scenario.

        Returns a serialized ReadinessReport dict.
        Raises ValueError for unknown scenario.
        """
        from hermes.context.readiness_engine import ReadinessEngine, SCENARIOS
        from hermes.context.risk_engine import RiskEngine

        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario: {scenario}. "
                f"Valid: {', '.join(sorted(SCENARIOS))}"
            )

        self.validate_workspace(workspace_id)
        snapshot = self._build_readiness_snapshot(workspace_id)
        engine = ReadinessEngine(RiskEngine())
        report = engine.evaluate(snapshot, scenario)
        return self._serialize_readiness_report(report)

    def readiness_category(
        self,
        workspace_id: str,
        category: str,
        scenario: str = "deployment",
    ) -> dict:
        """Evaluate a single readiness category within a scenario.

        Returns a serialized CategoryResult dict.
        Raises ValueError for unknown category or scenario.
        """
        from hermes.context.readiness_engine import (
            ALL_CATEGORIES,
            ReadinessEngine,
            SCENARIOS,
        )
        from hermes.context.risk_engine import RiskEngine

        if category not in ALL_CATEGORIES:
            raise ValueError(
                f"Unknown category: {category}. "
                f"Valid: {', '.join(sorted(ALL_CATEGORIES))}"
            )
        if scenario not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario: {scenario}. "
                f"Valid: {', '.join(sorted(SCENARIOS))}"
            )

        self.validate_workspace(workspace_id)
        snapshot = self._build_readiness_snapshot(workspace_id)
        engine = ReadinessEngine(RiskEngine())
        result = engine.evaluate_category(snapshot, category, scenario)
        return self._serialize_category_result(result)

    def _build_readiness_snapshot(self, workspace_id: str):
        """Assemble ReadinessSnapshot from all runtime sources."""
        from hermes.context.readiness_engine import ReadinessSnapshot

        # Operations
        ops = self.operation_store.list(workspace_id) if self.operation_store else []
        operations = [self._serialize_operation(op) for op in ops]

        # CEO Review data (KPIs, decisions)
        try:
            review = self._run_ceo_review(workspace_id)
            kpis = [
                {"id": k.kpi_id, "name": k.name, "status": k.status,
                 "goal_id": k.goal_id}
                for k in review.data.kpis
            ]
            decisions = [
                {"id": d.decision_id, "title": getattr(d, "title", ""),
                 "status": getattr(d, "status", "")}
                for d in review.data.decisions
            ]
        except Exception:
            kpis = []
            decisions = []
            review = None

        # Notifications
        notifications = self._generate_notifications(
            workspace_id, ops=ops, review=review,
        )
        notif_dicts = [
            {"id": n.id, "title": n.title, "severity": n.severity,
             "category": n.category}
            for n in notifications
        ]

        # Runtimes
        gh_runtime = self._get_github_runtime()
        repos = (
            [self._serialize_repo(r) for r in gh_runtime.list_repositories()]
            if gh_runtime.configured else []
        )

        infra_runtime = self._get_infrastructure_runtime()
        services = (
            [self._serialize_service(s) for s in infra_runtime.list_services()]
            if infra_runtime.configured else []
        )

        n8n_runtime = self._get_n8n_runtime()
        workflows = (
            [self._serialize_workflow(w) for w in n8n_runtime.list_workflows()]
            if n8n_runtime.configured else []
        )

        nocodb_runtime = self._get_nocodb_runtime()
        databases = (
            [self._serialize_database(d) for d in nocodb_runtime.list_databases()]
            if nocodb_runtime.configured else []
        )
        tables = (
            [self._serialize_table(t) for t in nocodb_runtime.list_tables()]
            if nocodb_runtime.configured else []
        )

        llm_runtime = self._get_llm_runtime()
        llm_providers = (
            [self._serialize_llm_provider(p) for p in llm_runtime.list_providers()]
            if llm_runtime.configured else []
        )
        llm_models = (
            [self._serialize_llm_model(m) for m in llm_runtime.list_models()]
            if llm_runtime.configured else []
        )

        # Business-layer objects
        capabilities = [
            self._serialize_capability_summary(c)
            for c in self.context_engine.capability_engine.registry.list()
        ]
        goals = [
            self._serialize_goal_summary(g)
            for g in self.goal_registry.list()
        ]
        people = [
            self._serialize_person_summary(p)
            for p in self.people_registry.list()
        ]
        departments = [
            self._serialize_department_summary(d)
            for d in self.department_registry.list()
        ]

        # Heartbeats by operation (newest first)
        heartbeats_by_operation: dict[str, list[dict]] = {}
        if self.heartbeat_store:
            from hermes.kernel.heartbeat_runtime import get_latest
            for op in ops:
                if op.status in ("created", "executing"):
                    hbs = self.heartbeat_store.list_by_operation(
                        workspace_id, op.id,
                    )
                    if hbs:
                        heartbeats_by_operation[op.id] = [
                            {"status": hb.status, "summary": hb.summary}
                            for hb in hbs
                        ]

        return ReadinessSnapshot(
            workspace_id=workspace_id,
            services=services,
            repositories=repos,
            workflows=workflows,
            databases=databases,
            tables=tables,
            llm_providers=llm_providers,
            models=llm_models,
            operations=operations,
            notifications=notif_dicts,
            capabilities=capabilities,
            goals=goals,
            people=people,
            departments=departments,
            kpis=kpis,
            decisions=decisions,
            heartbeats_by_operation=heartbeats_by_operation,
            infrastructure_configured=infra_runtime.configured,
            github_configured=gh_runtime.configured,
            n8n_configured=n8n_runtime.configured,
            nocodb_configured=nocodb_runtime.configured,
            llm_configured=llm_runtime.configured,
        )

    @staticmethod
    def _serialize_readiness_report(report) -> dict:
        """Convert a ReadinessReport to a JSON-serializable dict."""

        def _ser_issue(issue) -> dict:
            return {
                "category": issue.category,
                "object_type": issue.object_type,
                "object_id": issue.object_id,
                "name": issue.name,
                "reason": issue.reason,
                "severity": issue.severity,
            }

        def _ser_category(result) -> dict:
            return {
                "category": result.category,
                "status": result.status,
                "score": result.score,
                "blockers": [_ser_issue(i) for i in result.blockers],
                "warnings": [_ser_issue(i) for i in result.warnings],
                "checked": result.checked,
                "healthy": result.healthy,
            }

        def _ser_checklist(item) -> dict:
            return {
                "priority": item.priority,
                "category": item.category,
                "check": item.check,
                "status": item.status,
                "issues": [_ser_issue(i) for i in item.issues],
            }

        categories = {}
        for cat_name, cat_result in report.categories.items():
            categories[cat_name] = _ser_category(cat_result)

        return {
            "workspace_id": report.workspace_id,
            "scenario": report.scenario,
            "scenario_label": report.scenario_label,
            "overall_status": report.overall_status,
            "overall_score": report.overall_score,
            "categories": categories,
            "blockers": [_ser_issue(i) for i in report.blockers],
            "warnings": [_ser_issue(i) for i in report.warnings],
            "checklist": [_ser_checklist(i) for i in report.checklist],
            "critical_dependencies": report.critical_dependencies,
            "timestamp": report.timestamp,
        }

    @staticmethod
    def _serialize_category_result(result) -> dict:
        """Convert a CategoryResult to a JSON-serializable dict."""

        def _ser_issue(issue) -> dict:
            return {
                "category": issue.category,
                "object_type": issue.object_type,
                "object_id": issue.object_id,
                "name": issue.name,
                "reason": issue.reason,
                "severity": issue.severity,
            }

        return {
            "category": result.category,
            "status": result.status,
            "score": result.score,
            "blockers": [_ser_issue(i) for i in result.blockers],
            "warnings": [_ser_issue(i) for i in result.warnings],
            "checked": result.checked,
            "healthy": result.healthy,
        }

    # -- Jobs ------------------------------------------------------------------

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
        data = {
            "id": op.id,
            "workspace_id": op.workspace_id,
            "request": op.request,
            "status": op.status,
            "created_at": op.created_at.isoformat(),
            "updated_at": op.updated_at.isoformat(),
        }
        if op.outcome is not None:
            data["outcome"] = op.outcome
        if op.outcome_classification is not None:
            data["outcome_classification"] = op.outcome_classification
        if op.decision_id is not None:
            data["decision_id"] = op.decision_id
        if op.recommendation_id is not None:
            data["recommendation_id"] = op.recommendation_id
        if op.review_id is not None:
            data["review_id"] = op.review_id
        if op.sop_id is not None:
            data["sop_id"] = op.sop_id
        return data

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
        """Return the Workspace Home operating summary.

        Every card answers one of four executive questions:
          1. What needs my attention?   → notifications
          2. What changed?              → operations, heartbeat_pulse
          3. What decisions are waiting? → pending_decisions
          4. What should I do next?     → todays_focus
        """
        self.validate_workspace(workspace_id)

        ws_context = self.context_engine.workspace_engine.resolve(workspace_id)
        ws = ws_context.workspace

        # Live operation stats
        ops: list = []
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

        # CEO Review — run once, propagated explicitly (Amendment 1)
        review = self._run_ceo_review(workspace_id)
        brief = review.brief

        # Top 3 priorities — "What should I do next?"
        todays_focus = brief.priorities[:3]

        # Pending decisions — "What decisions are waiting?"
        pending_decisions = len(review.engine_result.recommendations)

        # Heartbeat pulse — "What changed?" (blocked + stale)
        heartbeat_pulse = {"blocked": 0, "stale": 0, "oldest_stale_hours": 0.0}
        if self.heartbeat_store and self.operation_store:
            threshold = config.stale_threshold_hours()
            stale_ops = find_stale_operations(
                ops, self.heartbeat_store, workspace_id, threshold,
            )
            heartbeat_pulse["stale"] = len(stale_ops)
            if stale_ops:
                heartbeat_pulse["oldest_stale_hours"] = max(
                    s.elapsed_hours for s in stale_ops
                )
            for op in ops:
                if op.status in ("created", "executing", "awaiting_escalation"):
                    latest = get_latest(self.heartbeat_store, workspace_id, op.id)
                    if latest and latest.status == "blocked":
                        heartbeat_pulse["blocked"] += 1

        # Amendment 4: Extensible runtime health summaries
        runtime_health = self._compute_runtime_health()

        return {
            "workspace": {"name": ws.name or workspace_id},
            "operations": {
                "active": ops_active,
                "completed_today": ops_completed_today,
                "total": ops_total,
            },
            "todays_focus": todays_focus,
            "pending_decisions": pending_decisions,
            "heartbeat_pulse": heartbeat_pulse,
            "notifications": self._compute_notification_summary(
                workspace_id, ops, review,
            ),
            "infrastructure": runtime_health.get("infrastructure", {}),
            "automation": runtime_health.get("automation", {}),
            "runtime_health": runtime_health,
        }

    def _compute_runtime_health(self) -> dict:
        """Compute extensible runtime health summaries (Amendment 4).

        Returns a dict of runtime name → summary. New runtimes plug in
        by adding a block here — no UI changes needed.
        """
        result: dict[str, dict] = {}

        # GitHub
        gh_runtime = self._get_github_runtime()
        gh_summary: dict = {"configured": gh_runtime.configured, "name": "GitHub"}
        if gh_runtime.configured:
            try:
                h = gh_runtime.health()
                gh_summary["reachable"] = h.reachable
                gh_summary["authenticated"] = h.authenticated
            except Exception:
                pass
        result["github"] = gh_summary

        # Infrastructure
        infra_runtime = self._get_infrastructure_runtime()
        infra_summary: dict = {"configured": False, "name": "Infrastructure", "total": 0, "healthy": 0, "unhealthy": 0, "resource_critical": 0}
        if infra_runtime.configured:
            try:
                services = infra_runtime.list_services()
                infra_summary["configured"] = True
                infra_summary["total"] = len(services)
                infra_summary["healthy"] = sum(1 for s in services if s.health == "healthy")
                infra_summary["unhealthy"] = sum(1 for s in services if s.health == "unhealthy")
                infra_summary["resource_critical"] = sum(1 for s in services if s.resource_state == "critical")
            except Exception:
                pass
        result["infrastructure"] = infra_summary

        # n8n / Automation
        n8n_runtime = self._get_n8n_runtime()
        auto_summary: dict = {"configured": False, "name": "Automation", "total": 0, "active": 0, "failing": 0, "attention_critical": 0}
        if n8n_runtime.configured:
            try:
                workflows = n8n_runtime.list_workflows()
                auto_summary["configured"] = True
                auto_summary["total"] = len(workflows)
                auto_summary["active"] = sum(1 for w in workflows if w.active)
                auto_summary["failing"] = sum(1 for w in workflows if w.status == "error")
                auto_summary["attention_critical"] = sum(1 for w in workflows if w.attention_state == "critical")
            except Exception:
                pass
        result["automation"] = auto_summary

        # NocoDB / Data (Sprint 44)
        nocodb_runtime = self._get_nocodb_runtime()
        data_summary: dict = {"configured": False, "name": "Data", "total_databases": 0, "total_tables": 0, "total_records": 0}
        if nocodb_runtime.configured:
            try:
                databases = nocodb_runtime.list_databases()
                data_summary["configured"] = True
                data_summary["total_databases"] = len(databases)
                data_summary["total_tables"] = sum(db.table_count for db in databases)
                data_summary["total_records"] = sum(db.record_count for db in databases)
            except Exception:
                pass
        result["data"] = data_summary

        # LLM / AI (Sprint 45)
        llm_runtime = self._get_llm_runtime()
        ai_summary: dict = {"configured": False, "name": "AI", "providers": 0, "healthy": 0, "models": 0, "default_provider": "", "default_model": ""}
        if llm_runtime.configured:
            try:
                providers = llm_runtime.list_providers()
                models = llm_runtime.list_models()
                ai_summary["configured"] = True
                ai_summary["providers"] = len(providers)
                ai_summary["healthy"] = sum(1 for p in providers if p.health_state == "healthy")
                ai_summary["models"] = len(models)
                ai_summary["default_provider"] = config.llm_default_provider()
                ai_summary["default_model"] = config.llm_default_model()
            except Exception:
                pass
        result["ai"] = ai_summary

        return result

    def _compute_notification_summary(
        self, workspace_id: str, ops: list, review: object,
    ) -> dict:
        """Compact notification counters — reuses caller's review (no extra CEO Loop)."""
        notifications = self._generate_notifications(
            workspace_id, ops=ops, review=review,
        )
        unread = [n for n in notifications if not n.acknowledged]
        critical = sum(1 for n in unread if n.severity == "critical")
        warning = sum(1 for n in unread if n.severity == "warning")

        return {
            "unread_count": len(unread),
            "critical_count": critical,
            "warning_count": warning,
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

        # Lessons (for Business Journal)
        lessons = [
            {
                "id": l.lesson_id,
                "title": l.title,
                "source": l.source,
                "date": l.date,
                "recommendation": l.recommendation,
            }
            for l in data.lessons
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
            "lessons": lessons,
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
