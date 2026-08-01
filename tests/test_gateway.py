import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from hermes.gateway.app import app

client = TestClient(app)


def _mock_hermes_service(tokens: list[str]):
    """Return a mock HermesService whose stream_chat yields the given tokens."""
    service = MagicMock()
    service.stream_chat.return_value = iter(tokens)
    service.chat.return_value = "".join(tokens)
    return service


# -- Health endpoint -------------------------------------------------------


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# -- Streaming chat --------------------------------------------------------


def test_chat_streams_sse_tokens():
    tokens = ["Hello", " world", "!"]
    service = _mock_hermes_service(tokens)

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    lines = resp.text.strip().split("\n")
    data_lines = [l for l in lines if l.startswith("data: ")]
    assert len(data_lines) == 4  # 3 tokens + [DONE]

    assert json.loads(data_lines[0].removeprefix("data: ")) == {"content": "Hello"}
    assert json.loads(data_lines[1].removeprefix("data: ")) == {"content": " world"}
    assert json.loads(data_lines[2].removeprefix("data: ")) == {"content": "!"}
    assert data_lines[3] == "data: [DONE]"


def test_chat_passes_messages_to_service():
    service = _mock_hermes_service(["OK"])

    messages = [
        {"role": "user", "content": "Hello"},
    ]

    with patch("hermes.gateway.app._hermes_service", service):
        client.post("/v1/chat", json={"messages": messages})

    service.stream_chat.assert_called_once()
    call_messages = service.stream_chat.call_args[0][0]
    assert len(call_messages) == 1
    assert call_messages[0].role == "user"
    assert call_messages[0].content == "Hello"


def test_chat_passes_profile_to_service():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._hermes_service", service):
        client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "profile": "developer",
            },
        )

    service.stream_chat.assert_called_once()
    assert service.stream_chat.call_args[1]["profile_id"] == "developer"


def test_chat_passes_none_profile_when_omitted():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._hermes_service", service):
        client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert service.stream_chat.call_args[1]["profile_id"] is None


def test_chat_with_model_override_creates_new_service():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._build_hermes_service", return_value=service) as mock_build:
        client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "model": "mistral",
            },
        )

    mock_build.assert_called_once_with("mistral")


def test_chat_defaults_to_streaming():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert resp.headers["content-type"].startswith("text/event-stream")
    service.stream_chat.assert_called_once()


# -- Non-streaming fallback ------------------------------------------------


def test_chat_non_streaming_returns_json():
    service = _mock_hermes_service(["Full response"])

    with patch("hermes.gateway.app._hermes_service", service):
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
    service.chat.assert_called_once()


def test_chat_non_streaming_passes_profile():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._hermes_service", service):
        client.post(
            "/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
                "profile": "business",
            },
        )

    assert service.chat.call_args[1]["profile_id"] == "business"


# -- Profiles endpoint -----------------------------------------------------


def test_list_profiles_returns_all():
    resp = client.get("/v1/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    ids = [p["id"] for p in profiles]
    assert "default" in ids
    assert "developer" in ids
    assert "business" in ids


def test_list_profiles_contains_expected_fields():
    resp = client.get("/v1/profiles")
    profiles = resp.json()
    for p in profiles:
        assert "id" in p
        assert "name" in p
        assert "description" in p
        # system_prompt should NOT be exposed
        assert "system_prompt" not in p


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


# -- Static UI serving -----------------------------------------------------


def test_root_serves_chat_ui():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Hermes" in resp.text
    assert "/v1/chat" in resp.text


def test_ui_contains_profile_selector():
    resp = client.get("/")
    assert "profile-select" in resp.text
    assert "/v1/profiles" in resp.text
