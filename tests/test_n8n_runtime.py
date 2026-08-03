"""Tests for n8n Runtime — Sprint 43.

Covers: Workflow model, N8nProvider, N8nRuntime, Gateway endpoints,
and Context Graph integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermes.models.workflow import Workflow, compute_attention_state
from hermes.runtime.n8n_provider import N8nProvider
from hermes.runtime.n8n_runtime import (
    N8nHealthStatus,
    N8nRuntime,
    _slugify,
    _extract_trigger_types,
    _clean_trigger_type,
)


# ── Workflow model tests ─────────────────────────────────────────────────────


def test_workflow_defaults():
    wf = Workflow(id="test", name="Test", provider_id="1")
    assert wf.id == "test"
    assert wf.name == "Test"
    assert wf.provider_id == "1"
    assert wf.active is False
    assert wf.tags == []
    assert wf.trigger_types == []
    assert wf.node_count == 0
    assert wf.execution_count == 0
    assert wf.last_execution == ""
    assert wf.last_success == ""
    assert wf.last_failure == ""
    assert wf.status == "unknown"
    assert wf.attention_state == "ok"
    assert wf.created_at == ""
    assert wf.updated_at == ""


def test_workflow_with_all_fields():
    wf = Workflow(
        id="process-orders",
        name="Process Orders",
        provider_id="42",
        active=True,
        tags=["production", "etsy"],
        trigger_types=["webhook"],
        node_count=5,
        execution_count=100,
        last_execution="2026-08-01T10:00:00Z",
        last_success="2026-08-01T10:00:00Z",
        last_failure="2026-07-30T08:00:00Z",
        status="active",
        attention_state="warning",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-08-01T10:00:00Z",
    )
    assert wf.active is True
    assert wf.node_count == 5
    assert wf.execution_count == 100
    assert wf.tags == ["production", "etsy"]


# ── compute_attention_state tests ────────────────────────────────────────────


def test_attention_state_inactive_is_ok():
    assert compute_attention_state(False, "", "") == "ok"


def test_attention_state_inactive_with_failure_is_ok():
    assert compute_attention_state(False, "2026-08-01", "") == "ok"


def test_attention_state_active_no_failure_is_ok():
    assert compute_attention_state(True, "", "") == "ok"


def test_attention_state_active_failure_no_success_is_critical():
    assert compute_attention_state(True, "2026-08-01", "") == "critical"


def test_attention_state_active_failure_after_success_is_critical():
    assert compute_attention_state(True, "2026-08-02", "2026-08-01") == "critical"


def test_attention_state_active_success_after_failure_is_warning():
    assert compute_attention_state(True, "2026-08-01", "2026-08-02") == "warning"


def test_attention_state_active_success_equals_failure_is_warning():
    assert compute_attention_state(True, "2026-08-01", "2026-08-01") == "warning"


# ── _slugify tests ───────────────────────────────────────────────────────────


def test_slugify_basic():
    assert _slugify("Process Etsy Orders") == "process-etsy-orders"


def test_slugify_special_chars():
    assert _slugify("My Workflow (v2)") == "my-workflow-v2"


def test_slugify_extra_spaces():
    assert _slugify("  Hello   World  ") == "hello-world"


def test_slugify_empty():
    assert _slugify("") == "unnamed"


def test_slugify_only_special_chars():
    assert _slugify("!!!") == "unnamed"


def test_slugify_unicode():
    assert _slugify("Café Orders") == "caf-orders"


def test_slugify_numbers():
    assert _slugify("Workflow 123") == "workflow-123"


# ── _extract_trigger_types tests ─────────────────────────────────────────────


def test_extract_trigger_types_empty():
    assert _extract_trigger_types([]) == []


def test_extract_trigger_types_none():
    assert _extract_trigger_types(None) == []


def test_extract_trigger_types_string_not_list():
    assert _extract_trigger_types("invalid") == []


def test_extract_trigger_types_webhook():
    nodes = [
        {"type": "n8n-nodes-base.webhookTrigger"},
        {"type": "n8n-nodes-base.httpRequest"},
    ]
    result = _extract_trigger_types(nodes)
    assert result == ["webhook"]


def test_extract_trigger_types_cron():
    nodes = [{"type": "n8n-nodes-base.cronTrigger"}]
    result = _extract_trigger_types(nodes)
    assert result == ["cron"]


def test_extract_trigger_types_manual():
    nodes = [{"type": "n8n-nodes-base.manualTrigger"}]
    result = _extract_trigger_types(nodes)
    assert result == ["manual"]


def test_extract_trigger_types_deduplicates():
    nodes = [
        {"type": "n8n-nodes-base.webhookTrigger"},
        {"type": "n8n-nodes-base.webhookTrigger"},
    ]
    result = _extract_trigger_types(nodes)
    assert result == ["webhook"]


def test_extract_trigger_types_multiple():
    nodes = [
        {"type": "n8n-nodes-base.webhookTrigger"},
        {"type": "n8n-nodes-base.cronTrigger"},
    ]
    result = _extract_trigger_types(nodes)
    assert result == ["webhook", "cron"]


def test_extract_trigger_types_skips_non_triggers():
    nodes = [
        {"type": "n8n-nodes-base.httpRequest"},
        {"type": "n8n-nodes-base.set"},
    ]
    result = _extract_trigger_types(nodes)
    assert result == []


def test_extract_trigger_types_skips_non_dict_nodes():
    nodes = [42, "invalid", {"type": "n8n-nodes-base.webhookTrigger"}]
    result = _extract_trigger_types(nodes)
    assert result == ["webhook"]


# ── _clean_trigger_type tests ────────────────────────────────────────────────


def test_clean_trigger_type_webhook():
    assert _clean_trigger_type("n8n-nodes-base.webhookTrigger") == "webhook"


def test_clean_trigger_type_cron():
    assert _clean_trigger_type("n8n-nodes-base.cronTrigger") == "cron"


def test_clean_trigger_type_manual():
    assert _clean_trigger_type("n8n-nodes-base.manualTrigger") == "manual"


def test_clean_trigger_type_no_dot():
    assert _clean_trigger_type("webhookTrigger") == "webhook"


def test_clean_trigger_type_just_trigger():
    assert _clean_trigger_type("Trigger") == "trigger"


# ── N8nProvider tests ────────────────────────────────────────────────────────


def test_provider_not_configured_without_url():
    p = N8nProvider(api_url="", api_key="key")
    assert p.configured is False


def test_provider_not_configured_without_key():
    p = N8nProvider(api_url="http://localhost:5678", api_key="")
    assert p.configured is False


def test_provider_configured():
    p = N8nProvider(api_url="http://localhost:5678", api_key="secret")
    assert p.configured is True


def test_provider_url_strips_trailing_slash():
    p = N8nProvider(api_url="http://localhost:5678/", api_key="key")
    assert p._api_url == "http://localhost:5678"


def test_provider_health_unconfigured():
    p = N8nProvider(api_url="", api_key="")
    result = p.health()
    assert result["configured"] is False
    assert result["authenticated"] is False
    assert result["reachable"] is False


def test_provider_list_workflows_unconfigured():
    p = N8nProvider(api_url="", api_key="")
    assert p.list_workflows() == []


def test_provider_get_workflow_unconfigured():
    p = N8nProvider(api_url="", api_key="")
    assert p.get_workflow("1") is None


def test_provider_list_executions_unconfigured():
    p = N8nProvider(api_url="", api_key="")
    assert p.list_executions("1") == []


# ── N8nHealthStatus tests ────────────────────────────────────────────────────


def test_health_status_defaults():
    h = N8nHealthStatus()
    assert h.configured is False
    assert h.authenticated is False
    assert h.reachable is False
    assert h.api_version == ""
    assert h.workflow_count == 0
    assert h.active_count == 0
    assert h.failed_recently == 0
    assert h.last_sync == ""
    assert h.refresh_duration_ms == 0


def test_health_status_with_values():
    h = N8nHealthStatus(
        configured=True,
        authenticated=True,
        reachable=True,
        workflow_count=5,
        active_count=3,
        failed_recently=1,
        last_sync="2026-08-01T00:00:00Z",
        refresh_duration_ms=42,
    )
    assert h.workflow_count == 5
    assert h.active_count == 3
    assert h.failed_recently == 1
    assert h.refresh_duration_ms == 42


# ── N8nRuntime tests ─────────────────────────────────────────────────────────


def _sample_workflow(
    wf_id="1",
    name="Process Orders",
    active=True,
    tags=None,
    nodes=None,
):
    """Create a sample n8n workflow dict."""
    result = {
        "id": wf_id,
        "name": name,
        "active": active,
        "tags": tags or [],
        "nodes": nodes or [
            {"type": "n8n-nodes-base.webhookTrigger", "name": "Webhook"},
            {"type": "n8n-nodes-base.httpRequest", "name": "HTTP"},
        ],
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-08-01T10:00:00Z",
    }
    return result


def _make_runtime(workflows=None, executions=None, health_response=None):
    """Create an N8nRuntime with a mocked provider."""
    provider = MagicMock(spec=N8nProvider)
    provider.configured = True
    provider.list_workflows.return_value = workflows or []
    provider.list_executions.return_value = executions or []
    provider.health.return_value = health_response or {
        "configured": True,
        "authenticated": True,
        "reachable": True,
        "workflow_count": len(workflows or []),
        "active_count": sum(1 for w in (workflows or []) if w.get("active")),
    }
    return N8nRuntime(provider)


def test_runtime_not_configured():
    provider = MagicMock(spec=N8nProvider)
    provider.configured = False
    runtime = N8nRuntime(provider)
    assert runtime.configured is False
    assert runtime.list_workflows() == []


def test_runtime_configured():
    runtime = _make_runtime()
    assert runtime.configured is True


def test_runtime_list_workflows_empty():
    runtime = _make_runtime(workflows=[])
    assert runtime.list_workflows() == []


def test_runtime_list_workflows_single():
    runtime = _make_runtime(workflows=[_sample_workflow()])
    workflows = runtime.list_workflows()
    assert len(workflows) == 1
    wf = workflows[0]
    assert isinstance(wf, Workflow)
    assert wf.name == "Process Orders"
    assert wf.id == "process-orders"  # Amendment 1: slugified
    assert wf.provider_id == "1"  # Amendment 1: preserved
    assert wf.active is True
    assert wf.status == "active"


def test_runtime_hermes_id_is_slugified():
    runtime = _make_runtime(workflows=[_sample_workflow(name="My Cool Workflow")])
    wf = runtime.list_workflows()[0]
    assert wf.id == "my-cool-workflow"


def test_runtime_provider_id_preserved():
    runtime = _make_runtime(workflows=[_sample_workflow(wf_id="42")])
    wf = runtime.list_workflows()[0]
    assert wf.provider_id == "42"


def test_runtime_inactive_workflow():
    runtime = _make_runtime(workflows=[_sample_workflow(active=False)])
    wf = runtime.list_workflows()[0]
    assert wf.active is False
    assert wf.status == "inactive"
    assert wf.attention_state == "ok"


def test_runtime_extracts_trigger_types():
    nodes = [
        {"type": "n8n-nodes-base.cronTrigger", "name": "Cron"},
        {"type": "n8n-nodes-base.set", "name": "Set"},
    ]
    runtime = _make_runtime(workflows=[_sample_workflow(nodes=nodes)])
    wf = runtime.list_workflows()[0]
    assert wf.trigger_types == ["cron"]


def test_runtime_node_count():
    nodes = [
        {"type": "n8n-nodes-base.webhookTrigger", "name": "Webhook"},
        {"type": "n8n-nodes-base.httpRequest", "name": "HTTP"},
        {"type": "n8n-nodes-base.set", "name": "Set"},
    ]
    runtime = _make_runtime(workflows=[_sample_workflow(nodes=nodes)])
    wf = runtime.list_workflows()[0]
    assert wf.node_count == 3


def test_runtime_tags_from_dicts():
    tags = [{"name": "production"}, {"name": "etsy"}]
    runtime = _make_runtime(workflows=[_sample_workflow(tags=tags)])
    wf = runtime.list_workflows()[0]
    assert wf.tags == ["production", "etsy"]


def test_runtime_tags_from_strings():
    tags = ["production", "etsy"]
    runtime = _make_runtime(workflows=[_sample_workflow(tags=tags)])
    wf = runtime.list_workflows()[0]
    assert wf.tags == ["production", "etsy"]


def test_runtime_timestamps():
    runtime = _make_runtime(workflows=[_sample_workflow()])
    wf = runtime.list_workflows()[0]
    assert wf.created_at == "2026-01-01T00:00:00Z"
    assert wf.updated_at == "2026-08-01T10:00:00Z"


def test_runtime_get_workflow_found():
    runtime = _make_runtime(workflows=[_sample_workflow()])
    wf = runtime.get_workflow("process-orders")
    assert wf is not None
    assert wf.id == "process-orders"


def test_runtime_get_workflow_not_found():
    runtime = _make_runtime(workflows=[_sample_workflow()])
    wf = runtime.get_workflow("nonexistent")
    assert wf is None


def test_runtime_get_workflow_not_configured():
    provider = MagicMock(spec=N8nProvider)
    provider.configured = False
    runtime = N8nRuntime(provider)
    assert runtime.get_workflow("anything") is None


def test_runtime_multiple_workflows():
    workflows = [
        _sample_workflow(wf_id="1", name="Process Orders"),
        _sample_workflow(wf_id="2", name="Sync Inventory", active=False),
        _sample_workflow(wf_id="3", name="Send Notifications"),
    ]
    runtime = _make_runtime(workflows=workflows)
    result = runtime.list_workflows()
    assert len(result) == 3
    ids = [w.id for w in result]
    assert "process-orders" in ids
    assert "sync-inventory" in ids
    assert "send-notifications" in ids


def test_runtime_health_unconfigured():
    provider = MagicMock(spec=N8nProvider)
    provider.configured = True
    provider.health.return_value = {"configured": False, "authenticated": False, "reachable": False}
    provider.list_workflows.return_value = []
    runtime = N8nRuntime(provider)
    h = runtime.health()
    assert isinstance(h, N8nHealthStatus)
    assert h.configured is False


def test_runtime_health_includes_sync_metadata():
    """Amendment 3: last_sync and refresh_duration_ms."""
    runtime = _make_runtime(workflows=[])
    h = runtime.health()
    assert h.last_sync != ""  # Should be populated with ISO timestamp
    assert isinstance(h.refresh_duration_ms, int)


def test_runtime_health_counts_failed_workflows():
    workflows = [
        _sample_workflow(wf_id="1", name="Workflow A"),
        _sample_workflow(wf_id="2", name="Workflow B"),
    ]
    provider = MagicMock(spec=N8nProvider)
    provider.configured = True
    provider.health.return_value = {
        "configured": True, "authenticated": True, "reachable": True,
        "workflow_count": 2, "active_count": 2,
    }
    provider.list_workflows.return_value = workflows

    # First workflow: last execution failed
    # Second workflow: last execution succeeded
    def mock_executions(wf_id, limit=1):
        if wf_id == "1":
            return [{"finished": True, "status": "error"}]
        return [{"finished": True, "status": "success"}]

    provider.list_executions.side_effect = mock_executions

    runtime = N8nRuntime(provider)
    h = runtime.health()
    assert h.failed_recently == 1


def test_runtime_empty_name_uses_provider_id():
    raw = {"id": "99", "name": "", "active": True, "tags": [], "nodes": []}
    runtime = _make_runtime(workflows=[raw])
    wf = runtime.list_workflows()[0]
    assert wf.id == "99"


# ── Gateway endpoint tests ───────────────────────────────────────────────────


@pytest.fixture
def gateway_client():
    """Create a test client for the gateway app."""
    from hermes.gateway.app import app

    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("starlette test client not available")

    return TestClient(app, raise_server_exceptions=False)


def test_gateway_list_workflows(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.list_workflows.return_value = [
            {
                "id": "process-orders",
                "name": "Process Orders",
                "provider_id": "1",
                "active": True,
                "tags": [],
                "trigger_types": ["webhook"],
                "node_count": 2,
                "execution_count": 0,
                "last_execution": "",
                "last_success": "",
                "last_failure": "",
                "status": "active",
                "attention_state": "ok",
                "created_at": "",
                "updated_at": "",
            }
        ]
        resp = gateway_client.get("/v1/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "process-orders"
        assert data[0]["provider_id"] == "1"


def test_gateway_get_workflow_found(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.get_workflow.return_value = {
            "id": "process-orders",
            "name": "Process Orders",
            "provider_id": "1",
            "active": True,
            "tags": [],
            "trigger_types": [],
            "node_count": 2,
            "execution_count": 0,
            "last_execution": "",
            "last_success": "",
            "last_failure": "",
            "status": "active",
            "attention_state": "ok",
            "created_at": "",
            "updated_at": "",
        }
        resp = gateway_client.get("/v1/workflows/process-orders")
        assert resp.status_code == 200
        assert resp.json()["id"] == "process-orders"


def test_gateway_get_workflow_not_found(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.get_workflow.return_value = None
        resp = gateway_client.get("/v1/workflows/nonexistent")
        assert resp.status_code == 404


def test_gateway_n8n_health(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.n8n_health.return_value = {
            "configured": True,
            "authenticated": True,
            "reachable": True,
            "api_version": "",
            "workflow_count": 5,
            "active_count": 3,
            "failed_recently": 0,
            "last_sync": "2026-08-01T00:00:00Z",
            "refresh_duration_ms": 42,
        }
        resp = gateway_client.get("/health/n8n")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["workflow_count"] == 5
        assert data["refresh_duration_ms"] == 42


# ── Context Graph integration tests ──────────────────────────────────────────


def test_context_graph_workflow_type_supported():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert "workflow" in SUPPORTED_TYPES


def test_context_graph_workflow_edges_exist():
    from hermes.context.context_graph import _EDGES
    assert "workflow" in _EDGES
    wf_edges = _EDGES["workflow"]
    # Should have edges to capabilities, operations, repositories, services, people, departments, goals, notifications
    expected_keys = {"capabilities", "operations", "repositories", "services", "people", "departments", "goals", "notifications"}
    assert expected_keys.issubset(set(wf_edges.keys()))


def test_context_graph_other_types_have_workflow_edge():
    from hermes.context.context_graph import _EDGES
    # Types that should have a "workflows" edge
    for t in ["goal", "person", "department", "capability", "operation", "repository", "service"]:
        assert "workflows" in _EDGES[t], f"{t} should have 'workflows' edge"


def test_context_graph_all_relation_keys_include_workflows():
    from hermes.context.context_graph import ALL_RELATION_KEYS
    assert "workflows" in ALL_RELATION_KEYS


# ── Capability workflow_refs test ────────────────────────────────────────────


def test_capability_has_workflow_refs():
    from hermes.models.capability import Capability
    cap = Capability(
        id="cap-1",
        name="Order Processing",
        version="1.0",
        provides=["orders"],
        keywords=["etsy"],
        owner="jerome-cornet",
        workflow_refs=["process-orders", "sync-inventory"],
    )
    assert cap.workflow_refs == ["process-orders", "sync-inventory"]


def test_capability_workflow_refs_default_empty():
    from hermes.models.capability import Capability
    cap = Capability(id="cap-1", name="Test", version="1.0", provides=[], keywords=[], owner="test")
    assert cap.workflow_refs == []


# ── Config tests ─────────────────────────────────────────────────────────────


def test_config_n8n_url_default():
    from hermes import config
    with patch.dict("os.environ", {}, clear=True):
        assert config.n8n_url() == ""


def test_config_n8n_url_from_env():
    from hermes import config
    with patch.dict("os.environ", {"HERMES_N8N_URL": "http://n8n:5678"}):
        assert config.n8n_url() == "http://n8n:5678"


def test_config_n8n_api_key_default():
    from hermes import config
    with patch.dict("os.environ", {}, clear=True):
        assert config.n8n_api_key() == ""


def test_config_n8n_api_key_from_env():
    from hermes import config
    with patch.dict("os.environ", {"HERMES_N8N_API_KEY": "secret123"}):
        assert config.n8n_api_key() == "secret123"
