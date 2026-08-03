"""Tests for ContextGraph — deterministic context resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.context.context_graph import (
    ContextGraph,
    GraphData,
    SUPPORTED_TYPES,
    ALL_RELATION_KEYS,
    _owner_matches,
)
from hermes.kernel.goal_registry import GoalRegistry
from hermes.kernel.people_registry import PeopleRegistry
from hermes.kernel.department_registry import DepartmentRegistry
from hermes.kernel.capability_registry import CapabilityRegistry
from hermes.kernel.sop_registry import SOPRegistry

_BIZ_ROOT = Path("businesses")
_PEOPLE_ROOT = Path("people")
_DEPTS_ROOT = Path("departments")
_SKILLS_ROOT = Path("skills")


# -- Fixtures ------------------------------------------------------------------


@pytest.fixture
def graph():
    """ContextGraph wired to real registries."""
    return ContextGraph(
        goal_registry=GoalRegistry(businesses_root=_BIZ_ROOT),
        people_registry=PeopleRegistry(people_root=_PEOPLE_ROOT),
        department_registry=DepartmentRegistry(departments_root=_DEPTS_ROOT),
        capability_registry=CapabilityRegistry(skills_root=_SKILLS_ROOT),
        sop_registry=SOPRegistry(skills_root=_SKILLS_ROOT),
    )


@pytest.fixture
def empty_data():
    """GraphData with no per-request data."""
    return GraphData(workspace_id="AVANZIA")


# -- Helpers for mock objects --------------------------------------------------


@dataclass
class _MockOp:
    id: str
    request: str = "test"
    status: str = "executing"
    decision_id: str | None = None
    sop_id: str | None = None
    extra_fields: dict = field(default_factory=dict)


@dataclass
class _MockDecision:
    decision_id: str
    title: str = "Test Decision"
    status: str = "approved"
    decision_date: str = "2026-08"
    goal_id: str | None = None
    owner: str | None = None


@dataclass
class _MockKPI:
    kpi_id: str
    goal_id: str
    name: str = "Test KPI"
    current_value: float = 50.0
    target_value: float = 100.0
    unit: str = "USD"
    status: str = "at_risk"
    owner: str | None = None


@dataclass
class _MockNotification:
    id: str
    title: str = "Test"
    severity: str = "warning"
    category: str = "kpi"
    summary: str = "Test notification"
    related_object_type: str = "kpi"
    related_object_id: str = ""
    acknowledged: bool = False


@dataclass
class _MockHeartbeat:
    id: str
    operation_id: str
    workspace_id: str = "AVANZIA"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "on_track"
    summary: str = "All good"
    author: str = ""
    details: str = ""
    blocker: str = ""
    next_action: str = ""


# -- Resolution tests (one per supported type) ---------------------------------


def test_resolve_goal(graph, empty_data):
    result = graph.resolve("goal", "GOAL-001", empty_data)
    assert result is not None
    assert result["object_type"] == "goal"
    assert result["object_id"] == "GOAL-001"
    assert result["object_summary"]["title"]
    # Goal owned by "Founder" → jerome-cornet via owner_aliases
    people = result["people"]
    assert any(p["id"] == "jerome-cornet" for p in people)


def test_resolve_person(graph, empty_data):
    result = graph.resolve("person", "jerome-cornet", empty_data)
    assert result is not None
    assert result["object_type"] == "person"
    assert result["object_id"] == "jerome-cornet"
    # jerome-cornet owns goals via "Founder" alias
    goals = result["goals"]
    assert len(goals) == 5  # all AVANZIA goals owned by Founder


def test_resolve_department(graph, empty_data):
    result = graph.resolve("department", "technology", empty_data)
    assert result is not None
    assert result["object_type"] == "department"
    # engineering-lead has department_ids: [technology, platform]
    people = result["people"]
    assert any(p["id"] == "engineering-lead" for p in people)


def test_resolve_capability(graph, empty_data):
    result = graph.resolve("capability", "brand-strategy", empty_data)
    assert result is not None
    assert result["object_type"] == "capability"
    # Should have at least a department
    assert "departments" in result


def test_resolve_operation_with_decision_chain(graph):
    decision = _MockDecision(
        decision_id="DEC-001", goal_id="GOAL-001", owner="Founder",
    )
    op = _MockOp(id="OP-001", decision_id="DEC-001")
    data = GraphData(
        workspace_id="AVANZIA",
        operations=[op],
        decisions=[decision],
    )
    result = graph.resolve("operation", "OP-001", data)
    assert result is not None
    # Traces through: Operation → Decision → Goal
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["id"] == "DEC-001"
    assert len(result["goals"]) == 1
    assert result["goals"][0]["id"] == "GOAL-001"


def test_resolve_decision(graph):
    decision = _MockDecision(
        decision_id="DEC-001", goal_id="GOAL-001", owner="Founder",
    )
    data = GraphData(workspace_id="AVANZIA", decisions=[decision])
    result = graph.resolve("decision", "DEC-001", data)
    assert result is not None
    assert len(result["goals"]) == 1
    assert result["goals"][0]["id"] == "GOAL-001"
    assert any(p["id"] == "jerome-cornet" for p in result["people"])


def test_resolve_kpi(graph):
    kpi = _MockKPI(kpi_id="KPI-001", goal_id="GOAL-001", owner="Founder")
    data = GraphData(workspace_id="AVANZIA", kpis=[kpi])
    result = graph.resolve("kpi", "KPI-001", data)
    assert result is not None
    assert len(result["goals"]) == 1
    assert result["goals"][0]["id"] == "GOAL-001"
    assert any(p["id"] == "jerome-cornet" for p in result["people"])


def test_resolve_sop(graph, empty_data):
    # Find an SOP that exists
    sop_reg = SOPRegistry(skills_root=_SKILLS_ROOT)
    sops = sop_reg.list()
    if not sops:
        pytest.skip("No SOPs available")
    sop = sops[0]
    result = graph.resolve("sop", sop.id, empty_data)
    assert result is not None
    assert result["object_type"] == "sop"


# -- Edge cases ----------------------------------------------------------------


def test_resolve_unknown_id_returns_none(graph, empty_data):
    result = graph.resolve("goal", "NONEXISTENT", empty_data)
    assert result is None


def test_resolve_unknown_type_raises(graph, empty_data):
    with pytest.raises(ValueError, match="Unknown object type"):
        graph.resolve("spaceship", "id-1", empty_data)


def test_resolve_empty_relations(graph, empty_data):
    result = graph.resolve("goal", "GOAL-001", empty_data)
    assert result is not None
    # No KPIs, decisions, operations in empty_data
    assert result["kpis"] == []
    assert result["decisions"] == []
    assert result["operations"] == []


def test_operation_without_decision_has_empty_goals(graph):
    op = _MockOp(id="OP-SOLO", decision_id=None)
    data = GraphData(workspace_id="AVANZIA", operations=[op])
    result = graph.resolve("operation", "OP-SOLO", data)
    assert result is not None
    assert result["goals"] == []
    assert result["decisions"] == []


# -- Cross-reference accuracy --------------------------------------------------


def test_goal_context_includes_all_matching_kpis(graph):
    kpis = [
        _MockKPI(kpi_id="K1", goal_id="GOAL-001"),
        _MockKPI(kpi_id="K2", goal_id="GOAL-001"),
        _MockKPI(kpi_id="K3", goal_id="GOAL-002"),
    ]
    data = GraphData(workspace_id="AVANZIA", kpis=kpis)
    result = graph.resolve("goal", "GOAL-001", data)
    kpi_ids = [k["id"] for k in result["kpis"]]
    assert "K1" in kpi_ids
    assert "K2" in kpi_ids
    assert "K3" not in kpi_ids


def test_person_departments_include_member_and_owned(graph, empty_data):
    result = graph.resolve("person", "jerome-cornet", empty_data)
    dept_ids = [d["id"] for d in result["departments"]]
    # jerome-cornet has department_ids: ["business"]
    assert "business" in dept_ids


def test_department_includes_all_member_people(graph, empty_data):
    result = graph.resolve("department", "technology", empty_data)
    person_ids = [p["id"] for p in result["people"]]
    assert "engineering-lead" in person_ids


def test_operation_traces_through_decision_to_goal(graph):
    """Full chain: Operation → Decision → Goal."""
    dec = _MockDecision(decision_id="D1", goal_id="GOAL-003")
    op = _MockOp(id="OP-X", decision_id="D1")
    data = GraphData(
        workspace_id="AVANZIA", operations=[op], decisions=[dec],
    )
    result = graph.resolve("operation", "OP-X", data)
    assert result["goals"][0]["id"] == "GOAL-003"
    assert result["decisions"][0]["id"] == "D1"


# -- Ownership resolution -----------------------------------------------------


def test_owner_matches_person_id():
    person = MagicMock()
    person.id = "jerome-cornet"
    person.owner_aliases = []
    assert _owner_matches("jerome-cornet", person)
    assert _owner_matches("Jerome-Cornet", person)


def test_owner_matches_alias():
    person = MagicMock()
    person.id = "jerome-cornet"
    person.owner_aliases = ["Founder", "CEO"]
    assert _owner_matches("Founder", person)
    assert _owner_matches("ceo", person)


def test_owner_no_match():
    person = MagicMock()
    person.id = "jerome-cornet"
    person.owner_aliases = ["Founder"]
    assert not _owner_matches("Marketing Lead", person)


def test_owner_matches_none_returns_false():
    person = MagicMock()
    person.id = "someone"
    person.owner_aliases = []
    assert not _owner_matches(None, person)
    assert not _owner_matches("", person)


# -- Attention summary (Amendment 3) -------------------------------------------


def test_attention_summary_counts_critical(graph):
    notif = _MockNotification(
        id="n1", severity="critical", related_object_id="KPI-001",
    )
    kpi = _MockKPI(kpi_id="KPI-001", goal_id="GOAL-001")
    data = GraphData(
        workspace_id="AVANZIA", kpis=[kpi], notifications=[notif],
    )
    result = graph.resolve("goal", "GOAL-001", data)
    assert result["attention"]["critical"] == 1


def test_attention_summary_counts_warnings(graph):
    notifs = [
        _MockNotification(id="n1", severity="warning", related_object_id="KPI-A"),
        _MockNotification(id="n2", severity="warning", related_object_id="KPI-B"),
    ]
    kpis = [
        _MockKPI(kpi_id="KPI-A", goal_id="GOAL-001"),
        _MockKPI(kpi_id="KPI-B", goal_id="GOAL-001"),
    ]
    data = GraphData(
        workspace_id="AVANZIA", kpis=kpis, notifications=notifs,
    )
    result = graph.resolve("goal", "GOAL-001", data)
    assert result["attention"]["warning"] == 2


def test_attention_summary_counts_blocked(graph):
    op = _MockOp(id="OP-BLK", decision_id="D1")
    dec = _MockDecision(decision_id="D1", goal_id="GOAL-001")
    hb = _MockHeartbeat(id="hb1", operation_id="OP-BLK", status="blocked")

    store = MagicMock()
    store.list_by_operation.return_value = [hb]

    data = GraphData(
        workspace_id="AVANZIA",
        operations=[op],
        decisions=[dec],
        heartbeat_store=store,
    )
    result = graph.resolve("goal", "GOAL-001", data)
    assert result["attention"]["blocked"] == 1


def test_attention_summary_zero_when_clean(graph, empty_data):
    result = graph.resolve("goal", "GOAL-001", empty_data)
    assert result["attention"] == {"critical": 0, "warning": 0, "blocked": 0}


# -- Recursion guard (Amendment 4) ---------------------------------------------


def test_recursion_guard_prevents_cycle(graph, empty_data):
    visited = {("goal", "GOAL-001")}
    result = graph.resolve("goal", "GOAL-001", empty_data, _visited=visited)
    assert result is None


def test_recursion_guard_allows_different_objects(graph, empty_data):
    visited = {("goal", "GOAL-002")}
    result = graph.resolve("goal", "GOAL-001", empty_data, _visited=visited)
    assert result is not None


# -- Response shape ------------------------------------------------------------


def test_response_contains_all_relation_keys(graph, empty_data):
    result = graph.resolve("goal", "GOAL-001", empty_data)
    for key in ALL_RELATION_KEYS:
        assert key in result, f"Missing relation key: {key}"
        assert isinstance(result[key], list)


def test_response_has_object_summary(graph, empty_data):
    result = graph.resolve("goal", "GOAL-001", empty_data)
    assert "object_summary" in result
    assert result["object_summary"]["id"] == "GOAL-001"


def test_response_has_attention(graph, empty_data):
    result = graph.resolve("goal", "GOAL-001", empty_data)
    assert "attention" in result
    assert set(result["attention"].keys()) == {"critical", "warning", "blocked"}


# -- Supported types -----------------------------------------------------------


def test_supported_types_includes_all_eight():
    expected = {"goal", "person", "department", "capability",
                "operation", "decision", "kpi", "sop"}
    assert SUPPORTED_TYPES == expected
