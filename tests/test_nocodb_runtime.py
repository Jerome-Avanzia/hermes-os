"""Tests for NocoDB Runtime — Sprint 44.

Covers: Database model, Table model, ColumnSummary, DataProvider ABC,
NocodbProvider, NocodbRuntime, Gateway endpoints, Context Graph integration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermes.models.database import Database, compute_database_health, DATABASE_HEALTH_STATES
from hermes.models.table import (
    ColumnSummary,
    Table,
    compute_table_attention,
    TABLE_ATTENTION_STATES,
)
from hermes.runtime.data_provider import DataProvider
from hermes.runtime.nocodb_provider import NocodbProvider
from hermes.runtime.nocodb_runtime import (
    NocodbHealthStatus,
    NocodbRuntime,
    _slugify,
)


# ── Database model tests ─────────────────────────────────────────────────────


def test_database_defaults():
    db = Database(id="main", name="Main")
    assert db.id == "main"
    assert db.name == "Main"
    assert db.provider_id == ""
    assert db.provider == "nocodb"
    assert db.table_count == 0
    assert db.record_count == 0
    assert db.health_state == "unknown"


def test_database_with_all_fields():
    db = Database(
        id="main-store",
        name="Main Store",
        provider_id="p_abc123",
        provider="nocodb",
        table_count=5,
        record_count=1000,
        health_state="healthy",
    )
    assert db.table_count == 5
    assert db.record_count == 1000
    assert db.health_state == "healthy"


def test_database_health_states_valid():
    assert "healthy" in DATABASE_HEALTH_STATES
    assert "degraded" in DATABASE_HEALTH_STATES
    assert "unknown" in DATABASE_HEALTH_STATES


# ── compute_database_health tests ────────────────────────────────────────────


def test_database_health_with_tables():
    assert compute_database_health(3) == "healthy"


def test_database_health_zero_tables():
    assert compute_database_health(0) == "degraded"


def test_database_health_negative():
    assert compute_database_health(-1) == "unknown"


def test_database_health_one_table():
    assert compute_database_health(1) == "healthy"


# ── Table model tests ────────────────────────────────────────────────────────


def test_table_defaults():
    t = Table(id="main--orders", name="Orders")
    assert t.id == "main--orders"
    assert t.name == "Orders"
    assert t.provider_id == ""
    assert t.database_id == ""
    assert t.record_count == 0
    assert t.column_count == 0
    assert t.columns == []
    assert t.primary_key == ""
    assert t.last_updated == ""
    assert t.attention_state == "ok"


def test_table_with_all_fields():
    cols = [
        ColumnSummary(name="id", type="number", nullable=False, primary=True),
        ColumnSummary(name="name", type="text"),
    ]
    t = Table(
        id="main--orders",
        name="Orders",
        provider_id="tbl_123",
        database_id="main",
        record_count=42,
        column_count=2,
        columns=cols,
        primary_key="id",
        last_updated="2026-08-01T10:00:00Z",
        attention_state="ok",
    )
    assert t.record_count == 42
    assert t.column_count == 2
    assert len(t.columns) == 2
    assert t.columns[0].primary is True


def test_table_attention_states_valid():
    assert "ok" in TABLE_ATTENTION_STATES
    assert "warning" in TABLE_ATTENTION_STATES
    assert "critical" in TABLE_ATTENTION_STATES


# ── ColumnSummary tests (Amendment 5) ────────────────────────────────────────


def test_column_summary_defaults():
    col = ColumnSummary(name="title")
    assert col.name == "title"
    assert col.type == ""
    assert col.nullable is True
    assert col.primary is False


def test_column_summary_with_all_fields():
    col = ColumnSummary(name="id", type="number", nullable=False, primary=True)
    assert col.name == "id"
    assert col.type == "number"
    assert col.nullable is False
    assert col.primary is True


# ── compute_table_attention tests (Amendment 3) ──────────────────────────────


def test_attention_no_columns_is_critical():
    assert compute_table_attention(10, 0, "id") == "critical"


def test_attention_zero_records_is_warning():
    assert compute_table_attention(0, 3, "id") == "warning"


def test_attention_no_primary_key_is_warning():
    assert compute_table_attention(10, 3, "") == "warning"


def test_attention_all_good_is_ok():
    assert compute_table_attention(10, 3, "id") == "ok"


def test_attention_zero_records_no_pk_is_warning():
    assert compute_table_attention(0, 3, "") == "warning"


def test_attention_no_columns_overrides_warning():
    """Critical (no columns) takes priority over warning (no records)."""
    assert compute_table_attention(0, 0, "") == "critical"


# ── _slugify tests ───────────────────────────────────────────────────────────


def test_slugify_basic():
    assert _slugify("Main Database") == "main-database"


def test_slugify_special_chars():
    assert _slugify("My DB (prod)") == "my-db-prod"


def test_slugify_empty():
    assert _slugify("") == "unnamed"


def test_slugify_numbers():
    assert _slugify("Store v2") == "store-v2"


# ── DataProvider ABC tests ───────────────────────────────────────────────────


def test_data_provider_is_abstract():
    """DataProvider cannot be instantiated directly."""
    with pytest.raises(TypeError):
        DataProvider()


def test_nocodb_provider_is_data_provider():
    """NocodbProvider implements DataProvider."""
    p = NocodbProvider(api_url="http://localhost", api_token="key")
    assert isinstance(p, DataProvider)


def test_data_provider_name_property():
    p = NocodbProvider(api_url="http://localhost", api_token="key")
    assert p.name == "nocodb"


# ── NocodbProvider tests ─────────────────────────────────────────────────────


def test_provider_not_configured_without_url():
    p = NocodbProvider(api_url="", api_token="key")
    assert p.configured is False


def test_provider_not_configured_without_token():
    p = NocodbProvider(api_url="http://localhost:8080", api_token="")
    assert p.configured is False


def test_provider_configured():
    p = NocodbProvider(api_url="http://localhost:8080", api_token="secret")
    assert p.configured is True


def test_provider_url_strips_trailing_slash():
    p = NocodbProvider(api_url="http://localhost:8080/", api_token="key")
    assert p._api_url == "http://localhost:8080"


def test_provider_health_unconfigured():
    p = NocodbProvider(api_url="", api_token="")
    result = p.health()
    assert result["configured"] is False
    assert result["authenticated"] is False
    assert result["reachable"] is False


def test_provider_list_bases_unconfigured():
    p = NocodbProvider(api_url="", api_token="")
    assert p.list_bases() == []


def test_provider_get_base_unconfigured():
    p = NocodbProvider(api_url="", api_token="")
    assert p.get_base("abc") is None


def test_provider_list_tables_unconfigured():
    p = NocodbProvider(api_url="", api_token="")
    assert p.list_tables("abc") == []


def test_provider_get_table_unconfigured():
    p = NocodbProvider(api_url="", api_token="")
    assert p.get_table("abc") is None


# ── NocodbHealthStatus tests ─────────────────────────────────────────────────


def test_health_status_defaults():
    h = NocodbHealthStatus()
    assert h.configured is False
    assert h.authenticated is False
    assert h.reachable is False
    assert h.database_count == 0
    assert h.table_count == 0
    assert h.record_count == 0
    assert h.last_sync == ""
    assert h.refresh_duration_ms == 0


def test_health_status_with_values():
    h = NocodbHealthStatus(
        configured=True,
        authenticated=True,
        reachable=True,
        database_count=3,
        table_count=12,
        record_count=1500,
        last_sync="2026-08-01T00:00:00Z",
        refresh_duration_ms=42,
    )
    assert h.database_count == 3
    assert h.table_count == 12
    assert h.record_count == 1500


# ── NocodbRuntime tests ──────────────────────────────────────────────────────


def _sample_base(base_id="p_abc", title="Main Store"):
    return {"id": base_id, "title": title}


def _sample_table(
    table_id="tbl_1",
    title="Orders",
    columns=None,
    meta=None,
):
    if columns is None:
        columns = [
            {"title": "Id", "uidt": "Number", "pv": True},
            {"title": "Name", "uidt": "SingleLineText"},
            {"title": "Amount", "uidt": "Number"},
        ]
    if meta is None:
        meta = {"rows": 42}
    return {
        "id": table_id,
        "title": title,
        "columns": columns,
        "meta": meta,
        "updated_at": "2026-08-01T10:00:00Z",
    }


def _make_runtime(bases=None, tables_by_base=None, health_response=None):
    """Create a NocodbRuntime with a mocked DataProvider."""
    provider = MagicMock(spec=NocodbProvider)
    provider.name = "nocodb"
    provider.configured = True
    provider.list_bases.return_value = bases or []
    provider.health.return_value = health_response or {
        "configured": True,
        "authenticated": True,
        "reachable": True,
        "database_count": len(bases or []),
    }

    # Default: same tables for all bases
    if tables_by_base is None:
        provider.list_tables.return_value = []
    else:
        def _list_tables(base_id):
            return tables_by_base.get(base_id, [])
        provider.list_tables.side_effect = _list_tables

    return NocodbRuntime(provider)


def test_runtime_not_configured():
    provider = MagicMock(spec=NocodbProvider)
    provider.configured = False
    runtime = NocodbRuntime(provider)
    assert runtime.configured is False
    assert runtime.list_databases() == []


def test_runtime_configured():
    runtime = _make_runtime()
    assert runtime.configured is True


def test_runtime_list_databases_empty():
    runtime = _make_runtime(bases=[])
    assert runtime.list_databases() == []


def test_runtime_list_databases_single():
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    databases = runtime.list_databases()
    assert len(databases) == 1
    db = databases[0]
    assert isinstance(db, Database)
    assert db.name == "Main Store"
    assert db.id == "main-store"  # Amendment 2: slugified
    assert db.provider_id == "p_abc"  # Amendment 2: preserved
    assert db.table_count == 1
    assert db.record_count == 42
    assert db.health_state == "healthy"


def test_runtime_hermes_id_is_slugified():
    runtime = _make_runtime(bases=[_sample_base(title="My Cool DB")])
    db = runtime.list_databases()[0]
    assert db.id == "my-cool-db"


def test_runtime_provider_id_preserved():
    runtime = _make_runtime(bases=[_sample_base(base_id="p_xyz")])
    db = runtime.list_databases()[0]
    assert db.provider_id == "p_xyz"


def test_runtime_database_health_degraded():
    """Empty database (no tables) gets degraded health."""
    runtime = _make_runtime(
        bases=[_sample_base()],
        tables_by_base={"p_abc": []},
    )
    db = runtime.list_databases()[0]
    assert db.health_state == "degraded"


def test_runtime_get_database_found():
    runtime = _make_runtime(
        bases=[_sample_base()],
        tables_by_base={"p_abc": [_sample_table()]},
    )
    db = runtime.get_database("main-store")
    assert db is not None
    assert db.id == "main-store"


def test_runtime_get_database_not_found():
    runtime = _make_runtime(bases=[_sample_base()])
    db = runtime.get_database("nonexistent")
    assert db is None


def test_runtime_get_database_not_configured():
    provider = MagicMock(spec=NocodbProvider)
    provider.configured = False
    runtime = NocodbRuntime(provider)
    assert runtime.get_database("anything") is None


def test_runtime_list_tables():
    bases = [_sample_base()]
    tables = [_sample_table(), _sample_table(table_id="tbl_2", title="Products")]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    result = runtime.list_tables()
    assert len(result) == 2
    assert all(isinstance(t, Table) for t in result)


def test_runtime_table_id_scoped_to_database():
    """Table IDs include parent database slug."""
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    result = runtime.list_tables()
    assert result[0].id == "main-store--orders"


def test_runtime_table_columns_are_column_summary():
    """Amendment 5: Columns are ColumnSummary instances."""
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert len(tbl.columns) == 3
    assert isinstance(tbl.columns[0], ColumnSummary)
    assert tbl.columns[0].name == "Id"
    assert tbl.columns[0].type == "Number"
    assert tbl.columns[0].primary is True


def test_runtime_table_primary_key():
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert tbl.primary_key == "Id"


def test_runtime_table_attention_ok():
    """Table with records, columns, and PK is ok."""
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert tbl.attention_state == "ok"


def test_runtime_table_attention_warning_empty():
    """Amendment 3: Empty table gets warning."""
    bases = [_sample_base()]
    tables = [_sample_table(meta={"rows": 0})]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert tbl.attention_state == "warning"


def test_runtime_table_attention_critical_no_columns():
    """Amendment 3: Table with no columns gets critical."""
    bases = [_sample_base()]
    tables = [_sample_table(columns=[])]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert tbl.attention_state == "critical"


def test_runtime_table_attention_warning_no_pk():
    """Amendment 3: Table without primary key gets warning."""
    bases = [_sample_base()]
    cols = [{"title": "Name", "uidt": "SingleLineText"}]
    tables = [_sample_table(columns=cols)]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert tbl.attention_state == "warning"


def test_runtime_get_table_found():
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.get_table("main-store--orders")
    assert tbl is not None
    assert tbl.id == "main-store--orders"


def test_runtime_get_table_not_found():
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.get_table("nonexistent--whatever")
    assert tbl is None


def test_runtime_multiple_databases():
    bases = [
        _sample_base(base_id="p_1", title="Store"),
        _sample_base(base_id="p_2", title="Analytics"),
    ]
    runtime = _make_runtime(
        bases=bases,
        tables_by_base={
            "p_1": [_sample_table(table_id="t1", title="Orders")],
            "p_2": [_sample_table(table_id="t2", title="Events")],
        },
    )
    databases = runtime.list_databases()
    assert len(databases) == 2
    ids = [db.id for db in databases]
    assert "store" in ids
    assert "analytics" in ids

    all_tables = runtime.list_tables()
    assert len(all_tables) == 2
    table_ids = [t.id for t in all_tables]
    assert "store--orders" in table_ids
    assert "analytics--events" in table_ids


def test_runtime_health_includes_sync_metadata():
    runtime = _make_runtime(bases=[])
    h = runtime.health()
    assert isinstance(h, NocodbHealthStatus)
    assert h.last_sync != ""
    assert isinstance(h.refresh_duration_ms, int)


def test_runtime_health_counts_databases_and_tables():
    bases = [_sample_base()]
    tables = [_sample_table(), _sample_table(table_id="tbl_2", title="Products")]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    h = runtime.health()
    assert h.database_count == 1
    assert h.table_count == 2
    assert h.record_count == 84  # 42 * 2


def test_runtime_empty_name_uses_provider_id():
    bases = [{"id": "p_99", "title": ""}]
    runtime = _make_runtime(bases=bases)
    db = runtime.list_databases()[0]
    assert db.id == "p_99"


def test_runtime_table_timestamps():
    bases = [_sample_base()]
    tables = [_sample_table()]
    runtime = _make_runtime(bases=bases, tables_by_base={"p_abc": tables})
    tbl = runtime.list_tables()[0]
    assert tbl.last_updated == "2026-08-01T10:00:00Z"


# ── Gateway endpoint tests ───────────────────────────────────────────────────


@pytest.fixture
def gateway_client():
    from hermes.gateway.app import app
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("starlette test client not available")
    return TestClient(app, raise_server_exceptions=False)


def test_gateway_list_databases(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.list_databases.return_value = [
            {"id": "main", "name": "Main", "provider_id": "p_1",
             "provider": "nocodb", "table_count": 3, "record_count": 100,
             "health_state": "healthy"},
        ]
        resp = gateway_client.get("/v1/databases")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "main"
        assert data[0]["provider_id"] == "p_1"


def test_gateway_get_database_found(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.get_database.return_value = {
            "id": "main", "name": "Main", "provider_id": "p_1",
            "provider": "nocodb", "table_count": 1, "record_count": 42,
            "health_state": "healthy", "tables": [],
        }
        resp = gateway_client.get("/v1/databases/main")
        assert resp.status_code == 200
        assert resp.json()["id"] == "main"


def test_gateway_get_database_not_found(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.get_database.return_value = None
        resp = gateway_client.get("/v1/databases/nonexistent")
        assert resp.status_code == 404


def test_gateway_list_tables(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.list_tables.return_value = [
            {"id": "main--orders", "name": "Orders", "provider_id": "tbl_1",
             "database_id": "main", "record_count": 42, "column_count": 3,
             "columns": [], "primary_key": "Id", "last_updated": "",
             "attention_state": "ok"},
        ]
        resp = gateway_client.get("/v1/tables")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "main--orders"


def test_gateway_get_table_found(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.get_table.return_value = {
            "id": "main--orders", "name": "Orders", "provider_id": "tbl_1",
            "database_id": "main", "record_count": 42, "column_count": 3,
            "columns": [{"name": "Id", "type": "Number", "nullable": False, "primary": True}],
            "primary_key": "Id", "last_updated": "", "attention_state": "ok",
        }
        resp = gateway_client.get("/v1/tables/main--orders")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "main--orders"
        assert data["columns"][0]["name"] == "Id"


def test_gateway_get_table_not_found(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.get_table.return_value = None
        resp = gateway_client.get("/v1/tables/nonexistent--whatever")
        assert resp.status_code == 404


def test_gateway_nocodb_health(gateway_client):
    with patch("hermes.gateway.app._hermes_service") as mock_svc:
        mock_svc.nocodb_health.return_value = {
            "configured": True, "authenticated": True, "reachable": True,
            "database_count": 3, "table_count": 12, "record_count": 1500,
            "last_sync": "2026-08-01T00:00:00Z", "refresh_duration_ms": 42,
        }
        resp = gateway_client.get("/health/nocodb")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["database_count"] == 3
        assert data["table_count"] == 12
        assert data["refresh_duration_ms"] == 42


# ── Context Graph integration tests ──────────────────────────────────────────


def test_context_graph_database_type_supported():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert "database" in SUPPORTED_TYPES


def test_context_graph_table_type_supported():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert "table" in SUPPORTED_TYPES


def test_context_graph_database_edges_exist():
    from hermes.context.context_graph import _EDGES
    assert "database" in _EDGES
    db_edges = _EDGES["database"]
    expected_keys = {"tables", "capabilities", "workflows", "repositories",
                     "services", "people", "departments", "goals", "notifications"}
    assert expected_keys.issubset(set(db_edges.keys()))


def test_context_graph_table_edges_exist():
    from hermes.context.context_graph import _EDGES
    assert "table" in _EDGES
    tbl_edges = _EDGES["table"]
    expected_keys = {"databases", "capabilities", "workflows", "repositories",
                     "services", "operations", "people", "departments", "goals",
                     "notifications"}
    assert expected_keys.issubset(set(tbl_edges.keys()))


def test_context_graph_existing_types_have_database_edge():
    from hermes.context.context_graph import _EDGES
    for t in ["goal", "person", "department", "capability", "operation",
              "repository", "service", "workflow"]:
        assert "databases" in _EDGES[t], f"{t} should have 'databases' edge"


def test_context_graph_existing_types_have_table_edge():
    from hermes.context.context_graph import _EDGES
    for t in ["goal", "person", "department", "capability", "operation",
              "repository", "service", "workflow"]:
        assert "tables" in _EDGES[t], f"{t} should have 'tables' edge"


def test_context_graph_all_relation_keys_include_databases():
    from hermes.context.context_graph import ALL_RELATION_KEYS
    assert "databases" in ALL_RELATION_KEYS


def test_context_graph_all_relation_keys_include_tables():
    from hermes.context.context_graph import ALL_RELATION_KEYS
    assert "tables" in ALL_RELATION_KEYS


# ── Capability table_refs test ───────────────────────────────────────────────


def test_capability_has_table_refs():
    from hermes.models.capability import Capability
    cap = Capability(
        id="cap-1",
        name="Order Processing",
        version="1.0",
        provides=["orders"],
        keywords=["etsy"],
        owner="jerome-cornet",
        table_refs=["main--orders", "main--products"],
    )
    assert cap.table_refs == ["main--orders", "main--products"]


def test_capability_table_refs_default_empty():
    from hermes.models.capability import Capability
    cap = Capability(id="cap-1", name="Test", version="1.0", provides=[], keywords=[], owner="test")
    assert cap.table_refs == []


# ── Config tests ─────────────────────────────────────────────────────────────


def test_config_nocodb_url_default():
    from hermes import config
    with patch.dict("os.environ", {}, clear=True):
        assert config.nocodb_url() == ""


def test_config_nocodb_url_from_env():
    from hermes import config
    with patch.dict("os.environ", {"HERMES_NOCODB_URL": "http://nocodb:8080"}):
        assert config.nocodb_url() == "http://nocodb:8080"


def test_config_nocodb_token_default():
    from hermes import config
    with patch.dict("os.environ", {}, clear=True):
        assert config.nocodb_token() == ""


def test_config_nocodb_token_from_env():
    from hermes import config
    with patch.dict("os.environ", {"HERMES_NOCODB_TOKEN": "secret123"}):
        assert config.nocodb_token() == "secret123"
