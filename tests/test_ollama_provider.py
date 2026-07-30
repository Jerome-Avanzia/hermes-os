import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.models import (
    Context,
    KnowledgeContext,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
    WorkspaceSnapshot,
)
from hermes.providers.ollama_provider import (
    ChatMessage,
    OllamaConnectionError,
    OllamaProvider,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


def _build_context(request: str) -> Context:
    task = Task(id="t", business="AVANZIA", request=request)
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    knowledge = KnowledgeContext(project=project, documents=[])
    workspace = WorkspaceContext(
        workspace=Workspace(project_id="AVANZIA", path="/tmp/avanzia"),
        exists=True,
        is_git_repo=True,
        branch="main",
        is_clean=True,
        environment=[],
    )
    capabilities = CapabilityEngine(skills_root=SKILLS_ROOT).match(task)
    return Context(
        task=task,
        project=project,
        knowledge=knowledge,
        workspace=workspace,
        capabilities=capabilities,
    )


# -- Construction ----------------------------------------------------------


def test_default_model():
    provider = OllamaProvider()
    assert provider._model == "llama3.2"


def test_custom_model():
    provider = OllamaProvider(model="mistral")
    assert provider._model == "mistral"


def test_custom_base_url_strips_trailing_slash():
    provider = OllamaProvider(base_url="http://ollama:11434/")
    assert provider._base_url == "http://ollama:11434"


# -- Kernel generate (sync, non-streaming) ---------------------------------


def test_generate_calls_ollama_api(monkeypatch):
    provider = OllamaProvider()
    context = _build_context("Refactor backend")
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)
    workspace = WorkspaceSnapshot(root="/tmp/avanzia", files=[])

    mock_response = MagicMock()
    mock_response.json.return_value = {"response": "Here is my proposal."}
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_response

    with patch("hermes.providers.ollama_provider.httpx.Client", return_value=mock_client):
        result = provider.generate(
            task=context.task,
            context=context,
            plan=plan,
            skills=skills,
            workspace=workspace,
        )

    assert result == "Here is my proposal."
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert "/api/generate" in call_args[0][0]
    payload = call_args[1]["json"]
    assert payload["model"] == "llama3.2"
    assert payload["stream"] is False
    assert "Refactor backend" in payload["prompt"]


# -- Chat (non-streaming convenience) --------------------------------------


def test_chat_returns_full_response():
    provider = OllamaProvider()
    messages = [ChatMessage(role="user", content="Hello")]

    chunks = [
        json.dumps({"message": {"content": "Hi"}, "done": False}),
        json.dumps({"message": {"content": " there!"}, "done": True}),
    ]

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = iter(chunks)
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream.return_value = mock_response

    with patch("hermes.providers.ollama_provider.httpx.Client", return_value=mock_client):
        result = provider.chat(messages)

    assert result == "Hi there!"


# -- Streaming chat --------------------------------------------------------


def test_stream_chat_yields_tokens():
    provider = OllamaProvider()
    messages = [ChatMessage(role="user", content="Tell me a joke")]

    chunks = [
        json.dumps({"message": {"content": "Why"}, "done": False}),
        json.dumps({"message": {"content": " did"}, "done": False}),
        json.dumps({"message": {"content": " the chicken"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = iter(chunks)
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream.return_value = mock_response

    with patch("hermes.providers.ollama_provider.httpx.Client", return_value=mock_client):
        tokens = list(provider.stream_chat(messages))

    assert tokens == ["Why", " did", " the chicken"]


def test_stream_chat_sends_correct_payload():
    provider = OllamaProvider(model="mistral", base_url="http://ollama:11434")
    messages = [
        ChatMessage(role="system", content="You are helpful."),
        ChatMessage(role="user", content="Hi"),
    ]

    chunks = [json.dumps({"message": {"content": "Hello"}, "done": True})]

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = iter(chunks)
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream.return_value = mock_response

    with patch("hermes.providers.ollama_provider.httpx.Client", return_value=mock_client):
        list(provider.stream_chat(messages, temperature=0.7))

    mock_client.stream.assert_called_once()
    call_args = mock_client.stream.call_args
    assert call_args[0] == ("POST", "http://ollama:11434/api/chat")
    payload = call_args[1]["json"]
    assert payload["model"] == "mistral"
    assert payload["stream"] is True
    assert len(payload["messages"]) == 2
    assert payload["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert payload["messages"][1] == {"role": "user", "content": "Hi"}
    assert payload["options"] == {"temperature": 0.7}


# -- Connection error handling ---------------------------------------------


def test_stream_chat_raises_on_connection_error():
    provider = OllamaProvider(base_url="http://localhost:99999")

    import httpx

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.stream.side_effect = httpx.ConnectError("Connection refused")

    with patch("hermes.providers.ollama_provider.httpx.Client", return_value=mock_client):
        with pytest.raises(OllamaConnectionError, match="Cannot connect to Ollama"):
            list(provider.stream_chat([ChatMessage(role="user", content="hi")]))


def test_generate_raises_on_connection_error():
    provider = OllamaProvider()
    context = _build_context("Test")
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)
    workspace = WorkspaceSnapshot(root="/tmp", files=[])

    import httpx

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")

    with patch("hermes.providers.ollama_provider.httpx.Client", return_value=mock_client):
        with pytest.raises(OllamaConnectionError, match="Cannot connect to Ollama"):
            provider.generate(
                task=context.task,
                context=context,
                plan=plan,
                skills=skills,
                workspace=workspace,
            )


# -- Kernel prompt ---------------------------------------------------------


def test_kernel_prompt_contains_task_request():
    context = _build_context("Deploy the new API")
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)

    prompt = OllamaProvider._build_kernel_prompt(
        context.task, context, plan, skills
    )

    assert "Deploy the new API" in prompt
    assert "AVANZIA" in prompt
