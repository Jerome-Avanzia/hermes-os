"""Hermes Gateway — protocol boundary between the Workspace UI and HermesService.

Exposes workspace listing (GET /v1/workspaces), workspace-scoped API routes
under /v1/workspaces/{workspace_id}/..., health check (GET /health),
and the static Workspace shell (GET /).

The Gateway performs protocol translation only (ADR-0002). All requests
are delegated to HermesService, which orchestrates context assembly,
conversation rendering, and operation lifecycle management.

Workspace validation is a service-layer concern — the Gateway only
translates WorkspaceNotFoundError into HTTP 404 responses.
"""

import asyncio
import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hermes.conductor import Conductor
from hermes.kernel.acknowledgement_store import AcknowledgementStore
from hermes.kernel.heartbeat_runtime import InvalidHeartbeatStatusError
from hermes.kernel.heartbeat_store import HeartbeatStore
from hermes.kernel.job_store import JobStore
from hermes.kernel.operation_store import OperationNotFoundError, OperationStore
from hermes.kernel.profile_loader import ProfileLoader
from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError
from hermes.kernel.operation_runtime import StepNotActionableError, StepNotFoundError
from hermes.models.operation import InvalidTransitionError
from hermes.providers.ollama_provider import ChatMessage, OllamaConnectionError, OllamaProvider
from hermes.runtime.context_engine import ContextEngine
from hermes.service import HermesService, RecommendationNotFoundError

logger = logging.getLogger(__name__)

app = FastAPI(title="Hermes Gateway", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("HERMES_CORS_ORIGINS", "*").split(","),
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# -- Request / response models ---------------------------------------------


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]
    model: str | None = None
    profile: str | None = None
    stream: bool = True


class CreateOperationRequest(BaseModel):
    request: str


class CompleteOperationRequest(BaseModel):
    outcome: str
    outcome_classification: str = "success"


class LinkSOPRequest(BaseModel):
    sop_id: str


class CompleteStepRequest(BaseModel):
    step_id: str


class CreateHeartbeatRequest(BaseModel):
    status: str
    summary: str
    author: str = ""
    details: str = ""
    blocker: str = ""
    next_action: str = ""


class CreateDecisionRequest(BaseModel):
    recommendation_id: str
    action: str
    create_operation: bool = False


class DispatchEngineeringJobRequest(BaseModel):
    task: str
    repo: str


# -- Application singletons ------------------------------------------------


def _build_provider(model: str | None = None) -> OllamaProvider:
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = model or os.environ.get("OLLAMA_MODEL", "llama3.2")
    return OllamaProvider(model=ollama_model, base_url=ollama_url)


_profile_loader = ProfileLoader()
_workspace_engine = WorkspaceEngine()


_operation_store = OperationStore()
_job_store = JobStore()
_heartbeat_store = HeartbeatStore()
_acknowledgement_store = AcknowledgementStore()


def _build_hermes_service(model: str | None = None) -> HermesService:
    provider = _build_provider(model)
    conductor = Conductor(provider=provider, profile_loader=_profile_loader)
    context_engine = ContextEngine(
        workspace_engine=_workspace_engine,
        profile_loader=_profile_loader,
    )
    return HermesService(
        context_engine=context_engine,
        conductor=conductor,
        operation_store=_operation_store,
        job_store=_job_store,
        heartbeat_store=_heartbeat_store,
        acknowledgement_store=_acknowledgement_store,
    )


_hermes_service = _build_hermes_service()

from hermes.kernel.engineering_job_runner import EngineeringJobRunner, EngineeringJobNotFoundError, EngineeringJobStore  # noqa: E402

_engineering_job_store = EngineeringJobStore()
_engineering_job_runner = EngineeringJobRunner(job_store=_engineering_job_store)
_hermes_service.engineering_job_runner = _engineering_job_runner


# -- Exception handlers ----------------------------------------------------


@app.exception_handler(WorkspaceNotFoundError)
async def workspace_not_found_handler(request: Request, exc: WorkspaceNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": str(exc)},
    )


@app.exception_handler(EngineeringJobNotFoundError)
async def engineering_job_not_found_handler(request: Request, exc: EngineeringJobNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": str(exc)})


# -- SSE helpers ------------------------------------------------------------


def _sse_stream(tokens: Iterator[str]) -> Iterator[str]:
    """Wrap token chunks as SSE ``data:`` frames."""
    try:
        for token in tokens:
            payload = json.dumps({"content": token})
            yield f"data: {payload}\n\n"
    except OllamaConnectionError as exc:
        logger.error("Provider connection lost mid-stream: %s", exc)
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"
    yield "data: [DONE]\n\n"


# -- Endpoints --------------------------------------------------------------


@app.get("/v1/workspaces")
async def list_workspaces() -> list[dict]:
    """Return all registered workspaces."""
    return _hermes_service.list_workspaces()


@app.post("/v1/workspaces/{workspace_id}/chat")
async def chat(workspace_id: str, request: ChatRequest) -> StreamingResponse:
    service = _hermes_service
    if request.model:
        service = _build_hermes_service(request.model)

    messages = [
        ChatMessage(role=m["role"], content=m["content"])
        for m in request.messages
    ]

    try:
        if request.stream:
            tokens = service.stream_chat(
                messages,
                workspace_id=workspace_id,
                profile_id=request.profile,
            )
            return StreamingResponse(
                _sse_stream(tokens),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        result = service.chat(
            messages,
            workspace_id=workspace_id,
            profile_id=request.profile,
        )
        return StreamingResponse(
            iter([json.dumps({"content": result})]),
            media_type="application/json",
        )
    except OllamaConnectionError as exc:
        logger.error("Provider unreachable: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"error": str(exc)},
        )


@app.get("/v1/workspaces/{workspace_id}/profiles")
async def list_profiles(workspace_id: str) -> list[dict]:
    _hermes_service.validate_workspace(workspace_id)
    profiles = _profile_loader.list_all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
        }
        for p in profiles
    ]


@app.get("/v1/workspaces/{workspace_id}/workspace")
async def get_workspace(workspace_id: str) -> dict:
    """Return the workspace context for the UI bootstrap."""
    try:
        ctx = _workspace_engine.resolve(workspace_id)
    except WorkspaceNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"No registered workspace for project: {workspace_id}"},
        )

    ws = ctx.workspace
    default_profile = _profile_loader.get_default()

    provider = _build_provider()

    return {
        "workspace": {
            "id": ws.project_id,
            "name": ws.name or ws.project_id,
            "description": ws.description,
            "mission": ws.mission,
        },
        "profiles": ws.profiles,
        "repositories": [
            {"name": r.name, "branch": r.branch}
            for r in ctx.repositories
        ],
        "profile": {
            "id": default_profile.id,
            "name": default_profile.name,
        },
        "gateway": {
            "version": app.version,
        },
        "model": {
            "name": provider._model,
        },
        "sprint": None,
    }


@app.get("/v1/workspaces/{workspace_id}/dashboard")
async def get_dashboard(workspace_id: str) -> dict:
    """Return the Workspace Home operating summary."""
    return _hermes_service.get_dashboard(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/brief")
async def get_brief(workspace_id: str) -> JSONResponse:
    """Return the full Executive Brief from a CEO Review."""
    result = _hermes_service.get_brief(workspace_id)
    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/knowledge")
async def list_knowledge(workspace_id: str) -> list[dict]:
    """List all Knowledge Documents for the workspace."""
    return _hermes_service.list_knowledge(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/knowledge/{document_id:path}")
async def get_knowledge(workspace_id: str, document_id: str) -> JSONResponse:
    """Return a single Knowledge Document with full content.

    Sprint 48: document_id uses path converter to support architecture
    knowledge IDs containing colons (e.g. ``arch:decisions:ADR-0001``).
    """
    doc = _hermes_service.get_knowledge(workspace_id, document_id)
    if doc is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Knowledge document not found: {document_id}"},
        )
    return JSONResponse(content=doc)


@app.get("/v1/architecture-sources")
async def list_architecture_sources() -> list[dict]:
    """List available architecture knowledge source categories (Sprint 48)."""
    return _hermes_service.list_architecture_sources()


@app.get("/v1/workspaces/{workspace_id}/operations")
async def list_operations(workspace_id: str) -> list[dict]:
    """List all Operations for the workspace."""
    return _hermes_service.list_operations(workspace_id)


@app.post("/v1/workspaces/{workspace_id}/operations")
async def create_operation(workspace_id: str, body: CreateOperationRequest) -> JSONResponse:
    """Create an Operation from a conversation promotion."""
    result = _hermes_service.create_operation_from_chat(
        workspace_id, body.request
    )
    return JSONResponse(status_code=201, content=result)


@app.get("/v1/workspaces/{workspace_id}/operations/stale")
async def list_stale_operations(
    workspace_id: str, threshold_hours: float | None = None
) -> list[dict]:
    """Return active Operations whose latest heartbeat exceeds the freshness threshold."""
    return _hermes_service.list_stale_operations(workspace_id, threshold_hours)


@app.get("/v1/workspaces/{workspace_id}/operations/{operation_id}")
async def get_operation(workspace_id: str, operation_id: str) -> JSONResponse:
    """Return a single Operation."""
    op = _hermes_service.get_operation(workspace_id, operation_id)
    if op is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    return JSONResponse(content=op)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/approve")
async def approve_operation(workspace_id: str, operation_id: str) -> JSONResponse:
    """Approve an escalated Operation, returning it to executing."""
    try:
        result = _hermes_service.approve_operation(workspace_id, operation_id)
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except InvalidTransitionError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": str(exc)},
        )
    return JSONResponse(content=result)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/reject")
async def reject_operation(workspace_id: str, operation_id: str) -> JSONResponse:
    """Reject an escalated Operation."""
    try:
        result = _hermes_service.reject_operation(workspace_id, operation_id)
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except InvalidTransitionError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": str(exc)},
        )
    return JSONResponse(content=result)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/complete")
async def complete_operation(
    workspace_id: str, operation_id: str, body: CompleteOperationRequest
) -> JSONResponse:
    """Complete an executing Operation with an outcome."""
    try:
        result = _hermes_service.complete_operation(
            workspace_id, operation_id, body.outcome, body.outcome_classification
        )
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except InvalidTransitionError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": str(exc)},
        )
    return JSONResponse(content=result)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/fail")
async def fail_operation(
    workspace_id: str, operation_id: str, body: CompleteOperationRequest
) -> JSONResponse:
    """Fail an executing Operation with an outcome."""
    try:
        result = _hermes_service.fail_operation(
            workspace_id, operation_id, body.outcome, body.outcome_classification
        )
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except InvalidTransitionError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": str(exc)},
        )
    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/operations/{operation_id}/progress")
async def get_operation_progress(workspace_id: str, operation_id: str) -> JSONResponse:
    """Return SOP step progress for an Operation."""
    try:
        result = _hermes_service.get_operation_progress(workspace_id, operation_id)
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"error": "No SOP linked to this operation"},
        )
    return JSONResponse(content=result)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/steps/complete")
async def complete_operation_step(
    workspace_id: str, operation_id: str, body: CompleteStepRequest
) -> JSONResponse:
    """Complete the next SOP step for an Operation."""
    try:
        result = _hermes_service.complete_operation_step(
            workspace_id, operation_id, body.step_id
        )
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except StepNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": str(exc)},
        )
    except StepNotActionableError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": str(exc)},
        )
    return JSONResponse(content=result)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/link-sop")
async def link_sop_to_operation(
    workspace_id: str, operation_id: str, body: LinkSOPRequest
) -> JSONResponse:
    """Link an SOP to an Operation."""
    try:
        result = _hermes_service.link_sop_to_operation(
            workspace_id, operation_id, body.sop_id
        )
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except StepNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": str(exc)},
        )
    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/operations/{operation_id}/heartbeats")
async def list_heartbeats(workspace_id: str, operation_id: str) -> JSONResponse:
    """List all Heartbeats for an Operation, newest first."""
    try:
        result = _hermes_service.list_operation_heartbeats(workspace_id, operation_id)
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    return JSONResponse(content=result)


@app.post("/v1/workspaces/{workspace_id}/operations/{operation_id}/heartbeats")
async def create_heartbeat(
    workspace_id: str, operation_id: str, body: CreateHeartbeatRequest
) -> JSONResponse:
    """Append an immutable Heartbeat to an Operation."""
    try:
        result = _hermes_service.create_heartbeat(
            workspace_id, operation_id,
            status=body.status,
            summary=body.summary,
            author=body.author,
            details=body.details,
            blocker=body.blocker,
            next_action=body.next_action,
        )
    except OperationNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    except InvalidHeartbeatStatusError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": str(exc)},
        )
    return JSONResponse(status_code=201, content=result)


@app.get("/v1/workspaces/{workspace_id}/people")
async def list_people(workspace_id: str) -> list[dict]:
    """List all organizational people with workload metrics."""
    return _hermes_service.list_people(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/people/{person_id}")
async def get_person(workspace_id: str, person_id: str) -> JSONResponse:
    """Return full person detail with ownership breakdown."""
    person = _hermes_service.get_person(workspace_id, person_id)
    if person is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Person not found: {person_id}"},
        )
    return JSONResponse(content=person)


@app.get("/v1/workspaces/{workspace_id}/context/{object_type}/{object_id}")
async def get_context(workspace_id: str, object_type: str, object_id: str) -> JSONResponse:
    """Return the context graph for a business object."""
    from hermes.context.context_graph import SUPPORTED_TYPES

    if object_type not in SUPPORTED_TYPES:
        return JSONResponse(
            status_code=422,
            content={"error": f"Unknown object type: {object_type}. Supported: {', '.join(sorted(SUPPORTED_TYPES))}"},
        )

    try:
        result = _hermes_service.get_context(workspace_id, object_type, object_id)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    if result is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"{object_type} not found: {object_id}"},
        )
    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/impact/{object_type}/{object_id}")
async def get_impact(
    workspace_id: str,
    object_type: str,
    object_id: str,
    max_depth: int = 3,
    direction: str = "forward",
) -> JSONResponse:
    """Compute impact analysis for a business object (Sprint 46)."""
    from hermes.context.context_graph import SUPPORTED_TYPES

    if object_type not in SUPPORTED_TYPES:
        return JSONResponse(
            status_code=422,
            content={"error": f"Unknown object type: {object_type}. Supported: {', '.join(sorted(SUPPORTED_TYPES))}"},
        )

    if direction not in ("forward", "reverse"):
        return JSONResponse(
            status_code=422,
            content={"error": "direction must be 'forward' or 'reverse'"},
        )

    max_depth = max(1, min(5, max_depth))

    try:
        result = _hermes_service.impact(
            workspace_id, object_type, object_id, max_depth, direction,
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    # If the source object was not found, the report still returns but
    # we check if it has zero affected and source risk_reasons says not found
    if (
        result.get("total_affected", 0) == 0
        and "Object not found" in (result.get("source", {}).get("risk_reasons", []))
    ):
        return JSONResponse(
            status_code=404,
            content={"error": f"{object_type} not found: {object_id}"},
        )

    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/readiness")
async def get_readiness(
    workspace_id: str,
    scenario: str = "deployment",
) -> JSONResponse:
    """Compute executive readiness evaluation for a scenario (Sprint 47)."""
    from hermes.context.readiness_engine import SCENARIO_NAMES

    if scenario not in SCENARIO_NAMES:
        return JSONResponse(
            status_code=422,
            content={"error": f"Unknown scenario: {scenario}. Valid: {', '.join(sorted(SCENARIO_NAMES))}"},
        )

    try:
        result = _hermes_service.readiness(workspace_id, scenario)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/readiness/{category}")
async def get_readiness_category(
    workspace_id: str,
    category: str,
    scenario: str = "deployment",
) -> JSONResponse:
    """Evaluate a single readiness category within a scenario (Sprint 47)."""
    from hermes.context.readiness_engine import ALL_CATEGORIES, SCENARIO_NAMES

    if category not in ALL_CATEGORIES:
        return JSONResponse(
            status_code=422,
            content={"error": f"Unknown category: {category}. Valid: {', '.join(sorted(ALL_CATEGORIES))}"},
        )

    if scenario not in SCENARIO_NAMES:
        return JSONResponse(
            status_code=422,
            content={"error": f"Unknown scenario: {scenario}. Valid: {', '.join(sorted(SCENARIO_NAMES))}"},
        )

    try:
        result = _hermes_service.readiness_category(
            workspace_id, category, scenario,
        )
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"error": str(exc)})

    return JSONResponse(content=result)


@app.get("/v1/workspaces/{workspace_id}/goals")
async def list_goals(workspace_id: str) -> list[dict]:
    """List all strategic goals with cross-reference counts."""
    return _hermes_service.list_goals(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/goals/{goal_id}")
async def get_goal(workspace_id: str, goal_id: str) -> JSONResponse:
    """Return full goal detail with KPIs, Decisions, Operations, Capabilities."""
    goal = _hermes_service.get_goal(workspace_id, goal_id)
    if goal is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Goal not found: {goal_id}"},
        )
    return JSONResponse(content=goal)


@app.get("/v1/workspaces/{workspace_id}/departments")
async def list_departments(workspace_id: str) -> list[dict]:
    """List all departments with aggregate metrics."""
    return _hermes_service.list_departments(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/departments/{department_id}")
async def get_department(workspace_id: str, department_id: str) -> JSONResponse:
    """Return department detail with capabilities and SOP count."""
    dept = _hermes_service.get_department(workspace_id, department_id)
    if dept is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Department not found: {department_id}"},
        )
    return JSONResponse(content=dept)


@app.get("/v1/workspaces/{workspace_id}/capabilities")
async def list_capabilities(workspace_id: str) -> list[dict]:
    """List all organizational capabilities."""
    return _hermes_service.list_capabilities(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/capabilities/{capability_id}")
async def get_capability(workspace_id: str, capability_id: str) -> JSONResponse:
    """Return full detail for a single capability."""
    cap = _hermes_service.get_capability(workspace_id, capability_id)
    if cap is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Capability not found: {capability_id}"},
        )
    return JSONResponse(content=cap)


@app.get("/v1/workspaces/{workspace_id}/sops")
async def list_sops(workspace_id: str) -> list[dict]:
    """List all SOPs (summaries, no content)."""
    return _hermes_service.list_sops(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/sops/{sop_id:path}")
async def get_sop(workspace_id: str, sop_id: str) -> JSONResponse:
    """Return full SOP detail with markdown content."""
    sop = _hermes_service.get_sop(workspace_id, sop_id)
    if sop is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"SOP not found: {sop_id}"},
        )
    return JSONResponse(content=sop)


@app.get("/v1/workspaces/{workspace_id}/jobs")
async def list_jobs(workspace_id: str) -> list[dict]:
    """List all Jobs for the workspace."""
    return _hermes_service.list_jobs(workspace_id)


@app.get("/v1/workspaces/{workspace_id}/jobs/{job_id}")
async def get_job(workspace_id: str, job_id: str) -> JSONResponse:
    """Return a single Job with full output."""
    job = _hermes_service.get_job(workspace_id, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Job not found: {job_id}"},
        )
    return JSONResponse(content=job)


@app.get("/v1/workspaces/{workspace_id}/decisions")
async def list_decisions(workspace_id: str) -> list[dict]:
    """List all Decisions from the business knowledge layer."""
    return _hermes_service.list_decisions(workspace_id)


@app.post("/v1/workspaces/{workspace_id}/decisions")
async def create_decision(workspace_id: str, body: CreateDecisionRequest) -> JSONResponse:
    """Act on a recommendation to create a tracked Decision."""
    from hermes.service import VALID_DECISION_ACTIONS

    if body.action not in VALID_DECISION_ACTIONS:
        return JSONResponse(
            status_code=422,
            content={"error": f"Invalid action: {body.action}. Must be one of: {', '.join(sorted(VALID_DECISION_ACTIONS))}"},
        )

    try:
        result = _hermes_service.act_on_recommendation(
            workspace_id,
            body.recommendation_id,
            body.action,
            create_operation=body.create_operation,
        )
    except RecommendationNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content={"error": str(exc)},
        )
    return JSONResponse(status_code=201, content=result)


@app.get("/v1/workspaces/{workspace_id}/notifications")
async def list_notifications(workspace_id: str) -> JSONResponse:
    """Return all current notifications with unread count."""
    return JSONResponse(content=_hermes_service.list_notifications(workspace_id))


@app.get("/v1/workspaces/{workspace_id}/notifications/unread")
async def list_unread_notifications(workspace_id: str) -> JSONResponse:
    """Return only unacknowledged notifications."""
    return JSONResponse(content=_hermes_service.list_unread_notifications(workspace_id))


@app.post("/v1/workspaces/{workspace_id}/notifications/{notification_id}/acknowledge")
async def acknowledge_notification(workspace_id: str, notification_id: str) -> JSONResponse:
    """Acknowledge a notification. Idempotent."""
    result = _hermes_service.acknowledge_notification(workspace_id, notification_id)
    return JSONResponse(content=result)


@app.get("/v1/repositories")
async def list_repositories() -> list[dict]:
    """List all code repositories from configured provider."""
    return _hermes_service.list_repositories()


@app.get("/v1/repositories/{repo_id}")
async def get_repository(repo_id: str) -> JSONResponse:
    """Return a single repository by slug."""
    repo = _hermes_service.get_repository(repo_id)
    if repo is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Repository not found: {repo_id}"},
        )
    return JSONResponse(content=repo)


@app.get("/v1/services")
async def list_services() -> list[dict]:
    """List all infrastructure services."""
    return _hermes_service.list_services()


@app.get("/v1/services/{service_id}")
async def get_service(service_id: str) -> JSONResponse:
    """Return a single infrastructure service by ID."""
    svc = _hermes_service.get_service(service_id)
    if svc is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Service not found: {service_id}"},
        )
    return JSONResponse(content=svc)


@app.get("/v1/workflows")
async def list_workflows() -> list[dict]:
    """List all automation workflows."""
    return _hermes_service.list_workflows()


@app.get("/v1/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> JSONResponse:
    """Return a single workflow by Hermes ID."""
    wf = _hermes_service.get_workflow(workflow_id)
    if wf is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Workflow not found: {workflow_id}"},
        )
    return JSONResponse(content=wf)


@app.post("/v1/workspaces/{workspace_id}/engineering/jobs", status_code=202)
async def dispatch_engineering_job(workspace_id: str, request: DispatchEngineeringJobRequest) -> dict:
    _hermes_service.validate_workspace(workspace_id)
    workspace = _workspace_engine.resolve(workspace_id)
    job = _hermes_service.create_engineering_job(workspace_id, request.task, request.repo)
    asyncio.create_task(
        asyncio.to_thread(
            _engineering_job_runner.run,
            workspace_id,
            workspace.workspace.path,
            job.job_id,
        )
    )
    return {"job_id": job.job_id, "status": job.status, "dispatched_at": job.dispatched_at}


@app.get("/v1/workspaces/{workspace_id}/engineering/jobs/{job_id}")
async def get_engineering_job(workspace_id: str, job_id: str) -> dict:
    _hermes_service.validate_workspace(workspace_id)
    job = _hermes_service.get_engineering_job(workspace_id, job_id)
    return job.to_dict()


@app.get("/v1/workspaces/{workspace_id}/engineering/jobs")
async def list_engineering_jobs(workspace_id: str) -> list[dict]:
    _hermes_service.validate_workspace(workspace_id)
    jobs = _hermes_service.list_engineering_jobs(workspace_id)
    return [j.to_dict() for j in jobs]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/github")
async def github_health() -> dict:
    """GitHub integration health check (Amendment 6)."""
    return _hermes_service.github_health()


@app.get("/health/infrastructure")
async def infrastructure_health() -> dict:
    """Infrastructure runtime health check (Sprint 42)."""
    return _hermes_service.infrastructure_health()


@app.get("/health/n8n")
async def n8n_health() -> dict:
    """n8n runtime health check (Sprint 43)."""
    return _hermes_service.n8n_health()


# -- NocoDB Runtime (Sprint 44) ------------------------------------------------


@app.get("/v1/databases")
async def list_databases() -> list[dict]:
    """List all databases."""
    return _hermes_service.list_databases()


@app.get("/v1/databases/{database_id}")
async def get_database(database_id: str) -> JSONResponse:
    """Get a single database by Hermes ID."""
    result = _hermes_service.get_database(database_id)
    if result is None:
        return JSONResponse({"error": "Database not found"}, status_code=404)
    return JSONResponse(result)


@app.get("/v1/tables")
async def list_tables() -> list[dict]:
    """List all tables across all databases."""
    return _hermes_service.list_tables()


@app.get("/v1/tables/{table_id:path}")
async def get_table(table_id: str) -> JSONResponse:
    """Get a single table by Hermes ID."""
    result = _hermes_service.get_table(table_id)
    if result is None:
        return JSONResponse({"error": "Table not found"}, status_code=404)
    return JSONResponse(result)


@app.get("/health/nocodb")
async def nocodb_health() -> dict:
    """NocoDB runtime health check (Sprint 44)."""
    return _hermes_service.nocodb_health()


# -- LLM Runtime (Sprint 45) --------------------------------------------------


@app.get("/v1/llm-providers")
async def list_llm_providers() -> list[dict]:
    """List all LLM providers."""
    return _hermes_service.list_llm_providers()


@app.get("/v1/llm-providers/{provider_id}")
async def get_llm_provider(provider_id: str) -> JSONResponse:
    """Get a single LLM provider by Hermes ID."""
    result = _hermes_service.get_llm_provider(provider_id)
    if result is None:
        return JSONResponse({"error": "Provider not found"}, status_code=404)
    return JSONResponse(result)


@app.get("/v1/llm-models")
async def list_llm_models() -> list[dict]:
    """List all LLM models across all providers."""
    return _hermes_service.list_llm_models()


@app.get("/v1/llm-models/{model_id:path}")
async def get_llm_model(model_id: str) -> JSONResponse:
    """Get a single LLM model by Hermes ID."""
    result = _hermes_service.get_llm_model(model_id)
    if result is None:
        return JSONResponse({"error": "Model not found"}, status_code=404)
    return JSONResponse(result)


@app.get("/health/llm")
async def llm_health() -> dict:
    """LLM runtime health check (Sprint 45)."""
    return _hermes_service.llm_health()


# -- Static UI (must be mounted last so API routes take precedence) ---------

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
