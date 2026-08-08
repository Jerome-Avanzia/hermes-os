"""Ollama AI provider.

Calls a local (or remote) Ollama instance via its HTTP API.
Supports both synchronous generation (kernel ``AIProvider`` contract)
and async streaming for the conversational chat pipeline.
"""

import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from hermes.models import Context, ExecutionPlan, FileContent, LoadedSkill, Task, WorkspaceSnapshot
from hermes.providers.ai_provider import AIProvider

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama3.2"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 120.0


class OllamaConnectionError(Exception):
    pass


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class OllamaProvider(AIProvider):
    """Provider that talks to Ollama's ``/api/chat`` and ``/api/generate`` endpoints."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str = "",
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key

    # -- AIProvider contract (kernel integration) ----------------------------

    def generate(
        self,
        *,
        task: Task,
        context: Context,
        plan: ExecutionPlan,
        skills: list[LoadedSkill],
        workspace: WorkspaceSnapshot,
        file_contents: list[FileContent] | None = None,
    ) -> str:
        prompt = self._build_kernel_prompt(task, context, plan, skills)
        return self._generate_sync(prompt)

    # -- Conversational chat (streaming) ------------------------------------

    def stream_chat(
        self,
        messages: list[ChatMessage],
        **options: Any,
    ) -> Iterator[str]:
        """Stream a chat completion token-by-token.

        Yields content strings as they arrive from Ollama.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if options:
            payload["options"] = options

        url = f"{self._base_url}/api/chat"
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        # -- Temporary instrumentation: log exact request and response --------
        masked_key = ("*" * (len(self._api_key) - 4) + self._api_key[-4:]) if len(self._api_key) > 4 else ("*" * len(self._api_key) if self._api_key else "none")
        safe_payload = {k: v for k, v in payload.items() if k != "messages"}
        safe_payload["messages_count"] = len(payload.get("messages", []))
        logger.warning(
            "DEBUG REQUEST\n"
            "  method:            POST\n"
            "  url:               %s\n"
            "  model (field):     %r\n"
            "  payload.model:     %r\n"
            "  Authorization:     %s\n"
            "  body (no msgs):    %s",
            url,
            self._model,
            payload.get("model"),
            f"Bearer {masked_key}" if self._api_key else "not set",
            safe_payload,
        )
        # -- End instrumentation ----------------------------------------------

        try:
            with httpx.Client(timeout=self._timeout) as client:
                with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code >= 400:
                        resp_body = response.read().decode(errors="replace")
                        logger.warning(
                            "DEBUG RESPONSE\n"
                            "  status:  %d\n"
                            "  body:    %s",
                            response.status_code, resp_body[:1000],
                        )
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if chunk.get("done", False):
                            return
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is Ollama running?"
            ) from exc

    def chat(self, messages: list[ChatMessage], **options: Any) -> str:
        """Non-streaming chat completion. Returns the full response."""
        return "".join(self.stream_chat(messages, **options))

    # -- Internal helpers ---------------------------------------------------

    def _generate_sync(self, prompt: str) -> str:
        """Call /api/generate (non-streaming) and return the full response."""
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }
        url = f"{self._base_url}/api/generate"
        logger.info("Generating via %s model=%s", url, self._model)

        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json().get("response", "")
        except httpx.ConnectError as exc:
            raise OllamaConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is Ollama running?"
            ) from exc

    @staticmethod
    def from_env(model: str | None = None) -> "OllamaProvider":
        """Build an OllamaProvider from environment variables.

        Reads OLLAMA_MODE to select the correct endpoint:
          - OLLAMA_MODE=local (default): OLLAMA_LOCAL_URL (default: http://localhost:11434)
          - OLLAMA_MODE=cloud:           OLLAMA_CLOUD_URL (default: https://ollama.com)

        Model: *model* argument takes precedence, then OLLAMA_MODEL, then DEFAULT_MODEL.
        """
        mode = os.environ.get("OLLAMA_MODE", "local").strip().lower()
        if mode == "cloud":
            base_url = os.environ.get("OLLAMA_CLOUD_URL", "https://ollama.com").rstrip("/")
        else:
            base_url = os.environ.get("OLLAMA_LOCAL_URL", DEFAULT_BASE_URL).rstrip("/")
        resolved_model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        api_key = os.environ.get("OLLAMA_API_KEY", "")
        return OllamaProvider(model=resolved_model, base_url=base_url, api_key=api_key)

    @staticmethod
    def _build_kernel_prompt(
        task: Task,
        context: Context,
        plan: ExecutionPlan,
        skills: list[LoadedSkill],
    ) -> str:
        knowledge_docs = context.knowledge.documents[:3]
        knowledge_section = (
            "\n\n".join(f"## {doc.title}\n\n{doc.content}" for doc in knowledge_docs)
            or "-"
        )
        skill_summary = "\n".join(f"- {s.name}" for s in skills) or "-"
        step_summary = "\n".join(f"- {s.description}" for s in plan.steps) or "-"

        return (
            f"Project: {context.project.name}\n\n"
            f"Knowledge:\n{knowledge_section}\n\n"
            f"Skills:\n{skill_summary}\n\n"
            f"Plan:\n{step_summary}\n\n"
            f"Request:\n{task.request}\n\n"
            "Complete this task using the context above."
        )
