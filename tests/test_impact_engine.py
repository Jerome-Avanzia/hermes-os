"""Tests for Impact Engine — Sprint 46.

Covers: RiskEngine (intrinsic scoring, propagation, boosts, summary),
ImpactEngine (BFS expansion, forward/reverse, cycle detection,
relationship reasons, impact coverage), Gateway endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hermes.context.risk_engine import (
    ESTIMATED_IMPACT_LEVELS,
    ImpactCoverage,
    ImpactedObject,
    ImpactReport,
    ImpactSummary,
    RISK_LEVELS,
    RISK_ORDER,
    RiskEngine,
)
from hermes.context.impact_engine import (
    ImpactEngine,
    PROPAGATING_RELATIONS,
    INFORMATIONAL_RELATIONS,
    _RELATION_TO_TYPE,
    _get_relationship_reason,
)


# ── Data structure tests ─────────────────────────────────────────────────────


def test_risk_levels_valid():
    assert "none" in RISK_LEVELS
    assert "low" in RISK_LEVELS
    assert "medium" in RISK_LEVELS
    assert "high" in RISK_LEVELS
    assert "critical" in RISK_LEVELS


def test_risk_order_monotonic():
    assert RISK_ORDER["none"] < RISK_ORDER["low"]
    assert RISK_ORDER["low"] < RISK_ORDER["medium"]
    assert RISK_ORDER["medium"] < RISK_ORDER["high"]
    assert RISK_ORDER["high"] < RISK_ORDER["critical"]


def test_estimated_impact_levels_valid():
    assert "none" in ESTIMATED_IMPACT_LEVELS
    assert "severe" in ESTIMATED_IMPACT_LEVELS


def test_impacted_object_defaults():
    o = ImpactedObject(
        object_type="goal", object_id="g1", name="G1",
        risk_level="none", risk_reasons=[], depth=0, path=[],
    )
    assert o.relationship_reason == ""


def test_impacted_object_with_reason():
    o = ImpactedObject(
        object_type="capability", object_id="c1", name="C1",
        risk_level="low", risk_reasons=["draft"], depth=1,
        path=["repository:r1"],
        relationship_reason="Capability references repository_refs",
    )
    assert o.relationship_reason == "Capability references repository_refs"
    assert o.depth == 1


def test_impact_coverage_defaults():
    c = ImpactCoverage()
    assert c.types_analyzed == 0
    assert c.types_available == 0
    assert c.objects_analyzed == 0
    assert c.relationships_traversed == 0
    assert c.unknown_references == 0
    assert c.broken_links == []


def test_impact_summary_has_coverage():
    s = ImpactSummary(
        source_type="goal", source_id="g1", source_name="Goal 1",
        estimated_impact="none", affected_goals=[], affected_operations=[],
        affected_people=[], critical_dependencies=0, blocking_risks=[],
        recommended_checks=[], safe_to_proceed=True,
    )
    assert isinstance(s.coverage, ImpactCoverage)


def test_impact_report_direction():
    src = ImpactedObject("goal", "g1", "G1", "none", [], 0, [])
    summary = ImpactSummary("goal", "g1", "G1", "none", [], [], [], 0, [], [], True)
    r = ImpactReport(src, summary, {}, 0, 0, False, "reverse")
    assert r.direction == "reverse"


def test_impact_report_defaults_forward():
    src = ImpactedObject("goal", "g1", "G1", "none", [], 0, [])
    summary = ImpactSummary("goal", "g1", "G1", "none", [], [], [], 0, [], [], True)
    r = ImpactReport(src, summary, {}, 0, 0, False)
    assert r.direction == "forward"


# ── Relation mapping tests ───────────────────────────────────────────────────


def test_propagating_relations_exist():
    assert "goals" in PROPAGATING_RELATIONS
    assert "capabilities" in PROPAGATING_RELATIONS
    assert "services" in PROPAGATING_RELATIONS
    assert "llm_providers" in PROPAGATING_RELATIONS
    assert "models" in PROPAGATING_RELATIONS


def test_informational_relations_exist():
    assert "notifications" in INFORMATIONAL_RELATIONS
    assert "heartbeats" in INFORMATIONAL_RELATIONS
    assert "kpis" in INFORMATIONAL_RELATIONS


def test_propagating_and_informational_disjoint():
    assert PROPAGATING_RELATIONS & INFORMATIONAL_RELATIONS == set()


def test_relation_to_type_complete():
    for rel in PROPAGATING_RELATIONS:
        assert rel in _RELATION_TO_TYPE, f"Missing _RELATION_TO_TYPE entry: {rel}"


# ── Relationship reason tests (Amendment 2) ──────────────────────────────────


def test_relationship_reason_specific():
    reason = _get_relationship_reason("capability", "repositories", "repository")
    assert "repository_refs" in reason


def test_relationship_reason_specific_workflow():
    reason = _get_relationship_reason("capability", "workflows", "workflow")
    assert "workflow_refs" in reason


def test_relationship_reason_specific_llm():
    reason = _get_relationship_reason("llm_provider", "models", "model")
    assert "provider" in reason.lower()


def test_relationship_reason_generic_fallback():
    reason = _get_relationship_reason("unknown_type", "some_relation", "target")
    assert "Unknown Type" in reason
    assert "some relation" in reason


# ── RiskEngine: Intrinsic risk scoring ───────────────────────────────────────


class TestIntrinsicRisk:
    def setup_method(self):
        self.engine = RiskEngine()

    def test_goal_active(self):
        level, _ = self.engine.score_object("goal", {"status": "active"})
        assert level == "low"

    def test_goal_at_risk(self):
        level, reasons = self.engine.score_object("goal", {"status": "at_risk"})
        assert level == "high"
        assert any("at_risk" in r for r in reasons)

    def test_goal_completed(self):
        level, _ = self.engine.score_object("goal", {"status": "completed"})
        assert level == "none"

    def test_goal_blocked(self):
        level, _ = self.engine.score_object("goal", {"status": "blocked"})
        assert level == "medium"

    def test_person_active(self):
        level, _ = self.engine.score_object("person", {"status": "active"})
        assert level == "none"

    def test_person_inactive(self):
        level, reasons = self.engine.score_object("person", {"status": "inactive"})
        assert level == "high"
        assert any("inactive" in r.lower() for r in reasons)

    def test_department_inactive(self):
        level, _ = self.engine.score_object("department", {"status": "inactive"})
        assert level == "high"

    def test_capability_deprecated(self):
        level, _ = self.engine.score_object("capability", {"status": "deprecated"})
        assert level == "critical"

    def test_capability_experimental(self):
        level, _ = self.engine.score_object("capability", {"status": "experimental"})
        assert level == "medium"

    def test_capability_draft(self):
        level, _ = self.engine.score_object("capability", {"status": "draft"})
        assert level == "low"

    def test_capability_active(self):
        level, _ = self.engine.score_object("capability", {"status": "active"})
        assert level == "none"

    def test_operation_failed(self):
        level, _ = self.engine.score_object("operation", {"status": "failed"})
        assert level == "critical"

    def test_operation_executing(self):
        level, _ = self.engine.score_object("operation", {"status": "executing"})
        assert level == "medium"

    def test_operation_created(self):
        level, _ = self.engine.score_object("operation", {"status": "created"})
        assert level == "low"

    def test_operation_completed(self):
        level, _ = self.engine.score_object("operation", {"status": "completed"})
        assert level == "none"

    def test_service_unhealthy(self):
        level, _ = self.engine.score_object("service", {"health": "unhealthy"})
        assert level == "critical"

    def test_service_resource_critical(self):
        level, _ = self.engine.score_object("service", {"resource_state": "critical"})
        assert level == "critical"

    def test_service_resource_elevated(self):
        level, _ = self.engine.score_object("service", {"resource_state": "elevated"})
        assert level == "high"

    def test_service_restarting(self):
        level, _ = self.engine.score_object("service", {"status": "restarting"})
        assert level == "medium"

    def test_service_paused(self):
        level, _ = self.engine.score_object("service", {"status": "paused"})
        assert level == "low"

    def test_service_healthy_running(self):
        level, _ = self.engine.score_object("service", {"health": "healthy", "status": "running"})
        assert level == "none"

    def test_workflow_critical(self):
        level, _ = self.engine.score_object("workflow", {"attention_state": "critical"})
        assert level == "critical"

    def test_workflow_warning(self):
        level, _ = self.engine.score_object("workflow", {"attention_state": "warning"})
        assert level == "medium"

    def test_workflow_inactive(self):
        level, _ = self.engine.score_object("workflow", {"attention_state": "ok", "status": "inactive"})
        assert level == "low"

    def test_workflow_ok(self):
        level, _ = self.engine.score_object("workflow", {"attention_state": "ok", "status": "active"})
        assert level == "none"

    def test_database_degraded(self):
        level, _ = self.engine.score_object("database", {"health_state": "degraded"})
        assert level == "high"

    def test_database_unknown(self):
        level, _ = self.engine.score_object("database", {"health_state": "unknown"})
        assert level == "medium"

    def test_database_healthy(self):
        level, _ = self.engine.score_object("database", {"health_state": "healthy"})
        assert level == "none"

    def test_table_critical(self):
        level, _ = self.engine.score_object("table", {"attention_state": "critical"})
        assert level == "critical"

    def test_table_warning(self):
        level, _ = self.engine.score_object("table", {"attention_state": "warning"})
        assert level == "medium"

    def test_table_ok(self):
        level, _ = self.engine.score_object("table", {"attention_state": "ok"})
        assert level == "none"

    def test_llm_provider_unreachable(self):
        level, _ = self.engine.score_object("llm_provider", {"health_state": "unreachable"})
        assert level == "critical"

    def test_llm_provider_degraded(self):
        level, _ = self.engine.score_object("llm_provider", {"health_state": "degraded"})
        assert level == "high"

    def test_llm_provider_unconfigured(self):
        level, _ = self.engine.score_object("llm_provider", {"health_state": "unconfigured"})
        assert level == "medium"

    def test_llm_provider_healthy(self):
        level, _ = self.engine.score_object("llm_provider", {"health_state": "healthy"})
        assert level == "none"

    def test_model_critical(self):
        level, _ = self.engine.score_object("model", {"attention_state": "critical"})
        assert level == "critical"

    def test_model_warning(self):
        level, _ = self.engine.score_object("model", {"attention_state": "warning"})
        assert level == "medium"

    def test_model_ok(self):
        level, _ = self.engine.score_object("model", {"attention_state": "ok"})
        assert level == "none"

    def test_repository_always_none(self):
        level, _ = self.engine.score_object("repository", {})
        assert level == "none"

    def test_decision_proposed(self):
        level, _ = self.engine.score_object("decision", {"status": "proposed"})
        assert level == "low"

    def test_kpi_off_track(self):
        level, _ = self.engine.score_object("kpi", {"status": "off_track"})
        assert level == "high"

    def test_kpi_at_risk(self):
        level, _ = self.engine.score_object("kpi", {"status": "at_risk"})
        assert level == "medium"

    def test_sop_deprecated(self):
        level, _ = self.engine.score_object("sop", {"status": "deprecated"})
        assert level == "high"

    def test_unknown_type(self):
        level, _ = self.engine.score_object("imaginary", {})
        assert level == "none"


# ── RiskEngine: Informational boosts ─────────────────────────────────────────


class TestRiskBoosts:
    def setup_method(self):
        self.engine = RiskEngine()

    def test_critical_notification_boosts(self):
        ctx = {"notifications": [{"severity": "critical"}]}
        level, reasons = self.engine.score_object("repository", {}, ctx)
        assert level == "low"  # none + 1 boost = low
        assert any("critical notification" in r for r in reasons)

    def test_blocked_heartbeat_boosts(self):
        ctx = {"heartbeats": [{"status": "blocked"}]}
        level, _ = self.engine.score_object("repository", {}, ctx)
        assert level == "low"  # none + 1 boost

    def test_off_track_kpi_boosts(self):
        ctx = {"kpis": [{"status": "off_track"}]}
        level, _ = self.engine.score_object("repository", {}, ctx)
        assert level == "low"

    def test_multiple_boosts_stack(self):
        ctx = {
            "notifications": [{"severity": "critical"}],
            "heartbeats": [{"status": "blocked"}],
        }
        level, _ = self.engine.score_object("repository", {}, ctx)
        assert level == "medium"  # none + 1 + 1 = medium

    def test_boost_caps_at_critical(self):
        ctx = {
            "notifications": [{"severity": "critical"}, {"severity": "critical"}],
            "heartbeats": [{"status": "blocked"}],
            "kpis": [{"status": "off_track"}],
        }
        # Start at high (service elevated) → cap at critical
        level, _ = self.engine.score_object(
            "service", {"resource_state": "elevated"}, ctx,
        )
        assert level == "critical"

    def test_no_boost_without_context(self):
        level, reasons = self.engine.score_object("repository", {})
        assert level == "none"
        assert reasons == []


# ── RiskEngine: Risk propagation ─────────────────────────────────────────────


class TestRiskPropagation:
    def setup_method(self):
        self.engine = RiskEngine()

    def test_no_dependencies(self):
        result = self.engine.propagate_risk("low", [])
        assert result == "low"

    def test_dependency_attenuated(self):
        # critical dep → attenuated to high → max(none, high) = high
        result = self.engine.propagate_risk("none", ["critical"])
        assert result == "high"

    def test_intrinsic_wins_over_attenuated(self):
        # intrinsic critical vs dep critical(attenuated=high) → critical
        result = self.engine.propagate_risk("critical", ["critical"])
        assert result == "critical"

    def test_dependency_low_attenuated_to_none(self):
        # low dep → attenuated to none → max(none, none) = none
        result = self.engine.propagate_risk("none", ["low"])
        assert result == "none"

    def test_multiple_deps_max_wins(self):
        result = self.engine.propagate_risk("none", ["low", "high", "medium"])
        # high attenuated = medium, medium attenuated = low, low att = none
        assert result == "medium"

    def test_none_dep_no_effect(self):
        result = self.engine.propagate_risk("low", ["none"])
        assert result == "low"


# ── RiskEngine: Executive summary ────────────────────────────────────────────


class TestExecutiveSummary:
    def setup_method(self):
        self.engine = RiskEngine()

    def _make_source(self):
        return ImpactedObject("repository", "r1", "Repo", "none", [], 0, [])

    def test_empty_affected(self):
        s = self.engine.generate_summary(self._make_source(), {}, ImpactCoverage())
        assert s.estimated_impact == "none"
        assert s.safe_to_proceed is True
        assert s.critical_dependencies == 0

    def test_safe_when_low_risk(self):
        affected = {"capability": [
            ImpactedObject("capability", "c1", "C1", "low", ["draft"], 1, ["repository:r1"]),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.safe_to_proceed is True
        assert s.estimated_impact == "low"

    def test_unsafe_when_high_risk(self):
        affected = {"service": [
            ImpactedObject("service", "s1", "Svc", "high", ["elevated"], 1, ["repository:r1"]),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.safe_to_proceed is False
        assert len(s.blocking_risks) == 1

    def test_unsafe_when_critical_risk(self):
        affected = {"service": [
            ImpactedObject("service", "s1", "Svc", "critical", ["unhealthy"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.safe_to_proceed is False
        assert s.critical_dependencies == 1

    def test_severe_impact_with_critical(self):
        affected = {"service": [
            ImpactedObject("service", "s1", "Svc", "critical", ["unhealthy"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.estimated_impact == "severe"

    def test_severe_impact_with_high_goal(self):
        affected = {"goal": [
            ImpactedObject("goal", "g1", "Goal", "high", ["at_risk"], 2, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.estimated_impact == "severe"

    def test_significant_impact_with_high(self):
        affected = {"service": [
            ImpactedObject("service", "s1", "Svc", "high", ["elevated"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.estimated_impact == "significant"

    def test_moderate_impact_with_medium(self):
        affected = {"workflow": [
            ImpactedObject("workflow", "w1", "WF", "medium", ["warning"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.estimated_impact == "moderate"

    def test_affected_goals_collected(self):
        affected = {"goal": [
            ImpactedObject("goal", "g1", "G1", "low", [], 1, []),
            ImpactedObject("goal", "g2", "G2", "low", [], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.affected_goals == ["g1", "g2"]

    def test_affected_operations_collected(self):
        affected = {"operation": [
            ImpactedObject("operation", "op1", "Op", "none", [], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.affected_operations == ["op1"]

    def test_affected_people_collected(self):
        affected = {"person": [
            ImpactedObject("person", "p1", "Alice", "none", [], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert s.affected_people == ["p1"]

    def test_recommended_check_infrastructure(self):
        affected = {"service": [
            ImpactedObject("service", "s1", "Svc", "medium", ["restarting"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert any("infrastructure" in c.lower() for c in s.recommended_checks)

    def test_recommended_check_automation(self):
        affected = {"workflow": [
            ImpactedObject("workflow", "w1", "WF", "medium", ["warning"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert any("automation" in c.lower() for c in s.recommended_checks)

    def test_recommended_check_data(self):
        affected = {"database": [
            ImpactedObject("database", "d1", "DB", "medium", ["unknown"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert any("data" in c.lower() for c in s.recommended_checks)

    def test_recommended_check_ai(self):
        affected = {"llm_provider": [
            ImpactedObject("llm_provider", "lp1", "LP", "medium", ["unconfigured"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert any("ai" in c.lower() for c in s.recommended_checks)

    def test_recommended_check_goals(self):
        affected = {"goal": [
            ImpactedObject("goal", "g1", "G1", "medium", ["blocked"], 2, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert any("goal" in c.lower() for c in s.recommended_checks)

    def test_recommended_check_operations(self):
        affected = {"operation": [
            ImpactedObject("operation", "op1", "Op", "medium", ["executing"], 1, []),
        ]}
        s = self.engine.generate_summary(self._make_source(), affected, ImpactCoverage())
        assert any("operation" in c.lower() for c in s.recommended_checks)

    def test_coverage_in_summary(self):
        cov = ImpactCoverage(types_analyzed=5, types_available=15, objects_analyzed=10)
        s = self.engine.generate_summary(self._make_source(), {}, cov)
        assert s.coverage.types_analyzed == 5
        assert s.coverage.types_available == 15
        assert s.coverage.objects_analyzed == 10


# ── ImpactEngine tests ──────────────────────────────────────────────────────


class _MockContextGraph:
    """Mock ContextGraph that returns pre-configured context results."""

    def __init__(self, results: dict[tuple[str, str], dict | None] | None = None):
        self._results = results or {}

    def resolve(self, object_type, object_id, data, _visited=None):
        return self._results.get((object_type, object_id))


class TestImpactEngine:
    def test_source_not_found(self):
        graph = _MockContextGraph()
        engine = ImpactEngine(graph)
        data = MagicMock()
        report = engine.analyze("goal", "nonexistent", data)
        assert report.total_affected == 0
        assert report.source.risk_reasons == ["Object not found"]
        assert report.direction == "forward"

    def test_source_found_no_deps(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock())
        assert report.source.object_id == "r1"
        assert report.source.name == "Repo1"
        assert report.total_affected == 0
        assert report.summary.safe_to_proceed is True
        assert report.summary.estimated_impact == "none"

    def test_single_depth_expansion(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "Cap1", "status": "active"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=1)
        assert report.total_affected == 1
        assert "capability" in report.affected
        assert report.affected["capability"][0].object_id == "c1"
        assert report.affected["capability"][0].depth == 1
        assert report.affected["capability"][0].path == ["repository:r1"]

    def test_multi_depth_expansion(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "Cap1", "status": "active"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "goals": [{"id": "g1", "title": "Goal1", "status": "active"}],
            },
            ("goal", "g1"): {
                "object_type": "goal",
                "object_id": "g1",
                "object_summary": {"id": "g1", "title": "Goal1", "status": "active", "owner": "x"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=3)
        assert report.total_affected == 2
        assert "capability" in report.affected
        assert "goal" in report.affected
        goal = report.affected["goal"][0]
        assert goal.depth == 2
        assert goal.path == ["repository:r1", "capability:c1"]

    def test_max_depth_respected(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "Cap1", "status": "active"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "goals": [{"id": "g1", "title": "G1", "status": "active"}],
            },
            ("goal", "g1"): {
                "object_type": "goal",
                "object_id": "g1",
                "object_summary": {"id": "g1", "title": "G1", "status": "active", "owner": "x"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=1)
        assert report.total_affected == 1  # only capability at depth 1
        assert "goal" not in report.affected

    def test_cycle_detection(self):
        """A→B→A should detect cycle and terminate."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "Cap1", "status": "active"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "repositories": [{"id": "r1", "name": "Repo1"}],  # cycle back to r1
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=3)
        assert report.cycle_detected is True
        assert report.total_affected == 1  # c1 only, r1 not re-expanded

    def test_visited_prevents_duplicates(self):
        """Two paths to the same node should only visit it once."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [
                    {"id": "c1", "name": "Cap1", "status": "active"},
                    {"id": "c2", "name": "Cap2", "status": "active"},
                ],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "people": [{"id": "p1", "name": "Alice", "status": "active"}],
            },
            ("capability", "c2"): {
                "object_type": "capability",
                "object_id": "c2",
                "object_summary": {"id": "c2", "name": "Cap2", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "people": [{"id": "p1", "name": "Alice", "status": "active"}],
            },
            ("person", "p1"): {
                "object_type": "person",
                "object_id": "p1",
                "object_summary": {"id": "p1", "name": "Alice", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=3)
        # p1 appears in both c1 and c2, but should only be in report once
        person_count = len(report.affected.get("person", []))
        assert person_count == 1

    def test_relationship_reason_populated(self):
        """Amendment 2: Every affected object has relationship_reason."""
        graph = _MockContextGraph({
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "repositories": [{"id": "r1", "name": "Repo1"}],
            },
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("capability", "c1", MagicMock(), max_depth=1)
        repo = report.affected["repository"][0]
        assert repo.relationship_reason != ""
        assert "repository_refs" in repo.relationship_reason

    def test_forward_direction(self):
        """Amendment 3: Forward traversal."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), direction="forward")
        assert report.direction == "forward"

    def test_reverse_direction(self):
        """Amendment 3: Reverse traversal."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), direction="reverse")
        assert report.direction == "reverse"

    def test_informational_relations_not_expanded(self):
        """KPIs, notifications etc. inform risk but don't expand frontier."""
        graph = _MockContextGraph({
            ("goal", "g1"): {
                "object_type": "goal",
                "object_id": "g1",
                "object_summary": {"id": "g1", "title": "G1", "status": "active", "owner": "x"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "kpis": [{"id": "k1", "name": "KPI1", "status": "on_track"}],
                "notifications": [{"id": "n1", "title": "N1", "severity": "info"}],
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("goal", "g1", MagicMock(), max_depth=3)
        # KPIs and notifications should NOT be in affected
        assert "kpi" not in report.affected
        assert "notification" not in report.affected
        assert report.total_affected == 0

    def test_broken_link_tracked(self):
        """Amendment 4: Object referenced but not found → broken link."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c_missing", "name": "Missing", "status": "active"}],
            },
            # c_missing is NOT in the graph → broken link
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=1)
        assert report.summary.coverage.unknown_references == 1
        assert "capability:c_missing" in report.summary.coverage.broken_links

    def test_coverage_types_analyzed(self):
        """Amendment 4: Coverage tracks how many types were seen."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "Cap1", "status": "active"}],
                "services": [{"id": "s1", "name": "Svc1", "status": "running"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "Cap1", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
            ("service", "s1"): {
                "object_type": "service",
                "object_id": "s1",
                "object_summary": {"id": "s1", "name": "Svc1", "status": "running", "health": "healthy"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=1)
        # types_seen: repository (source) + capability + service = 3
        assert report.summary.coverage.types_analyzed >= 3

    def test_max_depth_clamped_to_5(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=100)
        # Should not crash, max_depth clamped to 5
        assert report.source.object_id == "r1"

    def test_max_depth_min_1(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=0)
        assert report.source.object_id == "r1"

    def test_source_relationship_reason_is_analysis_source(self):
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock())
        assert report.source.relationship_reason == "Analysis source"

    def test_risk_scoring_on_affected_objects(self):
        """Affected objects get scored by RiskEngine."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "Repo1"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "services": [{"id": "s1", "name": "Svc1", "status": "running", "health": "unhealthy"}],
            },
            ("service", "s1"): {
                "object_type": "service",
                "object_id": "s1",
                "object_summary": {"id": "s1", "name": "Svc1", "status": "running", "health": "unhealthy"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=1)
        svc = report.affected["service"][0]
        assert svc.risk_level == "critical"
        assert any("unhealthy" in r for r in svc.risk_reasons)

    def test_full_pipeline_repo_to_goal_to_person(self):
        """Full end-to-end: repository → capability → goal → person."""
        graph = _MockContextGraph({
            ("repository", "r1"): {
                "object_type": "repository",
                "object_id": "r1",
                "object_summary": {"id": "r1", "name": "hermes-os"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "NLP", "status": "active"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "NLP", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "goals": [{"id": "g1", "title": "Ship NLP", "status": "active"}],
                "workflows": [{"id": "w1", "name": "WF", "status": "active", "attention_state": "ok"}],
            },
            ("goal", "g1"): {
                "object_type": "goal",
                "object_id": "g1",
                "object_summary": {"id": "g1", "title": "Ship NLP", "status": "active", "owner": "jerome"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "people": [{"id": "jerome", "name": "Jerome", "status": "active"}],
            },
            ("workflow", "w1"): {
                "object_type": "workflow",
                "object_id": "w1",
                "object_summary": {"id": "w1", "name": "WF", "active": True, "status": "active", "attention_state": "ok"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
            ("person", "jerome"): {
                "object_type": "person",
                "object_id": "jerome",
                "object_summary": {"id": "jerome", "name": "Jerome", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("repository", "r1", MagicMock(), max_depth=3)

        assert "capability" in report.affected
        assert "goal" in report.affected
        assert "person" in report.affected
        assert "workflow" in report.affected

        assert report.summary.affected_goals == ["g1"]
        assert report.summary.affected_people == ["jerome"]
        assert report.summary.safe_to_proceed is True

    def test_llm_provider_impact(self):
        """LLM provider unreachable → models critical → capabilities impacted."""
        graph = _MockContextGraph({
            ("llm_provider", "ollama"): {
                "object_type": "llm_provider",
                "object_id": "ollama",
                "object_summary": {"id": "ollama", "name": "Ollama", "health_state": "unreachable"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "models": [{"id": "ollama--llama3", "name": "Llama3", "attention_state": "critical"}],
            },
            ("model", "ollama--llama3"): {
                "object_type": "model",
                "object_id": "ollama--llama3",
                "object_summary": {"id": "ollama--llama3", "name": "Llama3", "attention_state": "critical", "status": "unavailable"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
                "capabilities": [{"id": "c1", "name": "NLP", "status": "active"}],
            },
            ("capability", "c1"): {
                "object_type": "capability",
                "object_id": "c1",
                "object_summary": {"id": "c1", "name": "NLP", "status": "active"},
                "attention": {"critical": 0, "warning": 0, "blocked": 0},
            },
        })
        engine = ImpactEngine(graph)
        report = engine.analyze("llm_provider", "ollama", MagicMock(), max_depth=3)

        assert report.source.risk_level == "critical"
        assert "model" in report.affected
        assert report.affected["model"][0].risk_level == "critical"
        assert report.summary.safe_to_proceed is False
        assert report.summary.estimated_impact == "severe"


# ── Gateway endpoint tests ──────────────────────────────────────────────────


@pytest.fixture
def gateway_client():
    from hermes.gateway.app import app
    from starlette.testclient import TestClient
    return TestClient(app)


def _mock_impact_result():
    return {
        "source": {
            "object_type": "repository",
            "object_id": "r1",
            "name": "Repo1",
            "risk_level": "none",
            "risk_reasons": [],
            "depth": 0,
            "path": [],
            "relationship_reason": "Analysis source",
        },
        "summary": {
            "source_type": "repository",
            "source_id": "r1",
            "source_name": "Repo1",
            "estimated_impact": "low",
            "affected_goals": [],
            "affected_operations": [],
            "affected_people": [],
            "critical_dependencies": 0,
            "blocking_risks": [],
            "recommended_checks": [],
            "safe_to_proceed": True,
            "coverage": {
                "types_analyzed": 2,
                "types_available": 15,
                "objects_analyzed": 3,
                "relationships_traversed": 5,
                "unknown_references": 0,
                "broken_links": [],
            },
        },
        "affected": {},
        "total_affected": 0,
        "max_depth_reached": 1,
        "cycle_detected": False,
        "direction": "forward",
    }


class TestGatewayImpact:
    def test_impact_endpoint_returns_200(self, gateway_client):
        with patch("hermes.service.HermesService.impact", return_value=_mock_impact_result()):
            resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/repository/r1")
            assert resp.status_code == 200
            data = resp.json()
            assert data["source"]["object_id"] == "r1"
            assert data["direction"] == "forward"

    def test_impact_unknown_type(self, gateway_client):
        resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/imaginary/x1")
        assert resp.status_code == 422

    def test_impact_not_found(self, gateway_client):
        not_found = _mock_impact_result()
        not_found["total_affected"] = 0
        not_found["source"]["risk_reasons"] = ["Object not found"]
        with patch("hermes.service.HermesService.impact", return_value=not_found):
            resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/repository/nonexistent")
            assert resp.status_code == 404

    def test_impact_with_direction(self, gateway_client):
        with patch("hermes.service.HermesService.impact", return_value=_mock_impact_result()) as mock:
            resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/repository/r1?direction=reverse")
            assert resp.status_code == 200
            mock.assert_called_once()
            call_args = mock.call_args
            assert call_args[0][4] == "reverse" or call_args[1].get("direction") == "reverse"

    def test_impact_invalid_direction(self, gateway_client):
        resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/repository/r1?direction=sideways")
        assert resp.status_code == 422

    def test_impact_with_max_depth(self, gateway_client):
        with patch("hermes.service.HermesService.impact", return_value=_mock_impact_result()) as mock:
            resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/repository/r1?max_depth=2")
            assert resp.status_code == 200
            mock.assert_called_once()

    def test_impact_max_depth_clamped(self, gateway_client):
        with patch("hermes.service.HermesService.impact", return_value=_mock_impact_result()) as mock:
            resp = gateway_client.get("/v1/workspaces/AVANZIA/impact/repository/r1?max_depth=99")
            assert resp.status_code == 200
            call_args = mock.call_args
            # max_depth should be clamped to 5
            depth_arg = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get("max_depth", 3)
            assert depth_arg <= 5


# ── Context Graph unmodified tests ───────────────────────────────────────────


def test_context_graph_supported_types_unchanged():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert len(SUPPORTED_TYPES) == 15  # Sprint 45 count, unchanged


def test_context_graph_relation_keys_unchanged():
    from hermes.context.context_graph import ALL_RELATION_KEYS
    assert len(ALL_RELATION_KEYS) == 17  # Sprint 45 count, unchanged
