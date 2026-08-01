"""Tests for ExecutiveBriefGenerator (Sprint 24)."""

from dataclasses import field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hermes.kernel.brief_generator import (
    ExecutiveBriefGenerator,
    GeneratorResult,
    _compute_overall_confidence,
)
from hermes.kernel.business_data_loader import BusinessData, BusinessDataLoader
from hermes.kernel.decision_engine import (
    DecisionEngine,
    DimensionScore,
    EngineResult,
    Recommendation,
)
from hermes.models import (
    Bottleneck,
    Business,
    ExecutiveBrief,
    KPI,
    Strategy,
)

ROOT = Path(__file__).resolve().parents[1]
AKOSMICANIMALS = ROOT / "businesses" / "AKosmicAnimals"


# -- Helpers -------------------------------------------------------------------


def _make_business(business_id: str = "biz_test") -> Business:
    return Business(business_id=business_id, name="TestBiz", mission="Test")


def _make_strategy() -> Strategy:
    return Strategy(
        strategy_id="strat_001",
        business_id="biz_test",
        title="Grow revenue",
        objective="Grow the business revenue",
    )


def _make_bottleneck(
    bot_id: str = "BOT-001", impact: str = "high", status: str = "open",
) -> Bottleneck:
    return Bottleneck(
        bottleneck_id=bot_id,
        business_id="biz_test",
        title="Test bottleneck",
        category="product",
        impact=impact,
        status=status,
    )


def _make_kpi(
    kpi_id: str = "KPI-001",
    name: str = "Revenue",
    current: float = 200.0,
    target: float = 1000.0,
    status: str = "off_track",
) -> KPI:
    return KPI(
        kpi_id=kpi_id,
        business_id="biz_test",
        goal_id="G-001",
        name=name,
        unit="USD",
        current_value=current,
        target_value=target,
        frequency="monthly",
        status=status,
    )


def _make_rec(
    rec_id: str = "rec_001",
    title: str = "Do something",
    rationale: str = "Because reasons",
    priority: float = 3.5,
    confidence: str = "medium",
    action: str = "experiment",
) -> Recommendation:
    scores = [
        DimensionScore(d, 3, "test")
        for d in [
            "strategic_alignment", "expected_impact", "required_effort",
            "risk", "urgency", "confidence", "historical_success",
        ]
    ]
    return Recommendation(
        recommendation_id=rec_id,
        business_id="biz_test",
        title=title,
        context="Context",
        rationale=rationale,
        source_type="bottleneck",
        source_id="BOT-001",
        dimension_scores=scores,
        priority_score=priority,
        confidence=confidence,
        suggested_action=action,
    )


def _make_data(**kwargs) -> BusinessData:
    defaults = {
        "business": _make_business(),
        "strategy": _make_strategy(),
        "goals": [],
        "kpis": [],
        "bottlenecks": [],
        "opportunities": [],
        "decisions": [],
        "experiments": [],
        "lessons": [],
        "warnings": [],
    }
    defaults.update(kwargs)
    return BusinessData(**defaults)


def _make_engine_result(**kwargs) -> EngineResult:
    defaults = {
        "business_id": "biz_test",
        "recommendations": [],
        "warnings": [],
    }
    defaults.update(kwargs)
    return EngineResult(**defaults)


# -- GeneratorResult -----------------------------------------------------------


class TestGeneratorResult:
    def test_construction(self):
        brief = ExecutiveBrief(
            brief_id="b1", business_id="biz_test",
            reporting_period="daily", generated_at="2026-08-01T00:00:00+00:00",
            summary="S", priorities=[], recommendations=[], status="draft",
        )
        result = GeneratorResult(brief=brief, warnings=["w1"])
        assert result.brief is brief
        assert result.warnings == ["w1"]

    def test_default_warnings(self):
        brief = ExecutiveBrief(
            brief_id="b1", business_id="biz_test",
            reporting_period="daily", generated_at="2026-08-01T00:00:00+00:00",
            summary="S", priorities=[], recommendations=[], status="draft",
        )
        result = GeneratorResult(brief=brief)
        assert result.warnings == []


# -- Build Summary (structured briefing) --------------------------------------


class TestBuildSummary:
    def test_business_name_in_summary(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Business: TestBiz" in result.brief.summary

    def test_strategy_title_in_summary(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Strategy: Grow revenue" in result.brief.summary

    def test_no_strategy_fallback(self):
        data = _make_data(strategy=None)
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Strategy: No strategy defined" in result.brief.summary

    def test_bottleneck_count(self):
        bots = [_make_bottleneck("BOT-001", "high"), _make_bottleneck("BOT-002", "medium")]
        data = _make_data(bottlenecks=bots)
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Bottlenecks: 2 active (1 high impact)" in result.brief.summary

    def test_period_in_summary(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er, reporting_period="weekly")
        assert "Period: weekly" in result.brief.summary

    def test_kpi_status_section(self):
        kpi = _make_kpi(name="Revenue", current=200, target=1000, status="off_track")
        data = _make_data(kpis=[kpi])
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "KPI Status:" in result.brief.summary
        assert "- Revenue: 200/1000 USD (off_track)" in result.brief.summary

    def test_no_kpi_fallback(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "- No KPI data available." in result.brief.summary

    def test_overall_confidence_in_summary(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Overall Confidence:" in result.brief.summary

    def test_recommendation_count(self):
        recs = [_make_rec("r1"), _make_rec("r2")]
        data = _make_data()
        er = _make_engine_result(recommendations=recs)
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Recommendations: 2 generated" in result.brief.summary

    def test_structured_format_has_labeled_lines(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        lines = result.brief.summary.split("\n")
        labeled = [l for l in lines if l.strip() and ":" in l]
        assert len(labeled) >= 8  # Business, Strategy, Period, counts, KPI Status, Confidence


# -- Overall Confidence --------------------------------------------------------


class TestOverallConfidence:
    def test_all_high(self):
        recs = [_make_rec(confidence="high"), _make_rec("r2", confidence="high")]
        assert _compute_overall_confidence(recs) == "high"

    def test_any_low(self):
        recs = [_make_rec(confidence="high"), _make_rec("r2", confidence="low")]
        assert _compute_overall_confidence(recs) == "low"

    def test_mixed_medium(self):
        recs = [_make_rec(confidence="high"), _make_rec("r2", confidence="medium")]
        assert _compute_overall_confidence(recs) == "medium"

    def test_no_recs(self):
        assert _compute_overall_confidence([]) == "insufficient_data"

    def test_all_medium(self):
        recs = [_make_rec(confidence="medium")]
        assert _compute_overall_confidence(recs) == "medium"


# -- Build Priorities ----------------------------------------------------------


class TestBuildPriorities:
    def test_top_five(self):
        recs = [_make_rec(f"r{i}", title=f"Rec {i}", priority=5.0 - i * 0.5) for i in range(7)]
        data = _make_data()
        er = _make_engine_result(recommendations=recs)
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.priorities) == 5

    def test_fewer_than_five(self):
        recs = [_make_rec("r1"), _make_rec("r2")]
        data = _make_data()
        er = _make_engine_result(recommendations=recs)
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.priorities) == 2

    def test_empty_emits_warning(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert result.brief.priorities == []
        assert any("No recommendations available" in w for w in result.warnings)

    def test_format_includes_score_and_action(self):
        rec = _make_rec(title="Fix thing", priority=3.9, action="decide")
        data = _make_data()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "[3.9] Fix thing (decide)" in result.brief.priorities[0]


# -- Build Recommendations ----------------------------------------------------


class TestBuildRecommendations:
    def test_decide_format(self):
        rec = _make_rec(title="Fix it", rationale="Broken", action="decide")
        data = _make_data()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Fix it: Broken" in result.brief.recommendations

    def test_experiment_format(self):
        rec = _make_rec(title="Try it", rationale="Maybe works", action="experiment")
        data = _make_data()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Experiment: Try it — Maybe works" in result.brief.recommendations

    def test_monitor_format(self):
        rec = _make_rec(title="Watch it", action="monitor")
        data = _make_data()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "Monitor: Watch it" in result.brief.recommendations

    def test_empty_emits_warning(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert result.brief.recommendations == []
        assert any("No recommendations generated" in w for w in result.warnings)


# -- Build Risks (factual only) -----------------------------------------------


class TestBuildRisks:
    def test_high_impact_bottleneck_included(self):
        bot = _make_bottleneck("BOT-001", impact="high", status="open")
        data = _make_data(bottlenecks=[bot])
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.risks) == 1
        assert "BOT-001" in result.brief.risks[0]
        assert "impact: high" in result.brief.risks[0]
        assert "status: open" in result.brief.risks[0]

    def test_low_impact_bottleneck_excluded(self):
        bot = _make_bottleneck("BOT-001", impact="low")
        data = _make_data(bottlenecks=[bot])
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.risks) == 0

    def test_medium_impact_excluded(self):
        bot = _make_bottleneck("BOT-001", impact="medium")
        data = _make_data(bottlenecks=[bot])
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.risks) == 0

    def test_off_track_kpi_included(self):
        kpi = _make_kpi(status="off_track")
        data = _make_data(kpis=[kpi])
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.risks) == 1
        assert "off_track" in result.brief.risks[0]

    def test_on_track_kpi_excluded(self):
        kpi = _make_kpi(status="on_track")
        data = _make_data(kpis=[kpi])
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert len(result.brief.risks) == 0

    def test_low_confidence_NOT_in_risks(self):
        rec = _make_rec(confidence="low", title="Uncertain thing")
        data = _make_data()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        for risk in result.brief.risks:
            assert "confidence" not in risk.lower()
            assert "Uncertain thing" not in risk

    def test_low_confidence_in_warnings_instead(self):
        rec = _make_rec(confidence="low", title="Uncertain thing")
        data = _make_data()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert any(
            "Low confidence recommendation: Uncertain thing" in w
            for w in result.warnings
        )

    def test_empty_risks_no_warning(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert result.brief.risks == []
        assert not any("No risks" in w for w in result.warnings)


# -- Reporting Period ----------------------------------------------------------


class TestReportingPeriod:
    def test_valid_period(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        for period in ("daily", "weekly", "monthly", "quarterly", "annual"):
            result = gen.generate(data, er, reporting_period=period)
            assert result.brief.reporting_period == period

    def test_invalid_period_defaults(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er, reporting_period="biweekly")
        assert result.brief.reporting_period == "daily"
        assert any("Invalid reporting_period" in w for w in result.warnings)


# -- Status --------------------------------------------------------------------


class TestStatus:
    def test_always_draft(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert result.brief.status == "draft"


# -- Empty / No Business ------------------------------------------------------


class TestEmptyData:
    def test_no_business_empty_brief(self):
        data = BusinessData()
        er = _make_engine_result(business_id="")
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert result.brief.business_id == ""
        assert result.brief.priorities == []
        assert result.brief.recommendations == []
        assert any("No business loaded" in w for w in result.warnings)

    def test_no_recommendations_empty_priorities(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert result.brief.priorities == []
        assert result.brief.recommendations == []


# -- Warning Propagation -------------------------------------------------------


class TestWarningPropagation:
    def test_engine_warnings_forwarded(self):
        data = _make_data()
        er = _make_engine_result(warnings=["engine warning 1", "engine warning 2"])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "engine warning 1" in result.warnings
        assert "engine warning 2" in result.warnings

    def test_generator_warnings_added(self):
        data = _make_data()
        er = _make_engine_result(warnings=["engine w"])
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "engine w" in result.warnings
        # Generator adds its own warnings (no recs → no priorities, no recommendations)
        assert any("No recommendations" in w for w in result.warnings)


# -- No Mutation ---------------------------------------------------------------


class TestNoMutation:
    def test_business_data_unchanged(self):
        bot = _make_bottleneck()
        data = _make_data(bottlenecks=[bot], warnings=["original"])
        original_warnings = list(data.warnings)
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        gen.generate(data, er)
        assert data.warnings == original_warnings
        assert bot.status == "open"

    def test_engine_result_unchanged(self):
        er = _make_engine_result(warnings=["eng"])
        original_warnings = list(er.warnings)
        data = _make_data()
        gen = ExecutiveBriefGenerator()
        gen.generate(data, er)
        assert er.warnings == original_warnings


# -- Statelessness -------------------------------------------------------------


class TestStatelessness:
    def test_two_calls_equivalent(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        rec = _make_rec()
        er = _make_engine_result(recommendations=[rec])
        gen = ExecutiveBriefGenerator()
        r1 = gen.generate(data, er)
        r2 = gen.generate(data, er)
        assert r1.brief.summary == r2.brief.summary
        assert r1.brief.priorities == r2.brief.priorities
        assert r1.brief.recommendations == r2.brief.recommendations
        assert r1.brief.risks == r2.brief.risks
        assert len(r1.warnings) == len(r2.warnings)


# -- Generated At / Created At ------------------------------------------------


class TestTimestamps:
    def test_generated_at_is_iso(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        # Should parse without error
        dt = datetime.fromisoformat(result.brief.generated_at)
        assert dt.tzinfo is not None

    def test_created_at_is_iso(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        dt = datetime.fromisoformat(result.brief.created_at)
        assert dt.tzinfo is not None

    def test_brief_id_contains_business_id(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert "biz_test" in result.brief.brief_id


# -- Brief is canonical model --------------------------------------------------


class TestCanonicalModel:
    def test_returns_executive_brief(self):
        data = _make_data()
        er = _make_engine_result()
        gen = ExecutiveBriefGenerator()
        result = gen.generate(data, er)
        assert isinstance(result.brief, ExecutiveBrief)


# -- Integration: AKosmicAnimals ----------------------------------------------


class TestAKosmicAnimalsIntegration:
    """Full pipeline: BusinessDataLoader → DecisionEngine → ExecutiveBriefGenerator."""

    @pytest.fixture()
    def result(self):
        loader = BusinessDataLoader()
        data = loader.load(AKOSMICANIMALS)
        engine = DecisionEngine()
        engine_result = engine.evaluate(data)
        gen = ExecutiveBriefGenerator()
        return gen.generate(data, engine_result)

    def test_brief_is_executive_brief(self, result):
        assert isinstance(result.brief, ExecutiveBrief)

    def test_business_id(self, result):
        assert result.brief.business_id == "biz_akosmicanimals"

    def test_status_is_draft(self, result):
        assert result.brief.status == "draft"

    def test_reporting_period(self, result):
        assert result.brief.reporting_period == "daily"

    def test_summary_mentions_business(self, result):
        assert "Business: AKosmicAnimals" in result.brief.summary

    def test_summary_has_strategy(self, result):
        assert "Strategy:" in result.brief.summary

    def test_summary_has_bottleneck_count(self, result):
        assert "Bottlenecks: 4 active" in result.brief.summary

    def test_summary_has_confidence(self, result):
        assert "Overall Confidence:" in result.brief.summary

    def test_summary_has_kpi_status(self, result):
        assert "KPI Status:" in result.brief.summary
        assert "No KPI data available." in result.brief.summary

    def test_four_priorities(self, result):
        assert len(result.brief.priorities) == 4

    def test_four_recommendations(self, result):
        assert len(result.brief.recommendations) == 4

    def test_risks_are_factual(self, result):
        # BOT-001 and BOT-002 have high impact
        assert len(result.brief.risks) == 2
        assert any("BOT-001" in r for r in result.brief.risks)
        assert any("BOT-002" in r for r in result.brief.risks)

    def test_no_analytical_uncertainty_in_risks(self, result):
        for risk in result.brief.risks:
            assert "confidence" not in risk.lower()

    def test_warnings_propagated(self, result):
        assert any("Goals.md" in w for w in result.warnings)

    def test_generated_at_valid(self, result):
        dt = datetime.fromisoformat(result.brief.generated_at)
        assert dt.tzinfo is not None
