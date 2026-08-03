"""Tests for the 10 canonical business object dataclasses (Sprint 20)."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from hermes.models import (
    Bottleneck,
    Business,
    Capability,
    Decision,
    ExecutiveBrief,
    Experiment,
    Goal,
    KPI,
    Lesson,
    Operation,
    Opportunity,
    Strategy,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = ROOT / "examples"


def _load_schema(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text())


def _load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


def _validate_against_schema(instance: dict, schema: dict) -> None:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    assert not errors, f"Schema validation errors: {[e.message for e in errors]}"


# -- Business ----------------------------------------------------------------


class TestBusiness:
    def test_required_fields_only(self):
        b = Business(
            business_id="biz_1",
            name="Test",
            mission="Build things",
        )
        assert b.business_id == "biz_1"
        assert b.status is None
        assert b.created_at is None
        assert b.vision is None
        assert b.owner is None
        assert b.updated_at is None

    def test_all_fields(self):
        b = Business(
            business_id="biz_1",
            name="Test",
            mission="Build things",
            status="active",
            created_at="2026-07-19T09:00:00Z",
            vision="Scale",
            owner="AVANZIA",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert b.vision == "Scale"
        assert b.owner == "AVANZIA"

    def test_asdict_keys_match_schema(self):
        b = Business(
            business_id="biz_1",
            name="Test",
            mission="Build things",
            status="draft",
            created_at="2026-07-19T09:00:00Z",
        )
        d = asdict(b)
        schema = _load_schema("business.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("business.example.json")
        b = Business(**example)
        d = {k: v for k, v in asdict(b).items() if v is not None}
        _validate_against_schema(d, _load_schema("business.schema.json"))


# -- Strategy ----------------------------------------------------------------


class TestStrategy:
    def test_required_fields_only(self):
        s = Strategy(
            strategy_id="strat_1",
            business_id="biz_1",
            title="Growth",
            objective="Grow revenue",
        )
        assert s.strategy_id == "strat_1"
        assert s.status is None
        assert s.owner is None
        assert s.review_frequency is None
        assert s.created_at is None

    def test_all_fields(self):
        s = Strategy(
            strategy_id="strat_1",
            business_id="biz_1",
            title="Growth",
            objective="Grow revenue",
            status="active",
            owner="AVANZIA",
            review_frequency="monthly",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert s.review_frequency == "monthly"

    def test_asdict_keys_match_schema(self):
        s = Strategy(
            strategy_id="strat_1",
            business_id="biz_1",
            title="Growth",
            objective="Grow revenue",
            status="draft",
        )
        d = asdict(s)
        schema = _load_schema("strategy.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("strategy.example.json")
        s = Strategy(**example)
        d = {k: v for k, v in asdict(s).items() if v is not None}
        _validate_against_schema(d, _load_schema("strategy.schema.json"))


# -- Goal --------------------------------------------------------------------


class TestGoal:
    def test_required_fields_only(self):
        g = Goal(
            goal_id="goal_1",
            business_id="biz_1",
            title="Revenue",
            description="Hit target",
            target_value="1000 USD",
            target_date="2027-07-19",
            owner="AVANZIA",
            status="planned",
        )
        assert g.strategy_id is None
        assert g.created_at is None

    def test_all_fields(self):
        g = Goal(
            goal_id="goal_1",
            business_id="biz_1",
            title="Revenue",
            description="Hit target",
            target_value="1000 USD",
            target_date="2027-07-19",
            owner="AVANZIA",
            status="active",
            strategy_id="strat_1",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert g.strategy_id == "strat_1"

    def test_asdict_keys_match_schema(self):
        g = Goal(
            goal_id="goal_1",
            business_id="biz_1",
            title="Revenue",
            description="Hit target",
            target_value="1000 USD",
            target_date="2027-07-19",
            owner="AVANZIA",
            status="planned",
        )
        d = asdict(g)
        schema = _load_schema("goal.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("goal.example.json")
        g = Goal(**example)
        d = {k: v for k, v in asdict(g).items() if v is not None}
        _validate_against_schema(d, _load_schema("goal.schema.json"))


# -- KPI ---------------------------------------------------------------------


class TestKPI:
    def test_required_fields_only(self):
        k = KPI(
            kpi_id="kpi_1",
            business_id="biz_1",
            goal_id="goal_1",
            name="Revenue",
            unit="USD",
            current_value=0,
            target_value=1000,
            frequency="monthly",
            status="on_track",
        )
        assert k.owner is None
        assert k.created_at is None

    def test_all_fields(self):
        k = KPI(
            kpi_id="kpi_1",
            business_id="biz_1",
            goal_id="goal_1",
            name="Revenue",
            unit="USD",
            current_value=500,
            target_value=1000,
            frequency="monthly",
            status="at_risk",
            owner="AVANZIA",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert k.current_value == 500

    def test_asdict_keys_match_schema(self):
        k = KPI(
            kpi_id="kpi_1",
            business_id="biz_1",
            goal_id="goal_1",
            name="Revenue",
            unit="USD",
            current_value=0,
            target_value=1000,
            frequency="monthly",
            status="on_track",
        )
        d = asdict(k)
        schema = _load_schema("kpi.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("kpi.example.json")
        k = KPI(**example)
        d = {k_: v for k_, v in asdict(k).items() if v is not None}
        _validate_against_schema(d, _load_schema("kpi.schema.json"))


# -- Bottleneck --------------------------------------------------------------


class TestBottleneck:
    def test_required_fields_only(self):
        b = Bottleneck(
            bottleneck_id="BOT-001",
            business_id="biz_1",
            title="Slow pipeline",
            category="operations",
            impact="high",
            status="open",
        )
        assert b.description is None
        assert b.owner is None

    def test_all_fields(self):
        b = Bottleneck(
            bottleneck_id="BOT-001",
            business_id="biz_1",
            title="Slow pipeline",
            category="operations",
            impact="high",
            status="mitigating",
            description="Details here",
            owner="AVANZIA",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert b.description == "Details here"

    def test_asdict_keys_match_schema(self):
        b = Bottleneck(
            bottleneck_id="BOT-001",
            business_id="biz_1",
            title="Slow pipeline",
            category="operations",
            impact="high",
            status="open",
        )
        d = asdict(b)
        schema = _load_schema("bottleneck.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("bottleneck.example.json")
        b = Bottleneck(**example)
        d = {k: v for k, v in asdict(b).items() if v is not None}
        _validate_against_schema(d, _load_schema("bottleneck.schema.json"))


# -- Opportunity -------------------------------------------------------------


class TestOpportunity:
    def test_required_fields_only(self):
        o = Opportunity(
            opportunity_id="OPP-001",
            business_id="biz_1",
            title="New channel",
            description="Expand to new channel",
            expected_impact="high",
            estimated_effort="medium",
            owner="AVANZIA",
            status="backlog",
        )
        assert o.strategy_id is None
        assert o.goal_id is None
        assert o.decision_id is None
        assert o.experiment_id is None

    def test_all_fields(self):
        o = Opportunity(
            opportunity_id="OPP-001",
            business_id="biz_1",
            title="New channel",
            description="Expand to new channel",
            expected_impact="high",
            estimated_effort="medium",
            owner="AVANZIA",
            status="active",
            strategy_id="strat_1",
            goal_id="goal_1",
            decision_id="DEC-001",
            experiment_id="EXP-001",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert o.strategy_id == "strat_1"

    def test_asdict_keys_match_schema(self):
        o = Opportunity(
            opportunity_id="OPP-001",
            business_id="biz_1",
            title="New channel",
            description="Expand",
            expected_impact="high",
            estimated_effort="medium",
            owner="AVANZIA",
            status="backlog",
        )
        d = asdict(o)
        schema = _load_schema("opportunity.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("opportunity.example.json")
        o = Opportunity(**example)
        d = {k: v for k, v in asdict(o).items() if v is not None}
        _validate_against_schema(d, _load_schema("opportunity.schema.json"))


# -- Decision ----------------------------------------------------------------


class TestDecision:
    def test_required_fields_only(self):
        d = Decision(
            decision_id="DEC-001",
            business_id="biz_1",
            title="Use tool X",
            context="Needed a tool",
            rationale="Best option",
            status="proposed",
            decision_date="2026-07-19",
        )
        assert d.strategy_id is None
        assert d.goal_id is None
        assert d.owner is None

    def test_all_fields(self):
        d = Decision(
            decision_id="DEC-001",
            business_id="biz_1",
            title="Use tool X",
            context="Needed a tool",
            rationale="Best option",
            status="implemented",
            decision_date="2026-07-19",
            strategy_id="strat_1",
            goal_id="goal_1",
            owner="AVANZIA",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert d.strategy_id == "strat_1"

    def test_asdict_keys_match_schema(self):
        d = Decision(
            decision_id="DEC-001",
            business_id="biz_1",
            title="Use tool X",
            context="Needed a tool",
            rationale="Best option",
            status="proposed",
            decision_date="2026-07-19",
        )
        data = asdict(d)
        schema = _load_schema("decision.schema.json")
        assert set(data.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("decision.example.json")
        d = Decision(**example)
        data = {k: v for k, v in asdict(d).items() if v is not None}
        _validate_against_schema(data, _load_schema("decision.schema.json"))


# -- Experiment --------------------------------------------------------------


class TestExperiment:
    def test_required_fields_only(self):
        e = Experiment(
            experiment_id="EXP-001",
            business_id="biz_1",
            title="A/B test",
            hypothesis="A is better than B",
            success_criteria="Higher CTR",
            status="planned",
        )
        assert e.opportunity_id is None
        assert e.decision_id is None
        assert e.outcome is None
        assert e.owner is None
        assert e.start_date is None
        assert e.end_date is None

    def test_all_fields(self):
        e = Experiment(
            experiment_id="EXP-001",
            business_id="biz_1",
            title="A/B test",
            hypothesis="A is better than B",
            success_criteria="Higher CTR",
            status="completed",
            opportunity_id="OPP-001",
            decision_id="DEC-001",
            outcome="success",
            owner="AVANZIA",
            start_date="2026-07-19",
            end_date="2026-08-19",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-08-19T10:00:00Z",
        )
        assert e.outcome == "success"

    def test_asdict_keys_match_schema(self):
        e = Experiment(
            experiment_id="EXP-001",
            business_id="biz_1",
            title="A/B test",
            hypothesis="A is better than B",
            success_criteria="Higher CTR",
            status="planned",
        )
        d = asdict(e)
        schema = _load_schema("experiment.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("experiment.example.json")
        e = Experiment(**example)
        d = {k: v for k, v in asdict(e).items() if v is not None}
        _validate_against_schema(d, _load_schema("experiment.schema.json"))


# -- Lesson ------------------------------------------------------------------


class TestLesson:
    def test_required_fields_only(self):
        l = Lesson(
            lesson_id="LES-001",
            business_id="biz_1",
            title="Learned something",
            summary="Summary here",
            source="review",
            recommendation="Do this next time",
            date="2026-07-19",
        )
        assert l.decision_id is None
        assert l.experiment_id is None
        assert l.owner is None

    def test_all_fields(self):
        l = Lesson(
            lesson_id="LES-001",
            business_id="biz_1",
            title="Learned something",
            summary="Summary here",
            source="experiment",
            recommendation="Do this next time",
            date="2026-07-19",
            decision_id="DEC-001",
            experiment_id="EXP-001",
            owner="AVANZIA",
            created_at="2026-07-19T09:00:00Z",
            updated_at="2026-07-19T10:00:00Z",
        )
        assert l.decision_id == "DEC-001"

    def test_asdict_keys_match_schema(self):
        l = Lesson(
            lesson_id="LES-001",
            business_id="biz_1",
            title="Learned something",
            summary="Summary here",
            source="review",
            recommendation="Do this next time",
            date="2026-07-19",
        )
        d = asdict(l)
        schema = _load_schema("lesson.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("lesson.example.json")
        l = Lesson(**example)
        d = {k: v for k, v in asdict(l).items() if v is not None}
        _validate_against_schema(d, _load_schema("lesson.schema.json"))


# -- ExecutiveBrief ----------------------------------------------------------


class TestExecutiveBrief:
    def test_required_fields_only(self):
        eb = ExecutiveBrief(
            brief_id="brief_1",
            business_id="biz_1",
            reporting_period="monthly",
            generated_at="2026-07-19T09:00:00Z",
            summary="All good",
            priorities=["Grow"],
            recommendations=["Do more"],
            status="draft",
        )
        assert eb.risks == []
        assert eb.created_at is None

    def test_all_fields(self):
        eb = ExecutiveBrief(
            brief_id="brief_1",
            business_id="biz_1",
            reporting_period="monthly",
            generated_at="2026-07-19T09:00:00Z",
            summary="All good",
            priorities=["Grow"],
            recommendations=["Do more"],
            status="published",
            risks=["Market risk"],
            created_at="2026-07-19T09:00:00Z",
        )
        assert eb.risks == ["Market risk"]

    def test_asdict_keys_match_schema(self):
        eb = ExecutiveBrief(
            brief_id="brief_1",
            business_id="biz_1",
            reporting_period="monthly",
            generated_at="2026-07-19T09:00:00Z",
            summary="All good",
            priorities=["Grow"],
            recommendations=["Do more"],
            status="draft",
        )
        d = asdict(eb)
        schema = _load_schema("executive-brief.schema.json")
        assert set(d.keys()) <= set(schema["properties"].keys())

    def test_example_validates_against_schema(self):
        example = _load_example("executive-brief.example.json")
        eb = ExecutiveBrief(**example)
        d = {k: v for k, v in asdict(eb).items() if v is not None}
        # Keep empty lists (risks can be empty but still valid)
        for key in ("priorities", "risks", "recommendations"):
            if key not in d and key in example:
                d[key] = example[key]
        _validate_against_schema(d, _load_schema("executive-brief.schema.json"))


# -- Operation (Business Knowledge) -------------------------------------------


class TestOperationBK:
    """Validate Operation schema and example for the Business Knowledge layer.

    The Operation model is dual-purpose (workspace YAML + BK markdown).
    The BK schema uses operation_id/business_id while the model uses id/workspace_id.
    These tests validate the schema and example independently.
    """

    def test_operation_has_bk_fields(self):
        """Operation model includes all BK-relevant fields."""
        from datetime import datetime, timezone
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        op = Operation(
            id="OPS-001",
            workspace_id="biz_1",
            request="Test",
            status="completed",
            created_at=now,
            updated_at=now,
            outcome="Done",
            outcome_classification="success",
            decision_id="DEC-001",
            recommendation_id="rec_001",
            review_id="review_001",
        )
        assert op.outcome == "Done"
        assert op.outcome_classification == "success"
        assert op.decision_id == "DEC-001"

    def test_example_validates_against_schema(self):
        example = _load_example("operation.example.json")
        schema = _load_schema("operation.schema.json")
        _validate_against_schema(example, schema)

    def test_schema_required_fields(self):
        schema = _load_schema("operation.schema.json")
        assert set(schema["required"]) == {
            "operation_id", "business_id", "title", "status", "operation_date",
        }


# -- Capability ----------------------------------------------------------------


class TestCapability:
    def test_required_fields_only(self):
        c = Capability(
            id="python",
            name="Python",
            version="1.0.0",
            provides=["python dev"],
            keywords=["python"],
        )
        assert c.description == ""
        assert c.status == "active"
        assert c.inputs == []
        assert c.outputs == []
        assert c.sop_ref is None
        assert c.sop_refs == []
        assert c.owner is None
        assert c.depends_on == []

    def test_all_fields(self):
        c = Capability(
            id="copywriting",
            name="Copywriting",
            version="1.0.0",
            provides=["marketing copy"],
            keywords=["copy"],
            description="Drafts copy",
            inputs=["brief"],
            outputs=["draft"],
            sop_ref="sops/copy.md",
            sop_refs=["copywriting/content-review"],
            status="active",
            skill_id="copywriting",
            owner="Marketing",
            depends_on=["brand-strategy"],
        )
        assert c.owner == "Marketing"
        assert c.depends_on == ["brand-strategy"]
        assert c.sop_refs == ["copywriting/content-review"]

    def test_example_validates_against_schema(self):
        example = _load_example("capability.example.json")
        schema = _load_schema("capability.schema.json")
        _validate_against_schema(example, schema)

    def test_schema_required_fields(self):
        schema = _load_schema("capability.schema.json")
        assert set(schema["required"]) == {"id", "name", "version", "status"}

    def test_all_status_values_valid(self):
        from hermes.models.capability import CAPABILITY_STATUSES
        for status in ("draft", "active", "experimental", "deprecated"):
            assert status in CAPABILITY_STATUSES


# -- SOP -----------------------------------------------------------------------


class TestSOP:
    def test_required_fields(self):
        from hermes.models.sop import SOP
        s = SOP(
            id="test/my-sop",
            title="My SOP",
            skill_id="test",
            filename="my-sop.md",
            content="# My SOP\n\nContent.",
        )
        assert s.description == ""
        assert s.version == ""
        assert s.status == "active"
        assert s.owner is None
        assert s.category is None

    def test_all_fields(self):
        from hermes.models.sop import SOP
        s = SOP(
            id="copywriting/content-review",
            title="Content Review Process",
            skill_id="copywriting",
            filename="content-review.md",
            content="# Content Review Process\n\nDetails.",
            description="Standard procedure.",
            version="1.0.0",
            status="active",
            owner="Marketing",
            category="Marketing",
        )
        assert s.category == "Marketing"
        assert s.owner == "Marketing"

    def test_example_validates_against_schema(self):
        example = _load_example("sop.example.json")
        schema = _load_schema("sop.schema.json")
        _validate_against_schema(example, schema)

    def test_schema_required_fields(self):
        schema = _load_schema("sop.schema.json")
        assert set(schema["required"]) == {
            "id", "title", "skill_id", "filename", "content", "status",
        }

    def test_all_status_values_valid(self):
        from hermes.models.sop import SOP_STATUSES
        for status in ("draft", "active", "deprecated"):
            assert status in SOP_STATUSES

    def test_category_values(self):
        from hermes.models.sop import SOP_CATEGORIES
        for cat in ("Operational", "Technical", "Business", "Marketing", "Sales"):
            assert cat in SOP_CATEGORIES
