"""Hermes Gateway — protocol boundary between the Workspace UI and HermesService.

Exposes SSE chat (POST /v1/chat), profile listing (GET /v1/profiles),
workspace bootstrap (GET /v1/workspace), dashboard summary (GET /v1/dashboard),
knowledge browsing (GET /v1/knowledge), operations management
(GET/POST /v1/operations), jobs listing (GET /v1/jobs),
health check (GET /health), and the static Workspace shell (GET /).

The Gateway performs protocol translation only (ADR-0002). All requests
are delegated to HermesService, which orchestrates context assembly,
conversation rendering, and operation lifecycle management.
"""

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hermes.conductor import Conductor
from hermes.kernel.job_store import JobStore
from hermes.kernel.operation_store import OperationNotFoundError, OperationStore
from hermes.kernel.profile_loader import ProfileLoader
from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError
from hermes.models.operation import InvalidTransitionError
from hermes.providers.ollama_provider import ChatMessage, OllamaConnectionError, OllamaProvider
from hermes.runtime.context_engine import ContextEngine
from hermes.service import HermesService

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


# -- Application singletons ------------------------------------------------


def _build_provider(model: str | None = None) -> OllamaProvider:
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = model or os.environ.get("OLLAMA_MODEL", "llama3.2")
    return OllamaProvider(model=ollama_model, base_url=ollama_url)


_profile_loader = ProfileLoader()
_workspace_engine = WorkspaceEngine()
_active_workspace_id = os.environ.get("HERMES_WORKSPACE", "AVANZIA")


_operation_store = OperationStore()
_job_store = JobStore()


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
    )


_hermes_service = _build_hermes_service()


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


@app.post("/v1/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
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
                workspace_id=_active_workspace_id,
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
            workspace_id=_active_workspace_id,
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


@app.get("/v1/profiles")
async def list_profiles() -> list[dict]:
    profiles = _profile_loader.list_all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
        }
        for p in profiles
    ]


@app.get("/v1/workspace")
async def get_workspace() -> dict:
    """Return the active workspace context for the UI bootstrap."""
    try:
        ctx = _workspace_engine.resolve(_active_workspace_id)
    except WorkspaceNotFoundError:
        return {
            "workspace": {"id": "default", "name": "Hermes", "description": "", "mission": ""},
            "profiles": [],
            "sprint": None,
        }

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


@app.get("/v1/dashboard")
async def get_dashboard() -> dict:
    """Return a CEO-oriented workspace operating summary for the Today screen."""
    return _hermes_service.get_dashboard(_active_workspace_id)


@app.get("/v1/knowledge")
async def list_knowledge() -> list[dict]:
    """List all Knowledge Documents for the active workspace."""
    return _hermes_service.list_knowledge(_active_workspace_id)


@app.get("/v1/knowledge/{document_id}")
async def get_knowledge(document_id: str) -> JSONResponse:
    """Return a single Knowledge Document with full content."""
    doc = _hermes_service.get_knowledge(_active_workspace_id, document_id)
    if doc is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Knowledge document not found: {document_id}"},
        )
    return JSONResponse(content=doc)


@app.get("/v1/operations")
async def list_operations() -> list[dict]:
    """List all Operations for the active workspace."""
    return _hermes_service.list_operations(_active_workspace_id)


@app.post("/v1/operations")
async def create_operation(body: CreateOperationRequest) -> JSONResponse:
    """Create an Operation from a conversation promotion."""
    result = _hermes_service.create_operation_from_chat(
        _active_workspace_id, body.request
    )
    return JSONResponse(status_code=201, content=result)


@app.get("/v1/operations/{operation_id}")
async def get_operation(operation_id: str) -> JSONResponse:
    """Return a single Operation."""
    op = _hermes_service.get_operation(_active_workspace_id, operation_id)
    if op is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Operation not found: {operation_id}"},
        )
    return JSONResponse(content=op)


@app.post("/v1/operations/{operation_id}/approve")
async def approve_operation(operation_id: str) -> JSONResponse:
    """Approve an escalated Operation, returning it to executing."""
    try:
        result = _hermes_service.approve_operation(_active_workspace_id, operation_id)
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


@app.post("/v1/operations/{operation_id}/reject")
async def reject_operation(operation_id: str) -> JSONResponse:
    """Reject an escalated Operation."""
    try:
        result = _hermes_service.reject_operation(_active_workspace_id, operation_id)
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


@app.get("/v1/jobs")
async def list_jobs() -> list[dict]:
    """List all Jobs for the active workspace."""
    return _hermes_service.list_jobs(_active_workspace_id)


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    """Return a single Job with full output."""
    job = _hermes_service.get_job(_active_workspace_id, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Job not found: {job_id}"},
        )
    return JSONResponse(content=job)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# -- Static UI (must be mounted last so API routes take precedence) ---------

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
