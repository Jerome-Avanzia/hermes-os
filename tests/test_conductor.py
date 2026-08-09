import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.conductor import Conductor
from hermes.kernel.profile_loader import ProfileLoader, ProfileNotFoundError
from hermes.models import (
    Context,
    KnowledgeContext,
    KnowledgeDocument,
    Organization,
    Profile,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
)
from hermes.providers.ollama_provider import ChatMessage, OllamaProvider


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "profiles"


def _make_provider(model: str = "llama3.2") -> OllamaProvider:
    return OllamaProvider(model=model, base_url="http://ollama:11434")


def _user_message(text: str = "Hello") -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


# -- Profile resolution ----------------------------------------------------


def test_uses_default_profile_when_none_specified():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    with patch.object(provider, "stream_chat", return_value=iter(["Hi"])) as mock:
        list(conductor.stream_chat(_user_message()))

    call_messages = mock.call_args[0][0]
    # Default profile system prompt should be prepended
    assert call_messages[0].role == "system"
    assert "professional assistant" in call_messages[0].content


def test_uses_specified_profile():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    with patch.object(provider, "stream_chat", return_value=iter(["Hi"])) as mock:
        list(conductor.stream_chat(_user_message(), profile_id="developer"))

    call_messages = mock.call_args[0][0]
    assert call_messages[0].role == "system"
    assert "technical" in call_messages[0].content.lower()


def test_unknown_profile_raises():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    with pytest.raises(ProfileNotFoundError, match="nonexistent"):
        list(conductor.stream_chat(_user_message(), profile_id="nonexistent"))


# -- System prompt prepending ----------------------------------------------


def test_prepends_system_prompt_to_messages():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    messages = [
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi there"),
        ChatMessage(role="user", content="How are you?"),
    ]

    with patch.object(provider, "stream_chat", return_value=iter(["Good"])) as mock:
        list(conductor.stream_chat(messages))

    call_messages = mock.call_args[0][0]
    assert len(call_messages) == 4  # system + 3 original
    assert call_messages[0].role == "system"
    assert call_messages[1].role == "user"
    assert call_messages[1].content == "Hello"


def test_replaces_existing_system_message():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    messages = [
        ChatMessage(role="system", content="Old system prompt"),
        ChatMessage(role="user", content="Hello"),
    ]

    with patch.object(provider, "stream_chat", return_value=iter(["Hi"])) as mock:
        list(conductor.stream_chat(messages))

    call_messages = mock.call_args[0][0]
    assert len(call_messages) == 2  # replaced, not doubled
    assert call_messages[0].role == "system"
    assert "Old system prompt" not in call_messages[0].content


def test_empty_system_prompt_does_not_prepend(tmp_path):
    (tmp_path / "empty.yaml").write_text(
        "id: empty\nname: Empty\nsystem_prompt: ''\n"
    )
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=tmp_path))

    messages = _user_message("Hi")

    with patch.object(provider, "stream_chat", return_value=iter(["OK"])) as mock:
        list(conductor.stream_chat(messages, profile_id="empty"))

    call_messages = mock.call_args[0][0]
    assert len(call_messages) == 1
    assert call_messages[0].role == "user"


# -- Model override --------------------------------------------------------


def test_profile_model_overrides_provider_model(tmp_path):
    (tmp_path / "code.yaml").write_text(textwrap.dedent("""\
        id: code
        name: Code
        system_prompt: You write code.
        model: codellama
    """))

    provider = _make_provider(model="llama3.2")
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=tmp_path))

    with patch("hermes.conductor.OllamaProvider") as MockProvider:
        mock_instance = MagicMock()
        mock_instance.stream_chat.return_value = iter(["code"])
        MockProvider.return_value = mock_instance

        list(conductor.stream_chat(_user_message(), profile_id="code"))

        MockProvider.assert_called_once_with(
            model="codellama",
            base_url="http://ollama:11434",
            timeout=120.0,
        )
        mock_instance.stream_chat.assert_called_once()


def test_profile_without_model_uses_existing_provider():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    with patch.object(provider, "stream_chat", return_value=iter(["Hi"])) as mock:
        list(conductor.stream_chat(_user_message()))

    mock.assert_called_once()


# -- Streaming passthrough -------------------------------------------------


def test_stream_chat_yields_tokens():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    with patch.object(provider, "stream_chat", return_value=iter(["Hello", " world"])):
        tokens = list(conductor.stream_chat(_user_message()))

    assert tokens == ["Hello", " world"]


def test_chat_returns_full_response():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    with patch.object(provider, "stream_chat", return_value=iter(["Hello", " world"])):
        result = conductor.chat(_user_message())

    assert result == "Hello world"


# -- Profile loader access -------------------------------------------------


def test_profile_loader_is_accessible():
    loader = ProfileLoader(profiles_dir=PROFILES_DIR)
    conductor = Conductor(_make_provider(), profile_loader=loader)
    assert conductor.profile_loader is loader


# -- Context-based rendering -----------------------------------------------


def _make_context(
    profile: Profile | None = None,
    organization: Organization | None = None,
    knowledge_docs: list[KnowledgeDocument] | None = None,
) -> Context:
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    ws = Workspace(
        project_id="AVANZIA",
        path="/tmp/avanzia",
        name="AVANZIA",
        organization=organization,
    )
    return Context(
        task=Task(id="conversation", business="AVANZIA", request=""),
        project=project,
        knowledge=KnowledgeContext(project=project, documents=knowledge_docs or []),
        workspace=WorkspaceContext(
            workspace=ws,
            exists=True,
            is_git_repo=False,
            branch=None,
            is_clean=None,
            environment=[],
        ),
        capabilities=[],
        profile=profile,
    )


def test_stream_chat_with_context_uses_profile_from_context():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    profile = Profile(id="test", name="Test", system_prompt="You are a test bot.")
    context = _make_context(profile=profile)

    with patch.object(provider, "stream_chat", return_value=iter(["Hi"])) as mock:
        list(conductor.stream_chat_with_context(_user_message(), context))

    call_messages = mock.call_args[0][0]
    assert call_messages[0].role == "system"
    assert "test bot" in call_messages[0].content


def test_stream_chat_with_context_includes_organization():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    org = Organization(
        name="AVANZIA",
        purpose="Build AI businesses.",
        mission="Design, build, operate.",
    )
    profile = Profile(id="default", name="Hermes", system_prompt="You are Hermes.")
    context = _make_context(profile=profile, organization=org)

    with patch.object(provider, "stream_chat", return_value=iter(["OK"])) as mock:
        list(conductor.stream_chat_with_context(_user_message(), context))

    system_content = mock.call_args[0][0][0].content
    assert "Build AI businesses" in system_content
    assert "Design, build, operate" in system_content


def test_stream_chat_with_context_includes_knowledge():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    docs = [
        KnowledgeDocument(
            id="purpose", title="Purpose", path="knowledge/AVANZIA/01-purpose.md",
            content="We exist to build AI-powered businesses.",
        ),
    ]
    profile = Profile(id="default", name="Hermes", system_prompt="You are Hermes.")
    context = _make_context(profile=profile, knowledge_docs=docs)

    with patch.object(provider, "stream_chat", return_value=iter(["OK"])) as mock:
        list(conductor.stream_chat_with_context(_user_message(), context))

    system_content = mock.call_args[0][0][0].content
    assert "AI-powered businesses" in system_content


def test_stream_chat_with_context_yields_tokens():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    profile = Profile(id="default", name="Hermes", system_prompt="You are Hermes.")
    context = _make_context(profile=profile)

    with patch.object(provider, "stream_chat", return_value=iter(["Hello", " world"])):
        tokens = list(conductor.stream_chat_with_context(_user_message(), context))

    assert tokens == ["Hello", " world"]


def test_stream_chat_with_context_includes_all_selected_docs():
    """All documents in context.knowledge are included — no silent truncation."""
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    docs = [
        KnowledgeDocument(
            id=f"doc-{i}", title=f"Document {i}", path=f"knowledge/AVANZIA/doc-{i}.md",
            content=f"Content of document {i}.",
        )
        for i in range(8)
    ]
    profile = Profile(id="default", name="Hermes", system_prompt="You are Hermes.")
    context = _make_context(profile=profile, knowledge_docs=docs)

    with patch.object(provider, "stream_chat", return_value=iter(["OK"])) as mock:
        list(conductor.stream_chat_with_context(_user_message(), context))

    system_content = mock.call_args[0][0][0].content
    for i in range(8):
        assert f"Document {i}" in system_content
        assert f"Content of document {i}" in system_content


def test_stream_chat_with_context_without_profile_uses_default():
    provider = _make_provider()
    conductor = Conductor(provider, profile_loader=ProfileLoader(profiles_dir=PROFILES_DIR))

    context = _make_context(profile=None)

    with patch.object(provider, "stream_chat", return_value=iter(["OK"])) as mock:
        list(conductor.stream_chat_with_context(_user_message(), context))

    call_messages = mock.call_args[0][0]
    assert call_messages[0].role == "system"
    assert "professional assistant" in call_messages[0].content
