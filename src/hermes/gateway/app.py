"""Hermes Gateway — lightweight API that bridges the chat UI to the LLM runtime.

Exposes a single SSE endpoint (POST /v1/chat) that streams tokens from the
Ollama provider back to the browser.
"""

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hermes.providers.ollama_provider import (
    ChatMessage,
    OllamaConnectionError,
    OllamaProvider,
)

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
    stream: bool = True


# -- Provider factory -------------------------------------------------------


def _build_provider(model: str | None = None) -> OllamaProvider:
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = model or os.environ.get("OLLAMA_MODEL", "llama3.2")
    return OllamaProvider(model=ollama_model, base_url=ollama_url)


# -- SSE helpers ------------------------------------------------------------


def _sse_stream(tokens: Iterator[str]) -> Iterator[str]:
    """Wrap token chunks as SSE ``data:`` frames."""
    for token in tokens:
        payload = json.dumps({"content": token})
        yield f"data: {payload}\n\n"
    yield "data: [DONE]\n\n"


# -- Endpoints --------------------------------------------------------------


@app.post("/v1/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    provider = _build_provider(request.model)

    messages = [
        ChatMessage(role=m["role"], content=m["content"])
        for m in request.messages
    ]

    if request.stream:
        tokens = provider.stream_chat(messages)
        return StreamingResponse(
            _sse_stream(tokens),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming fallback: return full response as JSON.
    result = provider.chat(messages)
    return StreamingResponse(
        iter([json.dumps({"content": result})]),
        media_type="application/json",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# -- Static UI (must be mounted last so API routes take precedence) ---------

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="ui")
