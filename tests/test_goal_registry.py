"""Tests for GoalRegistry — deterministic goal discovery."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from hermes.kernel.goal_registry import GoalRegistry, GOAL_STATUSES

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = ROOT / "examples"

_BIZ_ROOT = Path("businesses")


# -- Lifecycle -----------------------------------------------------------------


def test_registry_build_indexes_all_goals():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    reg.build()
    goals = reg.list()
    assert len(goals) == 5


def test_registry_build_is_idempotent():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    reg.build()
    count1 = len(reg.list())
    reg.build()
    assert len(reg.list()) == count1


def test_registry_reload_rebuilds():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.reload()
    assert len(reg.list()) > 0


def test_registry_invalidate_clears_index():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    reg.build()
    assert len(reg.list()) > 0
    reg.invalidate()
    goals = reg.list()
    assert len(goals) > 0


def test_registry_auto_builds_on_first_access():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goals = reg.list()
    assert len(goals) == 5


# -- Queries -------------------------------------------------------------------


def test_registry_list_returns_sorted_by_title():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goals = reg.list()
    titles = [g.title for g in goals]
    assert titles == sorted(titles)


def test_registry_get_returns_goal():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goal = reg.get("GOAL-001")
    assert goal is not None
    assert goal.goal_id == "GOAL-001"


def test_registry_get_unknown_returns_none():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    assert reg.get("GOAL-999") is None


# -- Enriched fields -----------------------------------------------------------


def test_registry_goal_has_required_fields():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goal = reg.get("GOAL-001")
    assert goal.title
    assert goal.description
    assert goal.target_value
    assert goal.target_date
    assert goal.owner
    assert goal.status


def test_registry_goal_has_business_id():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goal = reg.get("GOAL-001")
    assert goal.business_id == "biz_avanzia"


def test_registry_goal_has_strategy_id():
    """Goals may optionally link to a strategy."""
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goal = reg.get("GOAL-001")
    # AVANZIA goals have strategy references
    assert goal.strategy_id is not None


# -- Edge cases ----------------------------------------------------------------


def test_registry_empty_dir(tmp_path):
    reg = GoalRegistry(businesses_root=tmp_path)
    assert reg.list() == []


def test_registry_nonexistent_dir():
    reg = GoalRegistry(businesses_root=Path("/nonexistent/path"))
    assert reg.list() == []


def test_registry_biz_dir_without_goals(tmp_path):
    (tmp_path / "empty-biz").mkdir()
    reg = GoalRegistry(businesses_root=tmp_path)
    assert reg.list() == []


# -- Status values -------------------------------------------------------------


def test_all_statuses_valid():
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    for goal in reg.list():
        assert goal.status in GOAL_STATUSES


# -- Amendment 1: Standard registry interface ----------------------------------


def test_registry_has_standard_lifecycle_methods():
    """Amendment 1: GoalRegistry exposes build/reload/invalidate like all registries."""
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    assert callable(reg.build)
    assert callable(reg.reload)
    assert callable(reg.invalidate)
    assert callable(reg.list)
    assert callable(reg.get)


# -- Amendment 2: No derived priority -----------------------------------------


def test_goal_has_no_priority_field():
    """Amendment 2: Goals have status, not derived priority."""
    reg = GoalRegistry(businesses_root=_BIZ_ROOT)
    goal = reg.get("GOAL-001")
    assert not hasattr(goal, "priority")


# -- Schema & Example ---------------------------------------------------------


class TestGoalSchema:
    def test_example_validates_against_schema(self):
        schema = json.loads((CONTRACTS / "goal.schema.json").read_text())
        example = json.loads((EXAMPLES / "goal.example.json").read_text())
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
        assert not errors, f"Schema validation errors: {[e.message for e in errors]}"

    def test_schema_required_fields(self):
        schema = json.loads((CONTRACTS / "goal.schema.json").read_text())
        assert set(schema["required"]) == {
            "goal_id", "business_id", "title", "description",
            "target_value", "target_date", "owner", "status",
        }

    def test_schema_status_enum(self):
        schema = json.loads((CONTRACTS / "goal.schema.json").read_text())
        assert set(schema["properties"]["status"]["enum"]) == {
            "planned", "active", "completed", "cancelled",
        }
