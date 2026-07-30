import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from hermes.gateway.app import app

client = TestClient(app)


def _mock_provider(tokens: list[str]):
    """Return a mock OllamaProvider whose stream_chat yields the given tokens."""
    provider = MagicMock()
    provider.stream_chat.return_value = iter(tokens)
    provider.chat.return_value = "".join(tokens)
    return provider


# -- Health endpoint -------------------------------------------------------


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- Streaming chat --------------------------------------------------------


def test_chat_streams_sse_tokens():
    tokens = ["Hello", " world", "!"]
    provider = _mock_provider(tokens)

    with patch("hermes.gateway.app._build_provider", return_value=provider):
        resp = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    lines = resp.text.strip().split("\n")
    data_lines = [l for l in lines if l.startswith("data: ")]
    assert len(data_lines) == 4  # 3 tokens + [DONE]

    # Verify token payloads
    assert json.loads(data_lines[0].removeprefix("data: ")) == {"content": "Hello"}
    assert json.loads(data_lines[1].removeprefix("data: ")) == {"content": " world"}
    assert json.loads(data_lines[2].removeprefix("data: ")) == {"content": "!"}
    assert data_lines[3] == "data: [DONE]"


def test_chat_passes_messages_to_provider():
    provider = _mock_provider(["OK"])

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]

    with patch("hermes.gateway.app._build_provider", return_value=provider):
        client.post("/v1/chat", json={"messages": messages})

    provider.stream_chat.assert_called_once()
    call_messages = provider.stream_chat.call_args[0][0]
    assert len(call_messages) == 2
    assert call_messages[0].role == "system"
    assert call_messages[0].content == "You are helpful."
    assert call_messages[1].role == "user"
    assert call_messages[1].content == "Hello"


def test_chat_uses_requested_model():
    provider = _mock_provider(["OK"])

    with patch("hermes.gateway.app._build_provider", return_value=provider) as mock_build:
        client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "model": "mistral",
            },
        )

    mock_build.assert_called_once_with("mistral")


def test_chat_defaults_to_streaming():
    provider = _mock_provider(["OK"])

    with patch("hermes.gateway.app._build_provider", return_value=provider):
        resp = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert resp.headers["content-type"].startswith("text/event-stream")
    provider.stream_chat.assert_called_once()


# -- Non-streaming fallback ------------------------------------------------


def test_chat_non_streaming_returns_json():
    provider = _mock_provider(["Full response"])

    with patch("hermes.gateway.app._build_provider", return_value=provider):
        resp = client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "Full response"
    provider.chat.assert_called_once()


# -- CORS ------------------------------------------------------------------


def test_cors_allows_origin():
    resp = client.options(
        "/v1/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") is not None


# -- Validation ------------------------------------------------------------


def test_chat_rejects_empty_body():
    resp = client.post("/v1/chat")
    assert resp.status_code == 422


def test_chat_rejects_missing_messages():
    resp = client.post("/v1/chat", json={"stream": True})
    assert resp.status_code == 422
