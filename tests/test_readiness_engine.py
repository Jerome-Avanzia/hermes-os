"""Tests for Readiness Engine — Sprint 47.

Covers: Data structures, declarative rule evaluation, scenario-based
categories, NOT_APPLICABLE handling, navigation metadata, checklist
generation, overall readiness, RiskEngine integration, gateway endpoints.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hermes.context.readiness_engine import (
    ALL_CATEGORIES,
    CATEGORY_RULES,
    CATEGORY_STATUSES,
    CategoryResult,
    ChecklistItem,
    OVERALL_STATUSES,
    ReadinessEngine,
    ReadinessIssue,
    ReadinessReport,
    ReadinessSnapshot,
    SCENARIO_NAMES,
    SCENARIOS,
)
from hermes.context.risk_engine import RiskEngine


# ── Helpers ─────────────────────────────────────────────────────────────────

def _snapshot(**kwargs) -> ReadinessSnapshot:
    """Create a ReadinessSnapshot with defaults."""
    defaults = {"workspace_id": "TEST"}
    defaults.update(kwargs)
    return ReadinessSnapshot(**defaults)


def _engine() -> ReadinessEngine:
    return ReadinessEngine(RiskEngine())


# ── Data structure tests ────────────────────────────────────────────────────


def test_category_statuses_valid():
    assert "ready" in CATEGORY_STATUSES
    assert "warning" in CATEGORY_STATUSES
    assert "blocked" in CATEGORY_STATUSES
    assert "not_applicable" in CATEGORY_STATUSES


def test_overall_statuses_valid():
    assert "ready" in OVERALL_STATUSES
    assert "not_ready" in OVERALL_STATUSES


def test_all_categories_count():
    assert len(ALL_CATEGORIES) == 8


def test_all_categories_names():
    expected = {
        "infrastructure", "repositories", "automation", "data",
        "ai", "operations", "business", "goals",
    }
    assert ALL_CATEGORIES == expected


def test_snapshot_defaults():
    s = _snapshot()
    assert s.workspace_id == "TEST"
    assert s.services == []
    assert s.infrastructure_configured is False
    assert s.heartbeats_by_operation == {}


def test_readiness_issue_fields():
    issue = ReadinessIssue(
        category="infrastructure",
        object_type="service",
        object_id="svc1",
        name="Gateway",
        reason="Service 'Gateway' is unhealthy",
        severity="blocker",
    )
    assert issue.object_type == "service"
    assert issue.object_id == "svc1"
    assert issue.severity == "blocker"


def test_category_result_defaults():
    r = CategoryResult(category="test", status="ready", score=100)
    assert r.blockers == []
    assert r.warnings == []
    assert r.checked == 0
    assert r.healthy == 0


def test_checklist_item_defaults():
    c = ChecklistItem(priority=1, category="infrastructure",
                      check="test", status="pass")
    assert c.issues == []


def test_readiness_report_defaults():
    r = ReadinessReport(workspace_id="X", scenario="deployment",
                        scenario_label="Deployment", overall_status="ready",
                        overall_score=100)
    assert r.categories == {}
    assert r.blockers == []
    assert r.checklist == []
    assert r.critical_dependencies == 0


# ── Scenario tests ──────────────────────────────────────────────────────────


def test_scenario_names_exist():
    assert "deployment" in SCENARIO_NAMES
    assert "repository_merge" in SCENARIO_NAMES
    assert "workflow_activation" in SCENARIO_NAMES
    assert "database_maintenance" in SCENARIO_NAMES
    assert "provider_switch" in SCENARIO_NAMES


def test_scenario_count():
    assert len(SCENARIOS) == 5


def test_deployment_has_all_categories():
    weights = SCENARIOS["deployment"]["weights"]
    assert len(weights) == 8
    for cat in ALL_CATEGORIES:
        assert cat in weights


def test_repository_merge_excludes_infrastructure():
    weights = SCENARIOS["repository_merge"]["weights"]
    assert "infrastructure" not in weights
    assert "repositories" in weights


def test_workflow_activation_excludes_goals():
    weights = SCENARIOS["workflow_activation"]["weights"]
    assert "goals" not in weights
    assert "automation" in weights


def test_database_maintenance_excludes_ai():
    weights = SCENARIOS["database_maintenance"]["weights"]
    assert "ai" not in weights
    assert "data" in weights


def test_provider_switch_excludes_data():
    weights = SCENARIOS["provider_switch"]["weights"]
    assert "data" not in weights
    assert "ai" in weights


def test_scenario_weights_sum_to_100():
    for name, scenario in SCENARIOS.items():
        total = sum(scenario["weights"].values())
        assert total == 100, f"Scenario '{name}' weights sum to {total}, not 100"


def test_invalid_scenario_raises():
    engine = _engine()
    with pytest.raises(ValueError, match="Unknown scenario"):
        engine.evaluate(_snapshot(), scenario="nonexistent")


def test_invalid_scenario_in_evaluate_category_raises():
    engine = _engine()
    with pytest.raises(ValueError, match="Unknown scenario"):
        engine.evaluate_category(_snapshot(), "infrastructure", scenario="bad")


# ── Declarative rules tests ─────────────────────────────────────────────────


def test_category_rules_complete():
    for cat in ALL_CATEGORIES:
        assert cat in CATEGORY_RULES, f"Missing rule for category: {cat}"


def test_category_rules_have_required_fields():
    for cat, rules in CATEGORY_RULES.items():
        assert "configured_flag" in rules, f"{cat} missing configured_flag"
        assert "sources" in rules, f"{cat} missing sources"
        assert "checklist_label" in rules, f"{cat} missing checklist_label"
        for source in rules["sources"]:
            assert "source_field" in source, f"{cat} source missing source_field"
            assert "object_type" in source, f"{cat} source missing object_type"
            assert "id_field" in source, f"{cat} source missing id_field"
            assert "name_field" in source, f"{cat} source missing name_field"
            assert "blockers" in source, f"{cat} source missing blockers"
            assert "warnings" in source, f"{cat} source missing warnings"


def test_rules_are_tuples_of_three():
    for cat, rules in CATEGORY_RULES.items():
        for source in rules["sources"]:
            for rule in source["blockers"] + source["warnings"]:
                assert len(rule) == 3, f"{cat} rule has {len(rule)} elements"


# ── NOT_APPLICABLE tests (Amendment 3) ──────────────────────────────────────


def test_not_applicable_for_absent_category():
    engine = _engine()
    # repository_merge excludes infrastructure
    result = engine.evaluate_category(
        _snapshot(), "infrastructure", scenario="repository_merge",
    )
    assert result.status == "not_applicable"
    assert result.score == 0


def test_full_report_not_applicable_categories():
    engine = _engine()
    report = engine.evaluate(_snapshot(), scenario="repository_merge")
    assert report.categories["infrastructure"].status == "not_applicable"
    assert report.categories["data"].status == "not_applicable"
    assert report.categories["ai"].status == "not_applicable"


def test_not_applicable_not_in_checklist():
    engine = _engine()
    report = engine.evaluate(_snapshot(), scenario="repository_merge")
    checklist_cats = {item.category for item in report.checklist}
    # Only scenario categories in checklist
    assert "infrastructure" not in checklist_cats
    assert "repositories" in checklist_cats


def test_invalid_category_raises():
    engine = _engine()
    with pytest.raises(ValueError, match="Unknown category"):
        engine.evaluate_category(_snapshot(), "nonexistent")


# ── Infrastructure category tests ───────────────────────────────────────────


class TestInfrastructureCategory:
    def test_not_configured_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(infrastructure_configured=False), "infrastructure",
        )
        assert result.status == "warning"
        assert result.score == 0
        assert len(result.warnings) == 1
        assert result.warnings[0].object_type == "runtime"
        assert "not configured" in result.warnings[0].reason

    def test_healthy_services_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Gateway", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s2", "name": "Worker", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        assert result.status == "ready"
        assert result.score == 100
        assert result.checked == 2
        assert result.healthy == 2

    def test_unhealthy_service_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Gateway", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s2", "name": "Worker", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        assert result.status == "blocked"
        assert result.score == 50
        assert len(result.blockers) == 1
        assert result.blockers[0].object_type == "service"
        assert result.blockers[0].object_id == "s1"
        assert "unhealthy" in result.blockers[0].reason

    def test_resource_critical_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "DB", "health": "healthy",
                     "status": "running", "resource_state": "critical"},
                ],
            ),
            "infrastructure",
        )
        assert result.status == "blocked"
        assert "critical resource" in result.blockers[0].reason

    def test_restarting_service_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "healthy",
                     "status": "restarting", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        assert result.status == "warning"
        assert result.score == 0
        assert result.warnings[0].object_type == "service"
        assert "restarting" in result.warnings[0].reason

    def test_elevated_resource_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "healthy",
                     "status": "running", "resource_state": "elevated"},
                ],
            ),
            "infrastructure",
        )
        assert result.status == "warning"

    def test_empty_services_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(infrastructure_configured=True, services=[]),
            "infrastructure",
        )
        assert result.status == "ready"
        assert result.score == 100
        assert result.checked == 0


# ── Repositories category tests ─────────────────────────────────────────────


class TestRepositoriesCategory:
    def test_not_configured_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(github_configured=False), "repositories",
        )
        assert result.status == "warning"
        assert result.score == 0

    def test_configured_with_repos_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                github_configured=True,
                repositories=[{"id": "r1", "name": "hermes-os"}],
            ),
            "repositories",
        )
        assert result.status == "ready"
        assert result.score == 100
        assert result.checked == 1

    def test_configured_empty_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(github_configured=True, repositories=[]),
            "repositories",
        )
        assert result.status == "ready"
        assert result.score == 100


# ── Automation category tests ───────────────────────────────────────────────


class TestAutomationCategory:
    def test_not_configured_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(n8n_configured=False), "automation",
        )
        assert result.status == "warning"
        assert result.score == 0

    def test_healthy_workflows_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "Sync", "attention_state": "ok",
                     "status": "active"},
                ],
            ),
            "automation",
        )
        assert result.status == "ready"
        assert result.score == 100

    def test_critical_workflow_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "Sync", "attention_state": "critical",
                     "status": "active"},
                ],
            ),
            "automation",
        )
        assert result.status == "blocked"
        assert result.blockers[0].object_id == "w1"
        assert "critical failures" in result.blockers[0].reason

    def test_warning_workflow_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "Sync", "attention_state": "warning",
                     "status": "active"},
                ],
            ),
            "automation",
        )
        assert result.status == "warning"

    def test_inactive_workflow_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "Old", "attention_state": "ok",
                     "status": "inactive"},
                ],
            ),
            "automation",
        )
        assert result.status == "warning"
        assert "inactive" in result.warnings[0].reason


# ── Data category tests ─────────────────────────────────────────────────────


class TestDataCategory:
    def test_not_configured_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(nocodb_configured=False), "data",
        )
        assert result.status == "warning"

    def test_healthy_data_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                nocodb_configured=True,
                databases=[{"id": "d1", "name": "Main", "health_state": "healthy"}],
                tables=[{"id": "t1", "name": "Users", "attention_state": "ok"}],
            ),
            "data",
        )
        assert result.status == "ready"
        assert result.checked == 2
        assert result.healthy == 2

    def test_degraded_database_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                nocodb_configured=True,
                databases=[{"id": "d1", "name": "Main", "health_state": "degraded"}],
            ),
            "data",
        )
        assert result.status == "blocked"
        assert "degraded" in result.blockers[0].reason

    def test_critical_table_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                nocodb_configured=True,
                tables=[{"id": "t1", "name": "Users", "attention_state": "critical"}],
            ),
            "data",
        )
        assert result.status == "blocked"

    def test_unknown_database_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                nocodb_configured=True,
                databases=[{"id": "d1", "name": "Main", "health_state": "unknown"}],
            ),
            "data",
        )
        assert result.status == "warning"

    def test_warning_table_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                nocodb_configured=True,
                tables=[{"id": "t1", "name": "T", "attention_state": "warning"}],
            ),
            "data",
        )
        assert result.status == "warning"


# ── AI category tests ──────────────────────────────────────────────────────


class TestAICategory:
    def test_not_configured_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(llm_configured=False), "ai",
        )
        assert result.status == "warning"

    def test_healthy_ai_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                llm_configured=True,
                llm_providers=[{"id": "ollama", "name": "Ollama",
                                "health_state": "healthy"}],
                models=[{"id": "m1", "name": "Llama3",
                         "attention_state": "ok"}],
            ),
            "ai",
        )
        assert result.status == "ready"
        assert result.checked == 2

    def test_unreachable_provider_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                llm_configured=True,
                llm_providers=[{"id": "ollama", "name": "Ollama",
                                "health_state": "unreachable"}],
            ),
            "ai",
        )
        assert result.status == "blocked"
        assert result.blockers[0].object_type == "llm_provider"
        assert "unreachable" in result.blockers[0].reason

    def test_degraded_provider_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                llm_configured=True,
                llm_providers=[{"id": "p1", "name": "P", "health_state": "degraded"}],
            ),
            "ai",
        )
        assert result.status == "warning"

    def test_critical_model_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                llm_configured=True,
                models=[{"id": "m1", "name": "M", "attention_state": "critical"}],
            ),
            "ai",
        )
        assert result.status == "blocked"

    def test_warning_model_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                llm_configured=True,
                models=[{"id": "m1", "name": "M", "attention_state": "warning"}],
            ),
            "ai",
        )
        assert result.status == "warning"


# ── Operations category tests ──────────────────────────────────────────────


class TestOperationsCategory:
    def test_no_operations_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(_snapshot(), "operations")
        assert result.status == "ready"
        assert result.score == 100

    def test_completed_operations_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(operations=[
                {"id": "op1", "request": "Deploy", "status": "completed"},
            ]),
            "operations",
        )
        assert result.status == "ready"
        assert result.score == 100

    def test_failed_operation_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(operations=[
                {"id": "op1", "request": "Deploy v2", "status": "failed"},
            ]),
            "operations",
        )
        assert result.status == "blocked"
        assert result.blockers[0].object_id == "op1"
        assert result.blockers[0].name == "Deploy v2"
        assert "failed" in result.blockers[0].reason

    def test_executing_operation_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(operations=[
                {"id": "op1", "request": "Build", "status": "executing"},
            ]),
            "operations",
        )
        assert result.status == "warning"
        assert "executing" in result.warnings[0].reason

    def test_heartbeat_blocked_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                operations=[
                    {"id": "op1", "request": "Deploy", "status": "executing"},
                ],
                heartbeats_by_operation={
                    "op1": [{"status": "blocked", "summary": "Waiting on DB"}],
                },
            ),
            "operations",
        )
        assert result.status == "blocked"
        assert any("blocked" in b.reason for b in result.blockers)

    def test_heartbeat_not_blocked_no_effect(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                operations=[
                    {"id": "op1", "request": "Deploy", "status": "executing"},
                ],
                heartbeats_by_operation={
                    "op1": [{"status": "in_progress", "summary": "Going well"}],
                },
            ),
            "operations",
        )
        # Still warning because executing
        assert result.status == "warning"
        assert len(result.blockers) == 0

    def test_heartbeat_ignored_for_completed(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                operations=[
                    {"id": "op1", "request": "Deploy", "status": "completed"},
                ],
                heartbeats_by_operation={
                    "op1": [{"status": "blocked", "summary": "Old"}],
                },
            ),
            "operations",
        )
        # Completed operation — heartbeat doesn't matter
        assert result.status == "ready"
        assert len(result.blockers) == 0


# ── Business category tests ────────────────────────────────────────────────


class TestBusinessCategory:
    def test_active_business_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                capabilities=[{"id": "c1", "name": "NLP", "status": "active"}],
                people=[{"id": "p1", "name": "Alice", "status": "active"}],
                departments=[{"id": "d1", "name": "Eng", "status": "active"}],
            ),
            "business",
        )
        assert result.status == "ready"
        assert result.checked == 3
        assert result.healthy == 3

    def test_deprecated_capability_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                capabilities=[{"id": "c1", "name": "Old", "status": "deprecated"}],
            ),
            "business",
        )
        assert result.status == "blocked"
        assert result.blockers[0].object_type == "capability"
        assert "deprecated" in result.blockers[0].reason

    def test_experimental_capability_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                capabilities=[{"id": "c1", "name": "Beta", "status": "experimental"}],
            ),
            "business",
        )
        assert result.status == "warning"

    def test_draft_capability_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                capabilities=[{"id": "c1", "name": "New", "status": "draft"}],
            ),
            "business",
        )
        assert result.status == "warning"
        assert "draft" in result.warnings[0].reason

    def test_inactive_person_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                people=[{"id": "p1", "name": "Bob", "status": "inactive"}],
            ),
            "business",
        )
        assert result.status == "warning"
        assert result.warnings[0].object_type == "person"

    def test_inactive_department_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                departments=[{"id": "d1", "name": "Sales", "status": "inactive"}],
            ),
            "business",
        )
        assert result.status == "warning"
        assert result.warnings[0].object_type == "department"

    def test_empty_business_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(_snapshot(), "business")
        assert result.status == "ready"
        assert result.score == 100


# ── Goals category tests ───────────────────────────────────────────────────


class TestGoalsCategory:
    def test_active_goals_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                goals=[{"id": "g1", "title": "Ship v2", "status": "active"}],
                kpis=[{"id": "k1", "name": "Revenue", "status": "on_track"}],
            ),
            "goals",
        )
        assert result.status == "ready"
        assert result.checked == 2

    def test_at_risk_goal_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                goals=[{"id": "g1", "title": "Ship v2", "status": "at_risk"}],
            ),
            "goals",
        )
        assert result.status == "blocked"
        assert result.blockers[0].object_type == "goal"
        assert "at risk" in result.blockers[0].reason

    def test_blocked_goal_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                goals=[{"id": "g1", "title": "Ship v2", "status": "blocked"}],
            ),
            "goals",
        )
        assert result.status == "warning"

    def test_off_track_kpi_is_blocked(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                kpis=[{"id": "k1", "name": "Revenue", "status": "off_track"}],
            ),
            "goals",
        )
        assert result.status == "blocked"
        assert result.blockers[0].object_type == "kpi"
        assert "off track" in result.blockers[0].reason

    def test_at_risk_kpi_is_warning(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                kpis=[{"id": "k1", "name": "Revenue", "status": "at_risk"}],
            ),
            "goals",
        )
        assert result.status == "warning"

    def test_completed_goal_is_ready(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                goals=[{"id": "g1", "title": "Done", "status": "completed"}],
            ),
            "goals",
        )
        assert result.status == "ready"
        assert result.score == 100


# ── Navigation metadata tests (Amendment 4) ─────────────────────────────────


class TestNavigationMetadata:
    def test_blocker_has_navigation_fields(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "svc-1", "name": "Gateway", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        b = result.blockers[0]
        assert b.object_type == "service"
        assert b.object_id == "svc-1"
        assert b.name == "Gateway"
        assert b.reason != ""
        assert b.category == "infrastructure"
        assert b.severity == "blocker"

    def test_warning_has_navigation_fields(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "svc-1", "name": "Worker", "health": "healthy",
                     "status": "paused", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        w = result.warnings[0]
        assert w.object_type == "service"
        assert w.object_id == "svc-1"
        assert w.name == "Worker"
        assert w.severity == "warning"

    def test_unconfigured_runtime_has_navigation(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(llm_configured=False), "ai",
        )
        w = result.warnings[0]
        assert w.object_type == "runtime"
        assert w.object_id == "ai"
        assert w.severity == "warning"

    def test_all_report_blockers_have_metadata(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
                operations=[
                    {"id": "op1", "request": "Op", "status": "failed"},
                ],
            ),
            scenario="deployment",
        )
        for b in report.blockers:
            assert b.object_type != ""
            assert b.object_id != ""
            assert b.reason != ""
            assert b.severity == "blocker"

    def test_all_report_warnings_have_metadata(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "WF", "attention_state": "warning",
                     "status": "active"},
                ],
            ),
            scenario="deployment",
        )
        for w in report.warnings:
            assert w.object_type != ""
            assert w.object_id != ""
            assert w.severity == "warning"


# ── Checklist tests (Amendment 5) ───────────────────────────────────────────


class TestChecklist:
    def test_checklist_ordered_by_weight(self):
        engine = _engine()
        report = engine.evaluate(_snapshot(), scenario="deployment")
        # Deployment weights: infrastructure=20, operations=20, goals=15, ...
        priorities = [item.priority for item in report.checklist]
        assert priorities == sorted(priorities)
        # First items should be the highest-weight categories
        assert report.checklist[0].priority == 1

    def test_checklist_only_scenario_categories(self):
        engine = _engine()
        report = engine.evaluate(_snapshot(), scenario="repository_merge")
        checklist_cats = {item.category for item in report.checklist}
        scenario_cats = set(SCENARIOS["repository_merge"]["weights"].keys())
        assert checklist_cats == scenario_cats

    def test_checklist_pass_status(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            scenario="deployment",
        )
        infra_item = next(
            i for i in report.checklist if i.category == "infrastructure"
        )
        assert infra_item.status == "pass"
        assert "all clear" in infra_item.check

    def test_checklist_fail_status(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            scenario="deployment",
        )
        infra_item = next(
            i for i in report.checklist if i.category == "infrastructure"
        )
        assert infra_item.status == "fail"
        assert "blocking issue" in infra_item.check
        assert len(infra_item.issues) >= 1

    def test_checklist_warning_status(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "WF", "attention_state": "warning",
                     "status": "active"},
                ],
            ),
            scenario="deployment",
        )
        auto_item = next(
            i for i in report.checklist if i.category == "automation"
        )
        assert auto_item.status == "warning"
        assert "warning" in auto_item.check

    def test_checklist_issues_populated(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                operations=[
                    {"id": "op1", "request": "Op", "status": "failed"},
                ],
            ),
            scenario="deployment",
        )
        ops_item = next(
            i for i in report.checklist if i.category == "operations"
        )
        assert ops_item.status == "fail"
        assert len(ops_item.issues) == 1
        assert ops_item.issues[0].object_id == "op1"

    def test_checklist_plural_blockers(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "A", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s2", "name": "B", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            scenario="deployment",
        )
        infra_item = next(
            i for i in report.checklist if i.category == "infrastructure"
        )
        assert "2 blocking issues" in infra_item.check


# ── Overall readiness tests ─────────────────────────────────────────────────


class TestOverallReadiness:
    def test_all_clear_is_ready(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                github_configured=True,
                n8n_configured=True,
                nocodb_configured=True,
                llm_configured=True,
            ),
            scenario="deployment",
        )
        assert report.overall_status == "ready"
        assert report.overall_score == 100

    def test_one_blocked_is_not_ready(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                github_configured=True,
                n8n_configured=True,
                nocodb_configured=True,
                llm_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            scenario="deployment",
        )
        assert report.overall_status == "not_ready"

    def test_warnings_only_is_ready(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                github_configured=True,
                n8n_configured=True,
                nocodb_configured=True,
                llm_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "healthy",
                     "status": "restarting", "resource_state": "normal"},
                ],
            ),
            scenario="deployment",
        )
        assert report.overall_status == "ready"
        assert report.overall_score < 100

    def test_unconfigured_runtimes_reduce_score(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(),  # nothing configured
            scenario="deployment",
        )
        # 5 runtime categories at score 0, 3 non-runtime at score 100
        # infrastructure(20*0) + repos(5*0) + auto(10*0) + data(10*0) +
        # ai(10*0) + ops(20*100) + biz(10*100) + goals(15*100) = 4500
        # 4500/100 = 45
        assert report.overall_status == "ready"  # warnings, not blocked
        assert report.overall_score == 45

    def test_score_is_weighted_average(self):
        engine = _engine()
        # repository_merge: repos=25, auto=20, ops=20, biz=15, goals=20
        report = engine.evaluate(
            _snapshot(
                github_configured=True,
                repositories=[{"id": "r1", "name": "R"}],
                n8n_configured=True,
                workflows=[{"id": "w1", "name": "W", "attention_state": "ok",
                             "status": "active"}],
            ),
            scenario="repository_merge",
        )
        # repos: 100, auto: 100, ops: 100, biz: 100, goals: 100
        assert report.overall_score == 100

    def test_scenario_in_report(self):
        engine = _engine()
        report = engine.evaluate(_snapshot(), scenario="provider_switch")
        assert report.scenario == "provider_switch"
        assert report.scenario_label == "Provider Switch"

    def test_timestamp_populated(self):
        engine = _engine()
        report = engine.evaluate(_snapshot())
        assert report.timestamp != ""
        assert "T" in report.timestamp  # ISO 8601

    def test_blockers_aggregated(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
                operations=[
                    {"id": "op1", "request": "Op", "status": "failed"},
                ],
            ),
            scenario="deployment",
        )
        assert len(report.blockers) == 2
        cats = {b.category for b in report.blockers}
        assert "infrastructure" in cats
        assert "operations" in cats

    def test_warnings_aggregated(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "W", "attention_state": "warning",
                     "status": "active"},
                ],
                capabilities=[{"id": "c1", "name": "C", "status": "experimental"}],
            ),
            scenario="deployment",
        )
        assert len(report.warnings) >= 2

    def test_not_applicable_not_in_blockers(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            scenario="repository_merge",
        )
        # Infrastructure is NOT in repository_merge, so its blocker
        # should NOT appear in the report
        blocker_cats = {b.category for b in report.blockers}
        assert "infrastructure" not in blocker_cats


# ── Score computation tests ─────────────────────────────────────────────────


class TestScoreComputation:
    def test_score_half_healthy(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "A", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s2", "name": "B", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        assert result.score == 50

    def test_score_one_of_three(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "A", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s2", "name": "B", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s3", "name": "C", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        assert result.score == 33  # 1/3 rounded

    def test_score_multi_source_category(self):
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                nocodb_configured=True,
                databases=[
                    {"id": "d1", "name": "DB1", "health_state": "healthy"},
                    {"id": "d2", "name": "DB2", "health_state": "degraded"},
                ],
                tables=[
                    {"id": "t1", "name": "T1", "attention_state": "ok"},
                ],
            ),
            "data",
        )
        # 2 healthy out of 3 checked
        assert result.checked == 3
        assert result.healthy == 2
        assert result.score == 67  # 2/3 rounded

    def test_blocker_takes_priority_over_warning(self):
        """If an object matches both blocker and would match warning,
        only the blocker counts."""
        engine = _engine()
        result = engine.evaluate_category(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy",
                     "status": "restarting", "resource_state": "normal"},
                ],
            ),
            "infrastructure",
        )
        # health=unhealthy is blocker, status=restarting would be warning
        # but blocker takes precedence
        assert result.status == "blocked"
        assert len(result.blockers) == 1
        assert len(result.warnings) == 0


# ── RiskEngine integration tests ───────────────────────────────────────────


class TestRiskEngineIntegration:
    def test_critical_dependencies_counts_critical(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "unhealthy"},
                ],
                operations=[
                    {"id": "op1", "request": "Op", "status": "failed"},
                ],
            ),
            scenario="deployment",
        )
        # Both service (unhealthy→critical) and operation (failed→critical)
        assert report.critical_dependencies >= 2

    def test_critical_dependencies_zero_when_all_healthy(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                services=[
                    {"id": "s1", "name": "Svc", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                ],
            ),
            scenario="deployment",
        )
        assert report.critical_dependencies == 0

    def test_critical_dependencies_includes_all_types(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                capabilities=[
                    {"id": "c1", "name": "C", "status": "deprecated"},
                ],
            ),
            scenario="deployment",
        )
        # Deprecated capability → critical risk
        assert report.critical_dependencies >= 1


# ── Full pipeline tests ────────────────────────────────────────────────────


class TestFullPipeline:
    def test_deployment_all_healthy(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                github_configured=True,
                n8n_configured=True,
                nocodb_configured=True,
                llm_configured=True,
                services=[
                    {"id": "s1", "name": "Gateway", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                ],
                repositories=[{"id": "r1", "name": "hermes-os"}],
                workflows=[
                    {"id": "w1", "name": "Sync", "attention_state": "ok",
                     "status": "active"},
                ],
                databases=[
                    {"id": "d1", "name": "Main", "health_state": "healthy"},
                ],
                tables=[
                    {"id": "t1", "name": "Users", "attention_state": "ok"},
                ],
                llm_providers=[
                    {"id": "ollama", "name": "Ollama", "health_state": "healthy"},
                ],
                models=[
                    {"id": "m1", "name": "Llama3", "attention_state": "ok"},
                ],
                capabilities=[
                    {"id": "c1", "name": "NLP", "status": "active"},
                ],
                goals=[
                    {"id": "g1", "title": "Ship v2", "status": "active"},
                ],
                people=[
                    {"id": "p1", "name": "Alice", "status": "active"},
                ],
                departments=[
                    {"id": "d1", "name": "Engineering", "status": "active"},
                ],
                kpis=[
                    {"id": "k1", "name": "Revenue", "status": "on_track"},
                ],
            ),
            scenario="deployment",
        )
        assert report.overall_status == "ready"
        assert report.overall_score == 100
        assert len(report.blockers) == 0
        assert len(report.warnings) == 0
        assert report.critical_dependencies == 0
        assert all(
            item.status == "pass"
            for item in report.checklist
        )

    def test_deployment_mixed_issues(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                infrastructure_configured=True,
                github_configured=True,
                n8n_configured=True,
                nocodb_configured=True,
                llm_configured=True,
                services=[
                    {"id": "s1", "name": "Gateway", "health": "unhealthy",
                     "status": "running", "resource_state": "normal"},
                    {"id": "s2", "name": "Worker", "health": "healthy",
                     "status": "running", "resource_state": "normal"},
                ],
                workflows=[
                    {"id": "w1", "name": "Sync", "attention_state": "warning",
                     "status": "active"},
                ],
                goals=[
                    {"id": "g1", "title": "Ship v2", "status": "at_risk"},
                ],
            ),
            scenario="deployment",
        )
        assert report.overall_status == "not_ready"
        assert report.overall_score < 100
        assert len(report.blockers) >= 2  # service + goal
        assert len(report.warnings) >= 1  # workflow

    def test_repository_merge_scenario(self):
        engine = _engine()
        report = engine.evaluate(
            _snapshot(
                github_configured=True,
                repositories=[{"id": "r1", "name": "R"}],
                n8n_configured=True,
                workflows=[
                    {"id": "w1", "name": "CI", "attention_state": "ok",
                     "status": "active"},
                ],
                capabilities=[
                    {"id": "c1", "name": "NLP", "status": "active"},
                ],
                goals=[
                    {"id": "g1", "title": "Ship", "status": "active"},
                ],
            ),
            scenario="repository_merge",
        )
        assert report.overall_status == "ready"
        assert report.scenario == "repository_merge"
        # Infrastructure, data, AI should be NOT_APPLICABLE
        assert report.categories["infrastructure"].status == "not_applicable"
        assert report.categories["data"].status == "not_applicable"
        assert report.categories["ai"].status == "not_applicable"


# ── Gateway endpoint tests ──────────────────────────────────────────────────


@pytest.fixture
def gateway_client():
    from hermes.gateway.app import app
    from starlette.testclient import TestClient
    return TestClient(app)


def _mock_readiness_result():
    return {
        "workspace_id": "AVANZIA",
        "scenario": "deployment",
        "scenario_label": "Deployment",
        "overall_status": "ready",
        "overall_score": 95,
        "categories": {
            "infrastructure": {
                "category": "infrastructure",
                "status": "ready",
                "score": 100,
                "blockers": [],
                "warnings": [],
                "checked": 2,
                "healthy": 2,
            },
        },
        "blockers": [],
        "warnings": [],
        "checklist": [
            {
                "priority": 1,
                "category": "infrastructure",
                "check": "Infrastructure health — all clear",
                "status": "pass",
                "issues": [],
            },
        ],
        "critical_dependencies": 0,
        "timestamp": "2026-08-04T14:30:00+00:00",
    }


def _mock_category_result():
    return {
        "category": "infrastructure",
        "status": "ready",
        "score": 100,
        "blockers": [],
        "warnings": [],
        "checked": 2,
        "healthy": 2,
    }


class TestGatewayReadiness:
    def test_readiness_endpoint_returns_200(self, gateway_client):
        with patch("hermes.service.HermesService.readiness",
                   return_value=_mock_readiness_result()):
            resp = gateway_client.get(
                "/v1/workspaces/AVANZIA/readiness",
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["overall_status"] == "ready"
            assert data["scenario"] == "deployment"

    def test_readiness_with_scenario_param(self, gateway_client):
        with patch("hermes.service.HermesService.readiness",
                   return_value=_mock_readiness_result()) as mock:
            resp = gateway_client.get(
                "/v1/workspaces/AVANZIA/readiness?scenario=repository_merge",
            )
            assert resp.status_code == 200
            mock.assert_called_once()
            call_args = mock.call_args
            assert call_args[0][1] == "repository_merge" or \
                call_args[1].get("scenario") == "repository_merge"

    def test_readiness_invalid_scenario(self, gateway_client):
        resp = gateway_client.get(
            "/v1/workspaces/AVANZIA/readiness?scenario=nonexistent",
        )
        assert resp.status_code == 422

    def test_readiness_category_returns_200(self, gateway_client):
        with patch("hermes.service.HermesService.readiness_category",
                   return_value=_mock_category_result()):
            resp = gateway_client.get(
                "/v1/workspaces/AVANZIA/readiness/infrastructure",
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["category"] == "infrastructure"

    def test_readiness_invalid_category(self, gateway_client):
        resp = gateway_client.get(
            "/v1/workspaces/AVANZIA/readiness/nonexistent",
        )
        assert resp.status_code == 422

    def test_readiness_category_with_scenario(self, gateway_client):
        with patch("hermes.service.HermesService.readiness_category",
                   return_value=_mock_category_result()) as mock:
            resp = gateway_client.get(
                "/v1/workspaces/AVANZIA/readiness/infrastructure"
                "?scenario=workflow_activation",
            )
            assert resp.status_code == 200
            mock.assert_called_once()


# ── Regression tests ────────────────────────────────────────────────────────


def test_category_rules_cover_all_categories():
    for cat in ALL_CATEGORIES:
        assert cat in CATEGORY_RULES


def test_scenario_weights_are_positive():
    for name, scenario in SCENARIOS.items():
        for cat, weight in scenario["weights"].items():
            assert weight > 0, f"{name}.{cat} has non-positive weight"
            assert cat in ALL_CATEGORIES, f"{name} references unknown category: {cat}"


def test_context_graph_supported_types_unchanged():
    from hermes.context.context_graph import SUPPORTED_TYPES
    assert len(SUPPORTED_TYPES) == 15


def test_risk_levels_unchanged():
    from hermes.context.risk_engine import RISK_LEVELS
    assert len(RISK_LEVELS) == 5
