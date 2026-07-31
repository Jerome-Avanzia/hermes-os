"""Hermes Gateway — lightweight API that bridges the chat UI to the LLM runtime.

Exposes SSE chat (POST /v1/chat), profile listing (GET /v1/profiles),
health check (GET /health), and the static chat UI (GET /).

All chat requests are routed through the Conductor, which resolves the
active profile and prepends the system prompt before delegating to the
LLM provider.
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
from hermes.kernel.profile_loader import ProfileLoader
from hermes.kernel.workspace_engine import WorkspaceEngine, WorkspaceNotFoundError
from hermes.providers.ollama_provider import ChatMessage, OllamaConnectionError, OllamaProvider

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


# -- Singleton Conductor ----------------------------------------------------


def _build_provider(model: str | None = None) -> OllamaProvider:
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = model or os.environ.get("OLLAMA_MODEL", "llama3.2")
    return OllamaProvider(model=ollama_model, base_url=ollama_url)


def _build_conductor(model: str | None = None) -> Conductor:
    return Conductor(
        provider=_build_provider(model),
        profile_loader=ProfileLoader(),
    )


_conductor = _build_conductor()

_workspace_engine = WorkspaceEngine()
_active_workspace_id = os.environ.get("HERMES_WORKSPACE", "AVANZIA")


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
    conductor = _conductor
    if request.model:
        conductor = _build_conductor(request.model)

    messages = [
        ChatMessage(role=m["role"], content=m["content"])
        for m in request.messages
    ]

    try:
        if request.stream:
            tokens = conductor.stream_chat(messages, profile_id=request.profile)
            return StreamingResponse(
                _sse_stream(tokens),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        result = conductor.chat(messages, profile_id=request.profile)
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
    profiles = _conductor.profile_loader.list_all()
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
    default_profile = _conductor.profile_loader.get_default()

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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# -- Static UI (must be mounted last so API routes take precedence) ---------

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
