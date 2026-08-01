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


# -- Knowledge API ---------------------------------------------------------


def test_knowledge_list_returns_documents():
    resp = client.get("/v1/knowledge")
    assert resp.status_code == 200
    docs = resp.json()
    assert isinstance(docs, list)
    for doc in docs:
        assert "id" in doc
        assert "title" in doc
        assert "size" in doc
        assert "path" in doc


def test_knowledge_list_contains_expected_count():
    resp = client.get("/v1/knowledge")
    docs = resp.json()
    assert len(docs) == 12


def test_knowledge_list_documents_have_positive_size():
    resp = client.get("/v1/knowledge")
    for doc in resp.json():
        assert doc["size"] > 0


def test_knowledge_list_preserves_manifest_order():
    resp = client.get("/v1/knowledge")
    docs = resp.json()
    assert docs[0]["id"] == "01-purpose"
    assert docs[-1]["id"] == "12-homepage-tech-spec"


def test_knowledge_detail_returns_full_document():
    resp = client.get("/v1/knowledge/01-purpose")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["id"] == "01-purpose"
    assert doc["title"] != ""
    assert doc["size"] > 0
    assert doc["path"] != ""
    assert "content" in doc
    assert len(doc["content"]) > 0


def test_knowledge_detail_content_matches_title():
    resp = client.get("/v1/knowledge/01-purpose")
    doc = resp.json()
    assert doc["content"].startswith(f"# {doc['title']}")


def test_knowledge_detail_invalid_id_returns_404():
    resp = client.get("/v1/knowledge/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "nonexistent" in body["error"]


def test_knowledge_list_does_not_expose_content():
    resp = client.get("/v1/knowledge")
    for doc in resp.json():
        assert "content" not in doc


def test_knowledge_list_with_unknown_workspace_returns_empty():
    service = MagicMock()
    service.list_knowledge.return_value = []

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/knowledge")

    assert resp.status_code == 200
    assert resp.json() == []


# -- Dashboard API ---------------------------------------------------------


def test_dashboard_returns_workspace_identity():
    resp = client.get("/v1/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "workspace" in data
    assert "name" in data["workspace"]
    assert "mission" in data["workspace"]


def test_dashboard_contains_attention_section():
    resp = client.get("/v1/dashboard")
    data = resp.json()
    assert "attention" in data
    assert "count" in data["attention"]
    assert "items" in data["attention"]
    assert isinstance(data["attention"]["items"], list)


def test_dashboard_contains_operations_section():
    resp = client.get("/v1/dashboard")
    data = resp.json()
    assert "operations" in data
    ops = data["operations"]
    assert "active" in ops
    assert "completed_today" in ops
    assert "total" in ops


def test_dashboard_contains_knowledge_count():
    resp = client.get("/v1/dashboard")
    data = resp.json()
    assert "knowledge" in data
    assert "count" in data["knowledge"]
    assert data["knowledge"]["count"] > 0


def test_dashboard_contains_repository_count():
    resp = client.get("/v1/dashboard")
    data = resp.json()
    assert "repositories" in data
    assert "count" in data["repositories"]


def test_dashboard_knowledge_matches_knowledge_api():
    """Dashboard knowledge count must match the Knowledge API dynamically."""
    dashboard_resp = client.get("/v1/dashboard")
    knowledge_resp = client.get("/v1/knowledge")
    assert dashboard_resp.json()["knowledge"]["count"] == len(knowledge_resp.json())


def test_dashboard_delegates_to_service():
    service = MagicMock()
    service.get_dashboard.return_value = {
        "attention": {"count": 0, "items": []},
        "operations": {"active": 0, "completed_today": 0, "total": 0},
        "knowledge": {"count": 5},
        "repositories": {"count": 2},
        "workspace": {"name": "Test", "mission": ""},
    }

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/dashboard")

    assert resp.status_code == 200
    service.get_dashboard.assert_called_once()
    assert resp.json()["knowledge"]["count"] == 5


# -- UI: Today screen ------------------------------------------------------


def test_ui_contains_today_nav_item():
    resp = client.get("/")
    assert 'data-view="today"' in resp.text
    assert "Today" in resp.text


def test_ui_references_dashboard_endpoint():
    resp = client.get("/")
    assert "/v1/dashboard" in resp.text


# -- UI: Documents screen --------------------------------------------------


def test_ui_contains_documents_nav_item():
    resp = client.get("/")
    assert 'data-view="documents"' in resp.text
    assert "Documents" in resp.text


def test_ui_documents_view_references_knowledge_api():
    resp = client.get("/")
    assert "/v1/knowledge" in resp.text


def test_ui_documents_view_has_list_container():
    resp = client.get("/")
    assert 'id="documents-list"' in resp.text


def test_ui_documents_view_has_detail_container():
    resp = client.get("/")
    assert 'id="documents-detail"' in resp.text


def test_ui_documents_view_has_ask_about_action():
    resp = client.get("/")
    assert "Ask about this" in resp.text


def test_ui_documents_view_has_back_button():
    resp = client.get("/")
    assert "doc-back" in resp.text


# -- Operations API --------------------------------------------------------


def _mock_operation():
    return {
        "id": "OP-20260801-001",
        "workspace_id": "AVANZIA",
        "request": "Generate homepage copy",
        "status": "executing",
        "created_at": "2026-08-01T10:30:00+00:00",
        "updated_at": "2026-08-01T10:35:00+00:00",
    }


def test_operations_list_returns_200():
    service = MagicMock()
    service.list_operations.return_value = []

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/operations")

    assert resp.status_code == 200
    assert resp.json() == []


def test_operations_list_delegates_to_service():
    service = MagicMock()
    service.list_operations.return_value = [_mock_operation()]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/operations")

    service.list_operations.assert_called_once()
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == "OP-20260801-001"


def test_operations_list_deterministic_order():
    service = MagicMock()
    op1 = _mock_operation()
    op2 = {**_mock_operation(), "id": "OP-20260801-002"}
    service.list_operations.return_value = [op1, op2]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/operations")

    ids = [o["id"] for o in resp.json()]
    assert ids == ["OP-20260801-001", "OP-20260801-002"]


def test_operations_detail_returns_operation():
    service = MagicMock()
    service.get_operation.return_value = _mock_operation()

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/operations/OP-20260801-001")

    assert resp.status_code == 200
    assert resp.json()["id"] == "OP-20260801-001"


def test_operations_detail_not_found_returns_404():
    service = MagicMock()
    service.get_operation.return_value = None

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/operations/OP-NONEXISTENT")

    assert resp.status_code == 404
    assert "error" in resp.json()


def test_operations_detail_excludes_extra_fields():
    service = MagicMock()
    service.get_operation.return_value = _mock_operation()

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/operations/OP-20260801-001")

    assert "extra_fields" not in resp.json()


def test_operations_approve_delegates_to_service():
    service = MagicMock()
    approved = {**_mock_operation(), "status": "executing"}
    service.approve_operation.return_value = approved

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post("/v1/operations/OP-20260801-001/approve")

    assert resp.status_code == 200
    service.approve_operation.assert_called_once()
    assert resp.json()["status"] == "executing"


def test_operations_approve_not_found_returns_404():
    from hermes.kernel.operation_store import OperationNotFoundError

    service = MagicMock()
    service.approve_operation.side_effect = OperationNotFoundError("not found")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post("/v1/operations/OP-NONEXISTENT/approve")

    assert resp.status_code == 404


def test_operations_approve_invalid_transition_returns_409():
    from hermes.models.operation import InvalidTransitionError

    service = MagicMock()
    service.approve_operation.side_effect = InvalidTransitionError("bad transition")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post("/v1/operations/OP-20260801-001/approve")

    assert resp.status_code == 409
    assert "error" in resp.json()


def test_operations_reject_delegates_to_service():
    service = MagicMock()
    rejected = {**_mock_operation(), "status": "rejected"}
    service.reject_operation.return_value = rejected

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post("/v1/operations/OP-20260801-001/reject")

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_operations_reject_not_found_returns_404():
    from hermes.kernel.operation_store import OperationNotFoundError

    service = MagicMock()
    service.reject_operation.side_effect = OperationNotFoundError("not found")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post("/v1/operations/OP-NONEXISTENT/reject")

    assert resp.status_code == 404


def test_operations_reject_invalid_transition_returns_409():
    from hermes.models.operation import InvalidTransitionError

    service = MagicMock()
    service.reject_operation.side_effect = InvalidTransitionError("bad transition")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post("/v1/operations/OP-20260801-001/reject")

    assert resp.status_code == 409


# -- Jobs API --------------------------------------------------------------


def _mock_job(include_output=False):
    data = {
        "id": "JOB-20260801-001",
        "workspace_id": "AVANZIA",
        "operation_id": "OP-20260801-001",
        "status": "completed",
        "completed_steps": ["Python"],
        "started_at": "2026-08-01T10:30:00+00:00",
        "finished_at": "2026-08-01T10:30:05+00:00",
        "created_at": "2026-08-01T10:30:05+00:00",
        "updated_at": "2026-08-01T10:30:05+00:00",
    }
    if include_output:
        data["generated_output"] = "Generated text"
    return data


def test_jobs_list_returns_200():
    service = MagicMock()
    service.list_jobs.return_value = []

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs")

    assert resp.status_code == 200
    assert resp.json() == []


def test_jobs_list_delegates_to_service():
    service = MagicMock()
    service.list_jobs.return_value = [_mock_job()]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs")

    service.list_jobs.assert_called_once()
    assert len(resp.json()) == 1


def test_jobs_list_excludes_generated_output():
    service = MagicMock()
    service.list_jobs.return_value = [_mock_job(include_output=False)]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs")

    assert "generated_output" not in resp.json()[0]


def test_jobs_list_deterministic_order():
    service = MagicMock()
    j1 = _mock_job()
    j2 = {**_mock_job(), "id": "JOB-20260801-002"}
    service.list_jobs.return_value = [j1, j2]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs")

    ids = [j["id"] for j in resp.json()]
    assert ids == ["JOB-20260801-001", "JOB-20260801-002"]


def test_jobs_detail_returns_job():
    service = MagicMock()
    service.get_job.return_value = _mock_job(include_output=True)

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs/JOB-20260801-001")

    assert resp.status_code == 200
    assert resp.json()["id"] == "JOB-20260801-001"


def test_jobs_detail_includes_generated_output():
    service = MagicMock()
    service.get_job.return_value = _mock_job(include_output=True)

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs/JOB-20260801-001")

    assert "generated_output" in resp.json()


def test_jobs_detail_not_found_returns_404():
    service = MagicMock()
    service.get_job.return_value = None

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get("/v1/jobs/JOB-NONEXISTENT")

    assert resp.status_code == 404
    assert "error" in resp.json()


# -- UI: Operations screen -------------------------------------------------


def test_ui_contains_operations_nav_item():
    resp = client.get("/")
    assert 'data-view="operations"' in resp.text
    assert "Operations" in resp.text


def test_ui_operations_view_references_operations_api():
    resp = client.get("/")
    assert "/v1/operations" in resp.text


def test_ui_operations_view_has_list_container():
    resp = client.get("/")
    assert 'id="operations-list"' in resp.text


def test_ui_operations_view_has_detail_container():
    resp = client.get("/")
    assert 'id="operations-detail"' in resp.text


def test_ui_operations_view_has_filter_bar():
    resp = client.get("/")
    assert "filter-bar" in resp.text
    assert "filter-btn" in resp.text


def test_ui_operations_view_has_status_badges():
    resp = client.get("/")
    assert "status-badge" in resp.text


def test_ui_operations_view_has_approve_button_logic():
    resp = client.get("/")
    assert "op-approve" in resp.text
    assert "approveOperation" in resp.text


def test_ui_operations_view_has_reject_button_logic():
    resp = client.get("/")
    assert "op-reject" in resp.text
    assert "rejectOperation" in resp.text


def test_ui_operations_view_has_back_button():
    resp = client.get("/")
    assert "op-back" in resp.text


def test_ui_operations_view_has_jobs_section():
    resp = client.get("/")
    assert "op-jobs-list" in resp.text
    assert "/v1/jobs" in resp.text


def test_ui_operations_view_has_decisions_placeholder():
    resp = client.get("/")
    assert "No Decisions recorded." in resp.text


def test_ui_operations_view_has_empty_state():
    resp = client.get("/")
    assert "No Operations yet." in resp.text


def test_ui_operations_view_has_no_jobs_empty_state():
    resp = client.get("/")
    assert "No Jobs for this Operation." in resp.text


def test_ui_operations_view_shows_approve_only_for_escalation():
    """Approve/Reject buttons only render when status is awaiting_escalation."""
    resp = client.get("/")
    assert 'op.status === "awaiting_escalation"' in resp.text


def test_ui_operations_view_sorts_by_updated_at():
    """Operations are sorted by updated_at descending in the UI."""
    resp = client.get("/")
    assert "b.updated_at.localeCompare(a.updated_at)" in resp.text


# -- UI: Jobs screen -------------------------------------------------------


def test_ui_contains_jobs_nav_item():
    resp = client.get("/")
    assert 'data-view="jobs"' in resp.text
    assert "Jobs" in resp.text


def test_ui_jobs_view_references_jobs_api():
    resp = client.get("/")
    assert "/v1/jobs" in resp.text


def test_ui_jobs_view_has_list_container():
    resp = client.get("/")
    assert 'id="jobs-list"' in resp.text


def test_ui_jobs_view_has_detail_container():
    resp = client.get("/")
    assert 'id="jobs-detail"' in resp.text


def test_ui_jobs_view_has_status_filter():
    resp = client.get("/")
    assert "data-job-status" in resp.text


def test_ui_jobs_view_has_operation_filter():
    resp = client.get("/")
    assert "jobs-op-select" in resp.text


def test_ui_jobs_view_has_back_button():
    resp = client.get("/")
    assert "job-back" in resp.text


def test_ui_jobs_view_has_operation_link():
    resp = client.get("/")
    assert "navigateToOperation" in resp.text
    assert "job-view-op" in resp.text


def test_ui_jobs_view_has_output_section():
    resp = client.get("/")
    assert "Generated Output" in resp.text
    assert "completed without generated output" in resp.text


def test_ui_jobs_view_has_diagnostics_section():
    resp = client.get("/")
    assert "job-diag-toggle" in resp.text
    assert "job-diag-content" in resp.text


def test_ui_jobs_view_has_empty_state():
    resp = client.get("/")
    assert "No Jobs have been executed yet." in resp.text


def test_ui_jobs_view_has_duration():
    resp = client.get("/")
    assert "formatDuration" in resp.text


def test_ui_jobs_view_sorts_by_most_recent():
    """Jobs are sorted by finished_at descending in the UI."""
    resp = client.get("/")
    assert "b.finished_at" in resp.text


def test_ui_jobs_view_has_no_diagnostics_fallback():
    """When diagnostics data is absent, show 'No diagnostics recorded.'"""
    resp = client.get("/")
    assert "No diagnostics recorded." in resp.text


def test_ui_jobs_view_handles_parent_op_not_found():
    """If parent Operation cannot be loaded, stay on Job detail with error."""
    resp = client.get("/")
    assert "The parent Operation could not be loaded." in resp.text


def test_ui_jobs_view_duration_handles_missing_timestamps():
    """formatDuration handles incomplete Jobs per Founder amendment 3."""
    resp = client.get("/")
    # Verify the function handles missing timestamps
    assert 'if (!start) return "Unknown"' in resp.text
    assert 'if (!end) return "Running..."' in resp.text
