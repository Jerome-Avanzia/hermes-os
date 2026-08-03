import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from hermes.gateway.app import app

client = TestClient(app)

WS = "AVANZIA"
WS_PREFIX = f"/v1/workspaces/{WS}"


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


# -- Workspace listing -----------------------------------------------------


def test_list_workspaces_returns_200():
    resp = client.get("/v1/workspaces")
    assert resp.status_code == 200
    workspaces = resp.json()
    assert isinstance(workspaces, list)
    assert len(workspaces) >= 1


def test_list_workspaces_contains_avanzia():
    resp = client.get("/v1/workspaces")
    ids = [w["id"] for w in resp.json()]
    assert "AVANZIA" in ids


def test_list_workspaces_contains_expected_fields():
    resp = client.get("/v1/workspaces")
    for ws in resp.json():
        assert "id" in ws
        assert "name" in ws
        assert "description" in ws


# -- Workspace not found ---------------------------------------------------


def test_workspace_not_found_returns_404():
    resp = client.get("/v1/workspaces/NONEXISTENT/dashboard")
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_workspace_not_found_on_operations():
    resp = client.get("/v1/workspaces/NONEXISTENT/operations")
    assert resp.status_code == 404


def test_workspace_not_found_on_knowledge():
    resp = client.get("/v1/workspaces/NONEXISTENT/knowledge")
    assert resp.status_code == 404


def test_workspace_not_found_on_jobs():
    resp = client.get("/v1/workspaces/NONEXISTENT/jobs")
    assert resp.status_code == 404


# -- Legacy routes removed -------------------------------------------------


def test_legacy_chat_returns_not_found():
    resp = client.post("/v1/chat", json={"messages": [{"role": "user", "content": "Hi"}]})
    assert resp.status_code in (404, 405)  # POST to non-existent route may be 405


def test_legacy_dashboard_returns_404():
    resp = client.get("/v1/dashboard")
    assert resp.status_code == 404


def test_legacy_knowledge_returns_404():
    resp = client.get("/v1/knowledge")
    assert resp.status_code == 404


def test_legacy_operations_returns_404():
    resp = client.get("/v1/operations")
    assert resp.status_code == 404


def test_legacy_jobs_returns_404():
    resp = client.get("/v1/jobs")
    assert resp.status_code == 404


def test_legacy_profiles_returns_404():
    resp = client.get("/v1/profiles")
    assert resp.status_code == 404


# -- Streaming chat --------------------------------------------------------


def test_chat_streams_sse_tokens():
    tokens = ["Hello", " world", "!"]
    service = _mock_hermes_service(tokens)

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/chat",
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
        client.post(f"{WS_PREFIX}/chat", json={"messages": messages})

    service.stream_chat.assert_called_once()
    call_messages = service.stream_chat.call_args[0][0]
    assert len(call_messages) == 1
    assert call_messages[0].role == "user"
    assert call_messages[0].content == "Hello"


def test_chat_passes_workspace_id_to_service():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._hermes_service", service):
        client.post(
            f"{WS_PREFIX}/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert service.stream_chat.call_args[1]["workspace_id"] == WS


def test_chat_passes_profile_to_service():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._hermes_service", service):
        client.post(
            f"{WS_PREFIX}/chat",
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
            f"{WS_PREFIX}/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert service.stream_chat.call_args[1]["profile_id"] is None


def test_chat_with_model_override_creates_new_service():
    service = _mock_hermes_service(["OK"])

    with patch("hermes.gateway.app._build_hermes_service", return_value=service) as mock_build:
        client.post(
            f"{WS_PREFIX}/chat",
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
            f"{WS_PREFIX}/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

    assert resp.headers["content-type"].startswith("text/event-stream")
    service.stream_chat.assert_called_once()


# -- Non-streaming fallback ------------------------------------------------


def test_chat_non_streaming_returns_json():
    service = _mock_hermes_service(["Full response"])

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/chat",
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
            f"{WS_PREFIX}/chat",
            json={
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
                "profile": "business",
            },
        )

    assert service.chat.call_args[1]["profile_id"] == "business"


# -- Profiles endpoint -----------------------------------------------------


def test_list_profiles_returns_all():
    resp = client.get(f"{WS_PREFIX}/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    ids = [p["id"] for p in profiles]
    assert "default" in ids
    assert "developer" in ids
    assert "business" in ids


def test_list_profiles_contains_expected_fields():
    resp = client.get(f"{WS_PREFIX}/profiles")
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
        f"{WS_PREFIX}/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") is not None


# -- Validation ------------------------------------------------------------


def test_chat_rejects_empty_body():
    resp = client.post(f"{WS_PREFIX}/chat")
    assert resp.status_code == 422


def test_chat_rejects_missing_messages():
    resp = client.post(f"{WS_PREFIX}/chat", json={"stream": True})
    assert resp.status_code == 422


# -- Static UI serving -----------------------------------------------------


def test_root_serves_chat_ui():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Hermes" in resp.text


def test_ui_contains_profile_selector():
    resp = client.get("/")
    assert "profile-select" in resp.text


def test_ui_contains_workspace_selector():
    resp = client.get("/")
    assert "workspace-select" in resp.text


# -- Knowledge API ---------------------------------------------------------


def test_knowledge_list_returns_documents():
    resp = client.get(f"{WS_PREFIX}/knowledge")
    assert resp.status_code == 200
    docs = resp.json()
    assert isinstance(docs, list)
    for doc in docs:
        assert "id" in doc
        assert "title" in doc
        assert "size" in doc
        assert "path" in doc


def test_knowledge_list_contains_expected_count():
    resp = client.get(f"{WS_PREFIX}/knowledge")
    docs = resp.json()
    assert len(docs) == 12


def test_knowledge_list_documents_have_positive_size():
    resp = client.get(f"{WS_PREFIX}/knowledge")
    for doc in resp.json():
        assert doc["size"] > 0


def test_knowledge_list_preserves_manifest_order():
    resp = client.get(f"{WS_PREFIX}/knowledge")
    docs = resp.json()
    assert docs[0]["id"] == "01-purpose"
    assert docs[-1]["id"] == "12-homepage-tech-spec"


def test_knowledge_detail_returns_full_document():
    resp = client.get(f"{WS_PREFIX}/knowledge/01-purpose")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["id"] == "01-purpose"
    assert doc["title"] != ""
    assert doc["size"] > 0
    assert doc["path"] != ""
    assert "content" in doc
    assert len(doc["content"]) > 0


def test_knowledge_detail_content_matches_title():
    resp = client.get(f"{WS_PREFIX}/knowledge/01-purpose")
    doc = resp.json()
    assert doc["content"].startswith(f"# {doc['title']}")


def test_knowledge_detail_invalid_id_returns_404():
    resp = client.get(f"{WS_PREFIX}/knowledge/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "nonexistent" in body["error"]


def test_knowledge_list_does_not_expose_content():
    resp = client.get(f"{WS_PREFIX}/knowledge")
    for doc in resp.json():
        assert "content" not in doc


def test_knowledge_list_with_mocked_service_returns_empty():
    service = MagicMock()
    service.list_knowledge.return_value = []

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/knowledge")

    assert resp.status_code == 200
    assert resp.json() == []


# -- Dashboard API ---------------------------------------------------------


def test_dashboard_returns_workspace_identity():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "workspace" in data
    assert "name" in data["workspace"]
    assert "mission" in data["workspace"]


def test_dashboard_contains_operations_section():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    data = resp.json()
    assert "operations" in data
    ops = data["operations"]
    assert "active" in ops
    assert "completed_today" in ops
    assert "total" in ops


def test_dashboard_contains_knowledge_count():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    data = resp.json()
    assert "knowledge" in data
    assert "count" in data["knowledge"]
    assert data["knowledge"]["count"] > 0


def test_dashboard_contains_repository_count():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    data = resp.json()
    assert "repositories" in data
    assert "count" in data["repositories"]


def test_dashboard_contains_kpi_summary():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    data = resp.json()
    assert "kpis" in data
    kpis = data["kpis"]
    assert "total" in kpis
    assert "on_track" in kpis
    assert "at_risk" in kpis
    assert "off_track" in kpis
    assert kpis["total"] == 8


def test_dashboard_contains_todays_focus():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    data = resp.json()
    assert "todays_focus" in data
    assert isinstance(data["todays_focus"], list)
    assert len(data["todays_focus"]) <= 3


def test_dashboard_contains_risks():
    resp = client.get(f"{WS_PREFIX}/dashboard")
    data = resp.json()
    assert "risks" in data
    assert isinstance(data["risks"], list)


def test_dashboard_knowledge_matches_knowledge_api():
    """Dashboard knowledge count must match the Knowledge API dynamically."""
    dashboard_resp = client.get(f"{WS_PREFIX}/dashboard")
    knowledge_resp = client.get(f"{WS_PREFIX}/knowledge")
    assert dashboard_resp.json()["knowledge"]["count"] == len(knowledge_resp.json())


def test_dashboard_delegates_to_service():
    service = MagicMock()
    service.get_dashboard.return_value = {
        "operations": {"active": 0, "completed_today": 0, "total": 0},
        "knowledge": {"count": 5},
        "repositories": {"count": 2},
        "workspace": {"name": "Test", "mission": ""},
        "kpis": {"total": 0, "on_track": 0, "at_risk": 0, "off_track": 0},
        "todays_focus": [],
        "risks": [],
        "execution_status": "completed",
        "warnings": [],
    }

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/dashboard")

    assert resp.status_code == 200
    service.get_dashboard.assert_called_once_with(WS)
    assert resp.json()["knowledge"]["count"] == 5


# -- Brief endpoint -------------------------------------------------------


def test_brief_returns_200():
    resp = client.get(f"{WS_PREFIX}/brief")
    assert resp.status_code == 200


def test_brief_contains_expected_structure():
    resp = client.get(f"{WS_PREFIX}/brief")
    data = resp.json()
    assert "brief" in data
    assert "recommendations" in data
    assert "kpis" in data
    assert "goals" in data
    assert "bottlenecks" in data
    assert "decisions" in data
    assert "experiments" in data
    assert "execution_status" in data


def test_brief_reflects_avanzia_business_data():
    resp = client.get(f"{WS_PREFIX}/brief")
    data = resp.json()
    assert len(data["kpis"]) == 8
    assert len(data["goals"]) == 5
    assert len(data["bottlenecks"]) == 5
    assert len(data["decisions"]) == 6
    assert len(data["experiments"]) == 5


def test_brief_contains_priorities_and_risks():
    resp = client.get(f"{WS_PREFIX}/brief")
    data = resp.json()
    assert len(data["brief"]["priorities"]) > 0
    assert len(data["brief"]["risks"]) > 0


def test_brief_recommendations_have_scores():
    resp = client.get(f"{WS_PREFIX}/brief")
    data = resp.json()
    assert len(data["recommendations"]) > 0
    rec = data["recommendations"][0]
    assert "priority_score" in rec
    assert "confidence" in rec
    assert "suggested_action" in rec


def test_brief_delegates_to_service():
    service = MagicMock()
    service.get_brief.return_value = {
        "brief": {"id": "test", "priorities": [], "risks": []},
        "recommendations": [],
        "kpis": [],
        "goals": [],
        "bottlenecks": [],
        "decisions": [],
        "experiments": [],
        "execution_status": "completed",
        "warnings": [],
    }

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/brief")

    assert resp.status_code == 200
    service.get_brief.assert_called_once_with(WS)


def test_brief_unknown_workspace_returns_404():
    resp = client.get("/v1/workspaces/NONEXISTENT/brief")
    assert resp.status_code == 404


# -- Decision endpoints ----------------------------------------------------


def test_list_decisions_returns_200():
    resp = client.get(f"{WS_PREFIX}/decisions")
    assert resp.status_code == 200
    decisions = resp.json()
    assert isinstance(decisions, list)
    assert len(decisions) == 6  # AVANZIA has 6 existing decisions


def test_list_decisions_contains_expected_fields():
    resp = client.get(f"{WS_PREFIX}/decisions")
    for d in resp.json():
        assert "id" in d
        assert "title" in d
        assert "date" in d
        assert "status" in d
        assert "rationale" in d


def test_create_decision_invalid_action_returns_422():
    body = {"recommendation_id": "rec_bot_001", "action": "invalid"}
    resp = client.post(f"{WS_PREFIX}/decisions", json=body)
    assert resp.status_code == 422


def test_create_decision_unknown_recommendation_returns_404():
    body = {"recommendation_id": "nonexistent", "action": "approve"}
    resp = client.post(f"{WS_PREFIX}/decisions", json=body)
    assert resp.status_code == 404


def test_create_decision_delegates_to_service():
    service = MagicMock()
    service.act_on_recommendation.return_value = {
        "decision": {
            "id": "DEC-007",
            "business_id": "AVANZIA",
            "title": "Test",
            "context": "Test",
            "rationale": "Test",
            "status": "approved",
            "decision_date": "2026-08",
            "owner": "Founder",
            "recommendation_id": "rec_bot_001",
            "review_id": "review_test",
            "brief_id": "brief_test",
            "created_at": "2026-08-01T00:00:00+00:00",
            "updated_at": "2026-08-01T00:00:00+00:00",
        },
        "operation": None,
    }

    body = {"recommendation_id": "rec_bot_001", "action": "approve"}
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/decisions", json=body)

    assert resp.status_code == 201
    service.act_on_recommendation.assert_called_once_with(
        WS, "rec_bot_001", "approve", create_operation=False,
    )


def test_create_decision_with_operation():
    service = MagicMock()
    service.act_on_recommendation.return_value = {
        "decision": {"id": "DEC-007", "status": "approved"},
        "operation": {"id": "OP-20260801-001", "status": "created"},
    }

    body = {
        "recommendation_id": "rec_bot_001",
        "action": "approve",
        "create_operation": True,
    }
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/decisions", json=body)

    assert resp.status_code == 201
    data = resp.json()
    assert data["operation"] is not None
    service.act_on_recommendation.assert_called_once_with(
        WS, "rec_bot_001", "approve", create_operation=True,
    )


def test_decisions_unknown_workspace_returns_404():
    resp = client.get("/v1/workspaces/NONEXISTENT/decisions")
    assert resp.status_code == 404


# -- UI: Home screen -------------------------------------------------------


def test_ui_contains_home_nav_item():
    resp = client.get("/")
    assert 'data-view="home"' in resp.text
    assert "Home" in resp.text


def test_ui_references_dashboard_endpoint():
    resp = client.get("/")
    assert "/dashboard" in resp.text


def test_ui_home_fetches_operations_for_counts():
    resp = client.get("/")
    # Home screen fetches operations in parallel with dashboard
    assert "operationsPromise" in resp.text
    assert "wsBase()" in resp.text


def test_ui_home_attention_items_are_clickable():
    resp = client.get("/")
    assert "home-notif-link" in resp.text
    assert 'switchView("notifications")' in resp.text


def test_ui_home_operations_widget_is_clickable():
    resp = client.get("/")
    assert "home-ops-widget" in resp.text
    assert 'switchView("operations")' in resp.text


def test_ui_home_computes_active_operations():
    resp = client.get("/")
    assert "computeOpsStats" in resp.text
    assert '"created"' in resp.text
    assert '"executing"' in resp.text


def test_ui_home_computes_escalated_operations():
    resp = client.get("/")
    assert '"awaiting_escalation"' in resp.text
    assert "escalated.push" in resp.text


def test_ui_home_computes_completed_today():
    resp = client.get("/")
    assert "completedToday" in resp.text


def test_ui_home_handles_operations_failure():
    """If Operations API fails, Home still renders with operations widget."""
    resp = client.get("/")
    assert "Unavailable" in resp.text
    assert "home-ops-widget" in resp.text


def test_ui_home_has_notification_counters():
    """Home attention section shows compact notification counters (Sprint 36)."""
    resp = client.get("/")
    assert "critical_count" in resp.text or "unread_count" in resp.text
    assert "View all" in resp.text


# -- UI: Brief screen ------------------------------------------------------


def test_ui_contains_brief_nav_item():
    resp = client.get("/")
    assert 'data-view="brief"' in resp.text
    assert "Brief" in resp.text


def test_ui_brief_references_brief_endpoint():
    resp = client.get("/")
    assert "/brief" in resp.text


def test_ui_brief_has_summary_section():
    resp = client.get("/")
    assert "brief-summary" in resp.text


def test_ui_brief_renders_kpi_table():
    resp = client.get("/")
    assert "kpi-table" in resp.text or "brief-kpis" in resp.text


def test_ui_brief_renders_recommendations():
    resp = client.get("/")
    assert "recommendation" in resp.text.lower()


def test_ui_brief_caches_result():
    resp = client.get("/")
    assert "cachedBrief" in resp.text


# -- UI: Decision Inbox (within Brief) ------------------------------------


def test_ui_brief_has_decision_inbox_title():
    resp = client.get("/")
    assert "Decision Inbox" in resp.text


def test_ui_brief_has_approve_button():
    resp = client.get("/")
    assert "btn-approve" in resp.text
    assert "Approve" in resp.text


def test_ui_brief_has_reject_button():
    resp = client.get("/")
    assert "btn-reject" in resp.text
    assert "Reject" in resp.text


def test_ui_brief_has_postpone_button():
    resp = client.get("/")
    assert "btn-postpone" in resp.text
    assert "Postpone" in resp.text


def test_ui_brief_has_create_operation_checkbox():
    resp = client.get("/")
    assert "create-op-check" in resp.text
    assert "Create Operation" in resp.text


def test_ui_brief_has_decision_detail_modal():
    resp = client.get("/")
    assert "decision-modal" in resp.text
    assert "showDecisionDetail" in resp.text


def test_ui_brief_tracks_decision_actions():
    resp = client.get("/")
    assert "decisionActions" in resp.text
    assert "submitDecisionAction" in resp.text


def test_ui_brief_posts_to_decisions_endpoint():
    resp = client.get("/")
    assert '"/decisions"' in resp.text


def test_ui_decision_actions_cleared_on_workspace_switch():
    resp = client.get("/")
    assert "decisionActions = {}" in resp.text


# -- UI: Journal screen ----------------------------------------------------


def test_ui_contains_journal_nav_item():
    resp = client.get("/")
    assert 'data-view="journal"' in resp.text
    assert "Journal" in resp.text


def test_ui_journal_combines_decisions_and_operations():
    resp = client.get("/")
    assert "renderJournal" in resp.text


def test_ui_journal_has_timeline_layout():
    resp = client.get("/")
    assert "journal" in resp.text.lower()


# -- UI: Documents screen --------------------------------------------------


def test_ui_contains_documents_nav_item():
    resp = client.get("/")
    assert 'data-view="documents"' in resp.text
    assert "Documents" in resp.text


def test_ui_documents_view_references_knowledge_api():
    resp = client.get("/")
    assert "/knowledge" in resp.text


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
        "workspace_id": WS,
        "request": "Generate homepage copy",
        "status": "executing",
        "created_at": "2026-08-01T10:30:00+00:00",
        "updated_at": "2026-08-01T10:35:00+00:00",
    }


def test_operations_list_returns_200():
    service = MagicMock()
    service.list_operations.return_value = []

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/operations")

    assert resp.status_code == 200
    assert resp.json() == []


def test_operations_list_delegates_to_service():
    service = MagicMock()
    service.list_operations.return_value = [_mock_operation()]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/operations")

    service.list_operations.assert_called_once_with(WS)
    assert len(resp.json()) == 1
    assert resp.json()[0]["id"] == "OP-20260801-001"


def test_operations_list_deterministic_order():
    service = MagicMock()
    op1 = _mock_operation()
    op2 = {**_mock_operation(), "id": "OP-20260801-002"}
    service.list_operations.return_value = [op1, op2]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/operations")

    ids = [o["id"] for o in resp.json()]
    assert ids == ["OP-20260801-001", "OP-20260801-002"]


def test_operations_detail_returns_operation():
    service = MagicMock()
    service.get_operation.return_value = _mock_operation()

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/operations/OP-20260801-001")

    assert resp.status_code == 200
    assert resp.json()["id"] == "OP-20260801-001"


def test_operations_detail_passes_workspace_id():
    service = MagicMock()
    service.get_operation.return_value = _mock_operation()

    with patch("hermes.gateway.app._hermes_service", service):
        client.get(f"{WS_PREFIX}/operations/OP-20260801-001")

    service.get_operation.assert_called_once_with(WS, "OP-20260801-001")


def test_operations_detail_not_found_returns_404():
    service = MagicMock()
    service.get_operation.return_value = None

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/operations/OP-NONEXISTENT")

    assert resp.status_code == 404
    assert "error" in resp.json()


def test_operations_detail_excludes_extra_fields():
    service = MagicMock()
    service.get_operation.return_value = _mock_operation()

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/operations/OP-20260801-001")

    assert "extra_fields" not in resp.json()


def test_operations_approve_delegates_to_service():
    service = MagicMock()
    approved = {**_mock_operation(), "status": "executing"}
    service.approve_operation.return_value = approved

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/operations/OP-20260801-001/approve")

    assert resp.status_code == 200
    service.approve_operation.assert_called_once_with(WS, "OP-20260801-001")
    assert resp.json()["status"] == "executing"


def test_operations_approve_not_found_returns_404():
    from hermes.kernel.operation_store import OperationNotFoundError

    service = MagicMock()
    service.approve_operation.side_effect = OperationNotFoundError("not found")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/operations/OP-NONEXISTENT/approve")

    assert resp.status_code == 404


def test_operations_approve_invalid_transition_returns_409():
    from hermes.models.operation import InvalidTransitionError

    service = MagicMock()
    service.approve_operation.side_effect = InvalidTransitionError("bad transition")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/operations/OP-20260801-001/approve")

    assert resp.status_code == 409
    assert "error" in resp.json()


def test_operations_reject_delegates_to_service():
    service = MagicMock()
    rejected = {**_mock_operation(), "status": "rejected"}
    service.reject_operation.return_value = rejected

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/operations/OP-20260801-001/reject")

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_operations_reject_not_found_returns_404():
    from hermes.kernel.operation_store import OperationNotFoundError

    service = MagicMock()
    service.reject_operation.side_effect = OperationNotFoundError("not found")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/operations/OP-NONEXISTENT/reject")

    assert resp.status_code == 404


def test_operations_reject_invalid_transition_returns_409():
    from hermes.models.operation import InvalidTransitionError

    service = MagicMock()
    service.reject_operation.side_effect = InvalidTransitionError("bad transition")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(f"{WS_PREFIX}/operations/OP-20260801-001/reject")

    assert resp.status_code == 409


# -- Operations Complete/Fail API (Sprint 30) --------------------------------


def test_operations_complete_delegates_to_service():
    service = MagicMock()
    completed_op = {**_mock_operation(), "status": "completed", "outcome": "Done", "outcome_classification": "success"}
    service.complete_operation.return_value = {"operation": completed_op, "bk_operation_id": "OPS-001"}

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-20260801-001/complete",
            json={"outcome": "Done", "outcome_classification": "success"},
        )

    assert resp.status_code == 200
    service.complete_operation.assert_called_once_with(WS, "OP-20260801-001", "Done", "success")
    assert resp.json()["operation"]["status"] == "completed"
    assert resp.json()["bk_operation_id"] == "OPS-001"


def test_operations_complete_not_found_returns_404():
    from hermes.kernel.operation_store import OperationNotFoundError

    service = MagicMock()
    service.complete_operation.side_effect = OperationNotFoundError("not found")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-NONEXISTENT/complete",
            json={"outcome": "Done"},
        )

    assert resp.status_code == 404


def test_operations_complete_invalid_transition_returns_409():
    from hermes.models.operation import InvalidTransitionError

    service = MagicMock()
    service.complete_operation.side_effect = InvalidTransitionError("bad transition")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-20260801-001/complete",
            json={"outcome": "Done"},
        )

    assert resp.status_code == 409


def test_operations_fail_delegates_to_service():
    service = MagicMock()
    failed_op = {**_mock_operation(), "status": "failed", "outcome": "Blocked", "outcome_classification": "failure"}
    service.fail_operation.return_value = {"operation": failed_op, "bk_operation_id": "OPS-001"}

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-20260801-001/fail",
            json={"outcome": "Blocked", "outcome_classification": "failure"},
        )

    assert resp.status_code == 200
    assert resp.json()["operation"]["status"] == "failed"


def test_operations_fail_not_found_returns_404():
    from hermes.kernel.operation_store import OperationNotFoundError

    service = MagicMock()
    service.fail_operation.side_effect = OperationNotFoundError("not found")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-NONEXISTENT/fail",
            json={"outcome": "Blocked"},
        )

    assert resp.status_code == 404


def test_operations_fail_invalid_transition_returns_409():
    from hermes.models.operation import InvalidTransitionError

    service = MagicMock()
    service.fail_operation.side_effect = InvalidTransitionError("bad transition")

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-20260801-001/fail",
            json={"outcome": "Blocked"},
        )

    assert resp.status_code == 409


def test_operations_complete_default_classification():
    """When outcome_classification is omitted, it defaults to 'success'."""
    service = MagicMock()
    completed_op = {**_mock_operation(), "status": "completed"}
    service.complete_operation.return_value = {"operation": completed_op, "bk_operation_id": "OPS-001"}

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations/OP-20260801-001/complete",
            json={"outcome": "Done"},
        )

    assert resp.status_code == 200
    service.complete_operation.assert_called_once_with(WS, "OP-20260801-001", "Done", "success")


# -- Jobs API --------------------------------------------------------------


def _mock_job(include_output=False):
    data = {
        "id": "JOB-20260801-001",
        "workspace_id": WS,
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
        resp = client.get(f"{WS_PREFIX}/jobs")

    assert resp.status_code == 200
    assert resp.json() == []


def test_jobs_list_delegates_to_service():
    service = MagicMock()
    service.list_jobs.return_value = [_mock_job()]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/jobs")

    service.list_jobs.assert_called_once_with(WS)
    assert len(resp.json()) == 1


def test_jobs_list_excludes_generated_output():
    service = MagicMock()
    service.list_jobs.return_value = [_mock_job(include_output=False)]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/jobs")

    assert "generated_output" not in resp.json()[0]


def test_jobs_list_deterministic_order():
    service = MagicMock()
    j1 = _mock_job()
    j2 = {**_mock_job(), "id": "JOB-20260801-002"}
    service.list_jobs.return_value = [j1, j2]

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/jobs")

    ids = [j["id"] for j in resp.json()]
    assert ids == ["JOB-20260801-001", "JOB-20260801-002"]


def test_jobs_detail_returns_job():
    service = MagicMock()
    service.get_job.return_value = _mock_job(include_output=True)

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/jobs/JOB-20260801-001")

    assert resp.status_code == 200
    assert resp.json()["id"] == "JOB-20260801-001"


def test_jobs_detail_passes_workspace_id():
    service = MagicMock()
    service.get_job.return_value = _mock_job(include_output=True)

    with patch("hermes.gateway.app._hermes_service", service):
        client.get(f"{WS_PREFIX}/jobs/JOB-20260801-001")

    service.get_job.assert_called_once_with(WS, "JOB-20260801-001")


def test_jobs_detail_includes_generated_output():
    service = MagicMock()
    service.get_job.return_value = _mock_job(include_output=True)

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/jobs/JOB-20260801-001")

    assert "generated_output" in resp.json()


def test_jobs_detail_not_found_returns_404():
    service = MagicMock()
    service.get_job.return_value = None

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/jobs/JOB-NONEXISTENT")

    assert resp.status_code == 404
    assert "error" in resp.json()


# -- UI: Operations screen -------------------------------------------------


def test_ui_contains_operations_nav_item():
    resp = client.get("/")
    assert 'data-view="operations"' in resp.text
    assert "Operations" in resp.text


def test_ui_operations_view_references_operations_api():
    resp = client.get("/")
    assert "/operations" in resp.text


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
    assert "/jobs" in resp.text


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


# -- Create Operation API (Sprint 18) --------------------------------------


def test_create_operation_returns_201():
    service = MagicMock()
    service.create_operation_from_chat.return_value = {
        **_mock_operation(),
        "status": "created",
    }

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations",
            json={"request": "Generate homepage copy"},
        )

    assert resp.status_code == 201
    assert resp.json()["status"] == "created"


def test_create_operation_delegates_to_service():
    service = MagicMock()
    service.create_operation_from_chat.return_value = {
        **_mock_operation(),
        "status": "created",
    }

    with patch("hermes.gateway.app._hermes_service", service):
        client.post(
            f"{WS_PREFIX}/operations",
            json={"request": "Generate homepage copy"},
        )

    service.create_operation_from_chat.assert_called_once_with(WS, "Generate homepage copy")


def test_create_operation_response_excludes_extra_fields():
    service = MagicMock()
    service.create_operation_from_chat.return_value = {
        **_mock_operation(),
        "status": "created",
    }

    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.post(
            f"{WS_PREFIX}/operations",
            json={"request": "Test"},
        )

    assert "extra_fields" not in resp.json()


def test_create_operation_missing_request_returns_422():
    resp = client.post(f"{WS_PREFIX}/operations", json={})
    assert resp.status_code == 422


def test_create_operation_empty_body_returns_422():
    resp = client.post(f"{WS_PREFIX}/operations")
    assert resp.status_code == 422


# -- UI: Chat promote to Operation (Sprint 18) -----------------------------


def test_ui_chat_has_promote_button():
    resp = client.get("/")
    assert 'id="btn-promote"' in resp.text
    assert "Promote to Operation" in resp.text


def test_ui_chat_promote_calls_operations_api():
    resp = client.get("/")
    assert "promoteToOperation" in resp.text
    assert 'method: "POST"' in resp.text
    assert "wsBase()" in resp.text


def test_ui_chat_shows_inline_notification():
    resp = client.get("/")
    assert "notification" in resp.text
    assert "created." in resp.text


def test_ui_chat_notification_has_view_link():
    resp = client.get("/")
    assert "notification-link" in resp.text
    assert "View" in resp.text


def test_ui_chat_promote_uses_input_text():
    """Promote uses text from input field, not last message (Founder amendment 1)."""
    resp = client.get("/")
    assert "inputEl.value.trim()" in resp.text


def test_ui_chat_promote_shows_creating_state():
    """Button shows Creating... while POST is in flight (Founder amendment 3)."""
    resp = client.get("/")
    assert '"Creating..."' in resp.text


def test_ui_chat_promote_disables_during_request():
    """Button is disabled during POST (Founder amendment 3)."""
    resp = client.get("/")
    assert "promoteBtn.disabled = true" in resp.text
    assert "promoteBtn.disabled = false" in resp.text


# -- UI: Notification Badges (Sprint 19) ------------------------------------


def test_ui_has_nav_badge_css():
    resp = client.get("/")
    assert ".nav-badge" in resp.text


def test_ui_has_nav_badge_alert_css():
    resp = client.get("/")
    assert ".nav-badge.alert" in resp.text


def test_ui_has_load_badges_function():
    resp = client.get("/")
    assert "function loadBadges()" in resp.text


def test_ui_badges_fetch_operations():
    resp = client.get("/")
    assert "loadBadges" in resp.text


def test_ui_badges_fetch_jobs():
    resp = client.get("/")
    assert "/jobs" in resp.text


def test_ui_badges_compute_escalation_count():
    resp = client.get("/")
    assert '"awaiting_escalation"' in resp.text
    assert "escalated" in resp.text


def test_ui_badges_compute_failed_count():
    resp = client.get("/")
    assert '"failed"' in resp.text


def test_ui_badges_render_on_home_nav():
    resp = client.get("/")
    assert 'setBadge("home"' in resp.text


def test_ui_badges_render_on_operations_nav():
    resp = client.get("/")
    assert 'setBadge("operations"' in resp.text


def test_ui_badges_render_on_jobs_nav():
    resp = client.get("/")
    assert 'setBadge("jobs"' in resp.text


def test_ui_badges_clear_on_navigation():
    resp = client.get("/")
    assert "clearBadge(name)" in resp.text


def test_ui_badges_loaded_on_init():
    resp = client.get("/")
    # loadBadges() is called via selectWorkspace during init
    lines = resp.text.split("\n")
    assert any("loadBadges()" in line for line in lines)


def test_ui_badges_format_count_caps_at_99():
    """Counts over 99 display as '99+' (Founder amendment 3)."""
    resp = client.get("/")
    assert 'return "99+"' in resp.text
    assert "formatBadgeCount" in resp.text


def test_ui_badges_refresh_after_promote():
    """loadBadges() is called after successful promote (Founder amendment 1)."""
    resp = client.get("/")
    # In promoteToOperation success handler, loadBadges() is called
    assert "loadBadges" in resp.text


def test_ui_badges_refresh_after_approve():
    """loadBadges() is called after successful approve (Founder amendment 1)."""
    resp = client.get("/")
    # approveOperation calls loadBadges after success
    text = resp.text
    # Find approveOperation function and verify loadBadges is in it
    idx = text.find("function approveOperation")
    assert idx > 0
    next_fn = text.find("function rejectOperation", idx)
    section = text[idx:next_fn]
    assert "loadBadges()" in section


def test_ui_badges_refresh_after_reject():
    """loadBadges() is called after successful reject (Founder amendment 1)."""
    resp = client.get("/")
    text = resp.text
    idx = text.find("function rejectOperation")
    assert idx > 0
    next_section = text.find("// ──", idx)
    section = text[idx:next_section]
    assert "loadBadges()" in section


def test_ui_badges_independent_failure():
    """Operations, Jobs, and Notifications badges are fetched independently."""
    resp = client.get("/")
    # Three separate fetch calls, not Promise.all
    text = resp.text
    idx = text.find("function loadBadges()")
    assert idx > 0
    end = text.find("// ──", idx + 1)
    section = text[idx:end]
    # Should have three separate fetch calls using wsBase()
    fetches = section.count("fetch(wsBase()")
    assert fetches == 3


# -- UI: Workspace selector (Sprint 26) ------------------------------------


def test_ui_has_workspace_selector_css():
    resp = client.get("/")
    assert "#workspace-select" in resp.text


def test_ui_has_workspace_select_element():
    resp = client.get("/")
    assert 'id="workspace-select"' in resp.text


def test_ui_has_load_workspaces_function():
    resp = client.get("/")
    assert "function loadWorkspaces()" in resp.text


def test_ui_workspace_selection_uses_localstorage():
    resp = client.get("/")
    assert 'localStorage.getItem("hermes_workspace")' in resp.text
    assert 'localStorage.setItem("hermes_workspace"' in resp.text


def test_ui_workspace_three_tier_selection():
    """Workspace selection follows restore → auto-select singleton → require explicit."""
    resp = client.get("/")
    text = resp.text
    # Tier 1: restore last-used
    assert "lastUsed" in text
    assert "wsIds.indexOf(lastUsed)" in text
    # Tier 2: auto-select singleton
    assert "workspaces.length === 1" in text
    # Tier 3: require explicit
    assert "activeWorkspaceId = null" in text


def test_ui_workspace_fetch_uses_wsbase():
    """All data fetches use wsBase() for workspace-scoped URLs."""
    resp = client.get("/")
    assert "function wsBase()" in resp.text
    assert "wsBase()" in resp.text


def test_ui_workspace_init_loads_workspaces():
    """Init calls loadWorkspaces() instead of direct loadProfiles/loadDashboard."""
    resp = client.get("/")
    text = resp.text
    idx = text.find("// ── Init")
    assert idx > 0
    init_section = text[idx:]
    assert "loadWorkspaces()" in init_section


def test_ui_workspace_select_change_triggers_reload():
    """Changing workspace selector triggers data reload."""
    resp = client.get("/")
    assert 'workspaceSel.addEventListener("change"' in resp.text
    assert "selectWorkspace" in resp.text


# -- UI: Sprint 30 — Operations Completion & Journal Lessons -----------------


def test_ui_journal_renders_lessons():
    """Business Journal should aggregate lessons from brief data."""
    resp = client.get("/")
    assert "briefData.lessons" in resp.text
    assert '"lesson"' in resp.text


def test_ui_journal_lesson_type_css():
    """Lesson type should have its own CSS class."""
    resp = client.get("/")
    assert ".journal-type.lesson" in resp.text


def test_ui_operation_detail_has_complete_button():
    """Operation detail shows Complete button for executing operations."""
    resp = client.get("/")
    assert "op-complete" in resp.text
    assert "completeOperation" in resp.text


def test_ui_operation_detail_has_fail_button():
    """Operation detail shows Mark Failed button for executing operations."""
    resp = client.get("/")
    assert "op-fail" in resp.text
    assert "failOperation" in resp.text


def test_ui_operation_detail_has_outcome_textarea():
    """Operation detail has textarea for outcome description."""
    resp = client.get("/")
    assert "op-outcome-text" in resp.text


def test_ui_operation_detail_has_classification_select():
    """Operation detail has select for outcome classification."""
    resp = client.get("/")
    assert "op-outcome-cls" in resp.text
    assert "success" in resp.text
    assert "partial" in resp.text


def test_ui_operation_detail_shows_outcome_when_completed():
    """Completed operations should display their outcome text."""
    resp = client.get("/")
    assert "op.outcome" in resp.text
    assert "outcome_classification" in resp.text


def test_ui_operation_detail_shows_decision_id():
    """Operation detail shows decision_id for traceability."""
    resp = client.get("/")
    assert "op.decision_id" in resp.text


def test_ui_complete_operation_posts_to_endpoint():
    """completeOperation function posts to /operations/{id}/complete."""
    resp = client.get("/")
    assert "/complete" in resp.text
    assert "outcome_classification" in resp.text


def test_ui_fail_operation_posts_to_endpoint():
    """failOperation function posts to /operations/{id}/fail."""
    resp = client.get("/")
    assert "/fail" in resp.text


# -- Capabilities API (Sprint 31) -------------------------------------------


def test_get_capabilities_returns_200():
    service = MagicMock()
    service.list_capabilities.return_value = [
        {"id": "python", "name": "Python", "version": "1.0.0", "description": "Dev", "status": "active", "provides": []},
    ]
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/capabilities")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    service.list_capabilities.assert_called_once_with(WS)


def test_get_capabilities_has_required_fields():
    service = MagicMock()
    service.list_capabilities.return_value = [
        {"id": "python", "name": "Python", "version": "1.0.0", "description": "Dev", "status": "active", "provides": ["python development"]},
    ]
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/capabilities")
    cap = resp.json()[0]
    assert "id" in cap
    assert "name" in cap
    assert "status" in cap


def test_get_capability_detail_returns_200():
    service = MagicMock()
    service.get_capability.return_value = {
        "id": "python", "name": "Python", "version": "1.0.0",
        "description": "Dev", "status": "active", "provides": [],
        "inputs": [], "outputs": [], "keywords": [], "sop_ref": None,
        "skill_id": "python", "owner": "Technology", "depends_on": [],
    }
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/capabilities/python")
    assert resp.status_code == 200
    assert resp.json()["id"] == "python"
    assert resp.json()["owner"] == "Technology"


def test_get_capability_not_found_returns_404():
    service = MagicMock()
    service.get_capability.return_value = None
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/capabilities/nonexistent")
    assert resp.status_code == 404


# -- UI: Capabilities view (Sprint 31) --------------------------------------


def test_ui_capabilities_view_exists():
    resp = client.get("/")
    assert 'data-view="capabilities"' in resp.text
    assert 'id="view-capabilities"' in resp.text


def test_ui_capabilities_replaces_placeholder():
    """The old Skills 'Coming soon' placeholder should be gone."""
    resp = client.get("/")
    # The old skills placeholder should not exist
    assert 'id="view-skills"' not in resp.text


def test_ui_capabilities_renders_list():
    resp = client.get("/")
    assert "loadCapabilities" in resp.text
    assert "renderCapabilityList" in resp.text


def test_ui_capabilities_renders_detail():
    resp = client.get("/")
    assert "renderCapabilityDetail" in resp.text
    assert "loadCapabilityDetail" in resp.text


def test_ui_capabilities_shows_provides_requires_outputs():
    """UI uses Provides/Requires/Outputs labels per Founder amendment."""
    resp = client.get("/")
    assert "Provides" in resp.text
    assert "Requires" in resp.text
    assert "Outputs" in resp.text


def test_ui_capabilities_shows_depends_on():
    resp = client.get("/")
    assert "Depends On" in resp.text


def test_ui_capabilities_shows_owner():
    resp = client.get("/")
    assert "cap.owner" in resp.text


def test_ui_capabilities_has_status_badges():
    resp = client.get("/")
    assert ".status-badge.active" in resp.text
    assert ".status-badge.draft" in resp.text
    assert ".status-badge.experimental" in resp.text
    assert ".status-badge.deprecated" in resp.text


def test_ui_capabilities_shows_sop_links():
    """Capability detail shows clickable SOP links."""
    resp = client.get("/")
    assert "Standard Operating Procedures" in resp.text
    assert "sop-link" in resp.text


# -- SOPs API (Sprint 32) -----------------------------------------------------


def test_get_sops_returns_200():
    service = MagicMock()
    service.list_sops.return_value = [
        {"id": "copywriting/content-review", "title": "Content Review Process",
         "skill_id": "copywriting", "description": "Procedure.", "status": "active",
         "owner": "Marketing", "category": "Marketing"},
    ]
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/sops")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    service.list_sops.assert_called_once_with(WS)


def test_get_sops_has_required_fields():
    service = MagicMock()
    service.list_sops.return_value = [
        {"id": "copywriting/content-review", "title": "Content Review",
         "skill_id": "copywriting", "description": "Procedure.", "status": "active",
         "owner": "Marketing", "category": "Marketing"},
    ]
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/sops")
    sop = resp.json()[0]
    assert "id" in sop
    assert "title" in sop
    assert "skill_id" in sop
    assert "category" in sop


def test_get_sop_detail_returns_200():
    service = MagicMock()
    service.get_sop.return_value = {
        "id": "copywriting/content-review", "title": "Content Review Process",
        "skill_id": "copywriting", "filename": "content-review.md",
        "content": "# Content Review Process\n\nDetails.",
        "description": "Procedure.", "version": "1.0.0", "status": "active",
        "owner": "Marketing", "category": "Marketing",
    }
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/sops/copywriting/content-review")
    assert resp.status_code == 200
    assert resp.json()["id"] == "copywriting/content-review"
    assert resp.json()["content"] == "# Content Review Process\n\nDetails."


def test_get_sop_not_found_returns_404():
    service = MagicMock()
    service.get_sop.return_value = None
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/sops/nonexistent/sop")
    assert resp.status_code == 404


# -- UI: SOPs view (Sprint 32) ------------------------------------------------


def test_ui_sops_view_exists():
    resp = client.get("/")
    assert 'data-view="sops"' in resp.text
    assert 'id="view-sops"' in resp.text


def test_ui_sops_nav_item():
    resp = client.get("/")
    assert ">SOPs</div>" in resp.text or "SOPs" in resp.text


def test_ui_sops_renders_list():
    resp = client.get("/")
    assert "loadSOPs" in resp.text
    assert "renderSOPList" in resp.text


def test_ui_sops_renders_detail():
    resp = client.get("/")
    assert "renderSOPDetail" in resp.text
    assert "loadSOPDetail" in resp.text


def test_ui_sops_shows_category():
    resp = client.get("/")
    assert "sop.category" in resp.text


def test_ui_sops_shows_content():
    resp = client.get("/")
    assert "sop.content" in resp.text


def test_ui_sops_has_back_button():
    resp = client.get("/")
    assert "sop-back" in resp.text


# -- Departments API (Sprint 33) ----------------------------------------------


def test_get_departments_returns_200():
    service = MagicMock()
    service.list_departments.return_value = [
        {"id": "marketing", "name": "Marketing", "description": "Brand.",
         "owner": "CMO", "status": "active", "capability_count": 2,
         "active_capability_count": 2, "sop_count": 1},
    ]
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/departments")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    service.list_departments.assert_called_once_with(WS)


def test_get_departments_has_aggregate_metrics():
    service = MagicMock()
    service.list_departments.return_value = [
        {"id": "marketing", "name": "Marketing", "description": "Brand.",
         "owner": "CMO", "status": "active", "capability_count": 2,
         "active_capability_count": 2, "sop_count": 1},
    ]
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/departments")
    dept = resp.json()[0]
    assert "capability_count" in dept
    assert "active_capability_count" in dept
    assert "sop_count" in dept


def test_get_department_detail_returns_200():
    service = MagicMock()
    service.get_department.return_value = {
        "id": "marketing", "name": "Marketing", "description": "Brand.",
        "mission": "Build the brand.", "owner": "CMO", "status": "active",
        "tags": ["brand"], "capabilities": [{"id": "copywriting", "name": "Copywriting", "status": "active"}],
        "capability_count": 1, "active_capability_count": 1, "sop_count": 1,
    }
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/departments/marketing")
    assert resp.status_code == 200
    assert resp.json()["id"] == "marketing"
    assert len(resp.json()["capabilities"]) == 1


def test_get_department_not_found_returns_404():
    service = MagicMock()
    service.get_department.return_value = None
    with patch("hermes.gateway.app._hermes_service", service):
        resp = client.get(f"{WS_PREFIX}/departments/nonexistent")
    assert resp.status_code == 404


# -- UI: Departments view (Sprint 33) -----------------------------------------


def test_ui_departments_view_exists():
    resp = client.get("/")
    assert 'data-view="departments"' in resp.text
    assert 'id="view-departments"' in resp.text


def test_ui_departments_nav_item():
    resp = client.get("/")
    assert ">Departments</div>" in resp.text or "Departments" in resp.text


def test_ui_departments_renders_list():
    resp = client.get("/")
    assert "loadDepartments" in resp.text
    assert "renderDepartmentList" in resp.text


def test_ui_departments_renders_detail():
    resp = client.get("/")
    assert "renderDepartmentDetail" in resp.text
    assert "loadDepartmentDetail" in resp.text


def test_ui_departments_shows_capabilities():
    resp = client.get("/")
    assert "dept.capabilities" in resp.text or "dept-cap-link" in resp.text


def test_ui_departments_shows_mission():
    resp = client.get("/")
    assert "dept.mission" in resp.text


def test_ui_departments_has_back_button():
    resp = client.get("/")
    assert "dept-back" in resp.text


def test_ui_capability_detail_shows_department_link():
    """Capability detail shows clickable department link."""
    resp = client.get("/")
    assert "dept-link" in resp.text
    assert "cap.department_id" in resp.text


def test_ui_capability_detail_shows_both_department_and_owner():
    """Both Department and Owner are displayed in capability metadata."""
    resp = client.get("/")
    # Department line
    assert "Department:" in resp.text
    # Owner line preserved
    assert "Owner:" in resp.text
