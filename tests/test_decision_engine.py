"""Tests for DecisionEngine (Sprint 23)."""

import copy
from dataclasses import asdict
from pathlib import Path

import pytest

from hermes.kernel.business_data_loader import BusinessData, BusinessDataLoader
from hermes.kernel.decision_engine import (
    DEFAULT_WEIGHTS,
    DIMENSIONS,
    DecisionEngine,
    DimensionScore,
    EngineConfig,
    EngineResult,
    Recommendation,
)
from hermes.models import (
    Bottleneck,
    Business,
    Decision,
    Experiment,
    Goal,
    KPI,
    Lesson,
    Opportunity,
    Strategy,
)

ROOT = Path(__file__).resolve().parents[1]
AKOSMICANIMALS = ROOT / "businesses" / "AKosmicAnimals"


# -- Helpers -------------------------------------------------------------------


def _make_business(business_id: str = "biz_test") -> Business:
    return Business(business_id=business_id, name="TestBiz", mission="Test")


def _make_strategy(business_id: str = "biz_test") -> Strategy:
    return Strategy(
        strategy_id="strat_001",
        business_id=business_id,
        title="Grow",
        objective="Grow the business",
    )


def _make_bottleneck(
    bot_id: str = "BOT-001",
    category: str = "product",
    impact: str = "high",
    status: str = "open",
) -> Bottleneck:
    return Bottleneck(
        bottleneck_id=bot_id,
        business_id="biz_test",
        title="Test bottleneck",
        category=category,
        impact=impact,
        status=status,
    )


def _make_opportunity(
    opp_id: str = "OPP-001",
    impact: str = "high",
    effort: str = "medium",
    status: str = "planned",
    strategy_id: str | None = None,
    goal_id: str | None = None,
) -> Opportunity:
    return Opportunity(
        opportunity_id=opp_id,
        business_id="biz_test",
        title="Test opportunity",
        description="Test opportunity",
        expected_impact=impact,
        estimated_effort=effort,
        owner="Alice",
        status=status,
        strategy_id=strategy_id,
        goal_id=goal_id,
    )


def _make_kpi(
    kpi_id: str = "KPI-001",
    current: float = 200.0,
    target: float = 1000.0,
    status: str = "off_track",
) -> KPI:
    return KPI(
        kpi_id=kpi_id,
        business_id="biz_test",
        goal_id="G-001",
        name="Revenue",
        unit="USD",
        current_value=current,
        target_value=target,
        frequency="monthly",
        status=status,
    )


def _make_lesson(
    les_id: str = "LES-001", source: str = "product",
) -> Lesson:
    return Lesson(
        lesson_id=les_id,
        business_id="biz_test",
        title="Test lesson",
        summary="Test lesson",
        source=source,
        recommendation="Do something",
        date="2026-07-19",
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


# -- DimensionScore -----------------------------------------------------------


class TestDimensionScore:
    def test_construction(self):
        ds = DimensionScore(dimension="risk", score=4, rationale="High risk")
        assert ds.dimension == "risk"
        assert ds.score == 4
        assert ds.rationale == "High risk"

    def test_asdict(self):
        ds = DimensionScore(dimension="urgency", score=5, rationale="Urgent")
        d = asdict(ds)
        assert d["dimension"] == "urgency"
        assert d["score"] == 5


# -- Recommendation -----------------------------------------------------------


class TestRecommendation:
    def test_construction(self):
        scores = [
            DimensionScore(d, 3, "test") for d in DIMENSIONS
        ]
        rec = Recommendation(
            recommendation_id="rec_bot_001",
            business_id="biz_test",
            title="Fix it",
            context="Something broken",
            rationale="It's important",
            source_type="bottleneck",
            source_id="BOT-001",
            dimension_scores=scores,
            priority_score=3.5,
            confidence="medium",
            suggested_action="experiment",
        )
        assert rec.recommendation_id == "rec_bot_001"
        assert rec.source_type == "bottleneck"
        assert len(rec.dimension_scores) == 7

    def test_asdict(self):
        scores = [DimensionScore("risk", 3, "test")]
        rec = Recommendation(
            recommendation_id="rec_x",
            business_id="biz_test",
            title="T",
            context="C",
            rationale="R",
            source_type="kpi",
            source_id="K-001",
            dimension_scores=scores,
            priority_score=2.0,
            confidence="low",
            suggested_action="monitor",
        )
        d = asdict(rec)
        assert isinstance(d, dict)
        assert d["source_type"] == "kpi"


# -- EngineConfig --------------------------------------------------------------


class TestEngineConfig:
    def test_default_weights(self):
        engine = DecisionEngine()
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001

    def test_custom_weights(self):
        custom = {d: 1.0 / 7 for d in DIMENSIONS}
        # Adjust last to sum exactly 1.0
        remainder = 1.0 - sum(list(custom.values())[:-1])
        custom[DIMENSIONS[-1]] = remainder
        engine = DecisionEngine(EngineConfig(weights=custom))
        data = _make_data(bottlenecks=[_make_bottleneck()])
        result = engine.evaluate(data)
        assert len(result.recommendations) == 1

    def test_invalid_weights_rejected(self):
        bad = {d: 0.5 for d in DIMENSIONS}  # sum = 3.5
        with pytest.raises(ValueError, match="sum to 1.0"):
            DecisionEngine(EngineConfig(weights=bad))

    def test_seven_dimensions(self):
        assert len(DIMENSIONS) == 7
        assert len(DEFAULT_WEIGHTS) == 7
        assert set(DIMENSIONS) == set(DEFAULT_WEIGHTS.keys())


# -- ComputePriority ----------------------------------------------------------


class TestComputePriority:
    def test_all_fives(self):
        engine = DecisionEngine()
        scores = [DimensionScore(d, 5, "max") for d in DIMENSIONS]
        assert engine._compute_priority(scores) == 5.0

    def test_all_ones(self):
        engine = DecisionEngine()
        scores = [DimensionScore(d, 1, "min") for d in DIMENSIONS]
        assert engine._compute_priority(scores) == 1.0

    def test_mixed(self):
        engine = DecisionEngine()
        scores = [DimensionScore(d, 3, "mid") for d in DIMENSIONS]
        assert engine._compute_priority(scores) == 3.0

    def test_weighted_calculation(self):
        engine = DecisionEngine()
        scores = [
            DimensionScore("strategic_alignment", 5, ""),  # 0.25
            DimensionScore("expected_impact", 5, ""),  # 0.25
            DimensionScore("required_effort", 1, ""),  # 0.15
            DimensionScore("risk", 1, ""),  # 0.10
            DimensionScore("urgency", 1, ""),  # 0.10
            DimensionScore("confidence", 1, ""),  # 0.10
            DimensionScore("historical_success", 1, ""),  # 0.05
        ]
        # 5*0.25 + 5*0.25 + 1*0.15 + 1*0.10 + 1*0.10 + 1*0.10 + 1*0.05
        # = 1.25 + 1.25 + 0.15 + 0.10 + 0.10 + 0.10 + 0.05 = 3.0
        assert engine._compute_priority(scores) == 3.0


# -- Confidence ----------------------------------------------------------------


class TestConfidence:
    def test_high(self):
        scores = [DimensionScore("confidence", 4, "")]
        assert DecisionEngine._determine_confidence(scores) == "high"

    def test_high_at_five(self):
        scores = [DimensionScore("confidence", 5, "")]
        assert DecisionEngine._determine_confidence(scores) == "high"

    def test_medium(self):
        scores = [DimensionScore("confidence", 3, "")]
        assert DecisionEngine._determine_confidence(scores) == "medium"

    def test_medium_at_two(self):
        scores = [DimensionScore("confidence", 2, "")]
        assert DecisionEngine._determine_confidence(scores) == "medium"

    def test_low(self):
        scores = [DimensionScore("confidence", 1, "")]
        assert DecisionEngine._determine_confidence(scores) == "low"

    def test_missing_dimension(self):
        scores = [DimensionScore("risk", 4, "")]
        assert DecisionEngine._determine_confidence(scores) == "low"


# -- Action --------------------------------------------------------------------


class TestAction:
    def test_decide(self):
        assert DecisionEngine._determine_action("high") == "decide"

    def test_experiment(self):
        assert DecisionEngine._determine_action("medium") == "experiment"

    def test_monitor(self):
        assert DecisionEngine._determine_action("low") == "monitor"


# -- Evaluate Bottlenecks -----------------------------------------------------


class TestEvaluateBottlenecks:
    def test_generates_recommendation(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert len(result.recommendations) == 1
        rec = result.recommendations[0]
        assert rec.source_type == "bottleneck"
        assert rec.source_id == "BOT-001"
        assert rec.recommendation_id == "rec_bot-001"

    def test_high_impact_scores_five(self):
        data = _make_data(bottlenecks=[_make_bottleneck(impact="high")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        impact_ds = [s for s in rec.dimension_scores if s.dimension == "expected_impact"][0]
        assert impact_ds.score == 5

    def test_low_impact_scores_two(self):
        data = _make_data(bottlenecks=[_make_bottleneck(impact="low")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        impact_ds = [s for s in rec.dimension_scores if s.dimension == "expected_impact"][0]
        assert impact_ds.score == 2

    def test_no_strategy_link_warning(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert any("No structured strategy link" in w for w in result.warnings)

    def test_no_effort_data_warning(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert any("No effort data" in w for w in result.warnings)

    def test_risk_score_is_four(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        risk_ds = [s for s in rec.dimension_scores if s.dimension == "risk"][0]
        assert risk_ds.score == 4

    def test_seven_dimension_scores(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        assert len(rec.dimension_scores) == 7
        dims = {s.dimension for s in rec.dimension_scores}
        assert dims == set(DIMENSIONS)


# -- Strategic Alignment -------------------------------------------------------


class TestStrategicAlignment:
    def test_bottleneck_always_neutral(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        sa = [s for s in rec.dimension_scores if s.dimension == "strategic_alignment"][0]
        assert sa.score == 3

    def test_opportunity_with_link_scores_five(self):
        opp = _make_opportunity(strategy_id="strat_001")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        sa = [s for s in rec.dimension_scores if s.dimension == "strategic_alignment"][0]
        assert sa.score == 5

    def test_opportunity_with_goal_link_scores_five(self):
        opp = _make_opportunity(goal_id="G-001")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        sa = [s for s in rec.dimension_scores if s.dimension == "strategic_alignment"][0]
        assert sa.score == 5

    def test_opportunity_no_link_neutral_plus_warning(self):
        opp = _make_opportunity()
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        sa = [s for s in rec.dimension_scores if s.dimension == "strategic_alignment"][0]
        assert sa.score == 3
        assert any("No structured strategy link" in w for w in result.warnings)

    def test_kpi_always_five(self):
        kpi = _make_kpi(current=200, target=1000)
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        rec = result.recommendations[0]
        sa = [s for s in rec.dimension_scores if s.dimension == "strategic_alignment"][0]
        assert sa.score == 5


# -- Impact Scoring ------------------------------------------------------------


class TestImpactScoring:
    def test_high_maps_to_five(self):
        data = _make_data(bottlenecks=[_make_bottleneck(impact="high")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "expected_impact"][0]
        assert ds.score == 5

    def test_medium_maps_to_three(self):
        data = _make_data(bottlenecks=[_make_bottleneck(impact="medium")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "expected_impact"][0]
        assert ds.score == 3

    def test_low_maps_to_two(self):
        data = _make_data(bottlenecks=[_make_bottleneck(impact="low")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "expected_impact"][0]
        assert ds.score == 2

    def test_kpi_large_gap(self):
        kpi = _make_kpi(current=100, target=1000)  # 90% gap
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "expected_impact"][0]
        assert ds.score == 5

    def test_kpi_moderate_gap(self):
        kpi = _make_kpi(current=600, target=1000)  # 40% gap
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "expected_impact"][0]
        assert ds.score == 3

    def test_kpi_small_gap(self):
        kpi = _make_kpi(current=800, target=1000)  # 20% gap
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "expected_impact"][0]
        assert ds.score == 2


# -- Effort Scoring ------------------------------------------------------------


class TestEffortScoring:
    def test_low_effort_maps_to_five(self):
        opp = _make_opportunity(effort="low")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "required_effort"][0]
        assert ds.score == 5

    def test_medium_effort_maps_to_three(self):
        opp = _make_opportunity(effort="medium")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "required_effort"][0]
        assert ds.score == 3

    def test_high_effort_maps_to_two(self):
        opp = _make_opportunity(effort="high")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "required_effort"][0]
        assert ds.score == 2

    def test_bottleneck_neutral_effort(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "required_effort"][0]
        assert ds.score == 3


# -- Urgency Scoring -----------------------------------------------------------


class TestUrgencyScoring:
    def test_open_maps_to_five(self):
        data = _make_data(bottlenecks=[_make_bottleneck(status="open")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "urgency"][0]
        assert ds.score == 5

    def test_active_maps_to_five(self):
        opp = _make_opportunity(status="active")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "urgency"][0]
        assert ds.score == 5

    def test_planned_maps_to_three(self):
        opp = _make_opportunity(status="planned")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "urgency"][0]
        assert ds.score == 3

    def test_backlog_maps_to_two(self):
        opp = _make_opportunity(status="backlog")
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "urgency"][0]
        assert ds.score == 2

    def test_unknown_status_maps_to_three(self):
        data = _make_data(bottlenecks=[_make_bottleneck(status="something_else")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "urgency"][0]
        assert ds.score == 3


# -- Historical Success --------------------------------------------------------


class TestHistoricalSuccess:
    def test_no_lessons_neutral(self):
        data = _make_data(bottlenecks=[_make_bottleneck(category="product")])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "historical_success"][0]
        assert ds.score == 3

    def test_one_matching_lesson(self):
        lesson = _make_lesson(source="product")
        data = _make_data(
            bottlenecks=[_make_bottleneck(category="product")],
            lessons=[lesson],
        )
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "historical_success"][0]
        assert ds.score == 4

    def test_two_matching_lessons(self):
        lessons = [
            _make_lesson("LES-001", source="product"),
            _make_lesson("LES-002", source="product"),
        ]
        data = _make_data(
            bottlenecks=[_make_bottleneck(category="product")],
            lessons=lessons,
        )
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "historical_success"][0]
        assert ds.score == 5

    def test_non_matching_lesson_stays_neutral(self):
        lesson = _make_lesson(source="incident")
        data = _make_data(
            bottlenecks=[_make_bottleneck(category="product")],
            lessons=[lesson],
        )
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "historical_success"][0]
        assert ds.score == 3


# -- Evaluate Opportunities ---------------------------------------------------


class TestEvaluateOpportunities:
    def test_generates_recommendation(self):
        opp = _make_opportunity()
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert len(result.recommendations) == 1
        assert result.recommendations[0].source_type == "opportunity"

    def test_risk_score_is_three(self):
        opp = _make_opportunity()
        data = _make_data(opportunities=[opp])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "risk"][0]
        assert ds.score == 3


# -- Evaluate KPIs ------------------------------------------------------------


class TestEvaluateKPIs:
    def test_below_target_generates_recommendation(self):
        kpi = _make_kpi(current=200, target=1000)
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert len(result.recommendations) == 1
        assert result.recommendations[0].source_type == "kpi"

    def test_at_target_no_recommendation(self):
        kpi = _make_kpi(current=1000, target=1000)
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert len(result.recommendations) == 0

    def test_above_target_no_recommendation(self):
        kpi = _make_kpi(current=1500, target=1000)
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert len(result.recommendations) == 0

    def test_risk_score_is_two(self):
        kpi = _make_kpi(current=200, target=1000)
        data = _make_data(kpis=[kpi])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ds = [s for s in result.recommendations[0].dimension_scores
              if s.dimension == "risk"][0]
        assert ds.score == 2


# -- Empty data ----------------------------------------------------------------


class TestEmptyData:
    def test_no_business_empty_result(self):
        data = BusinessData()
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert result.business_id == ""
        assert result.recommendations == []
        assert any("No business loaded" in w for w in result.warnings)

    def test_no_sources_empty_recommendations(self):
        data = _make_data()
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert result.recommendations == []


# -- Warning propagation -------------------------------------------------------


class TestWarningPropagation:
    def test_business_data_warnings_forwarded(self):
        data = _make_data(warnings=["Missing required file: Goals.md"])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert "Missing required file: Goals.md" in result.warnings

    def test_engine_warnings_added(self):
        data = _make_data(
            bottlenecks=[_make_bottleneck()],
            warnings=["loader warning"],
        )
        engine = DecisionEngine()
        result = engine.evaluate(data)
        # Both loader and engine warnings present
        assert "loader warning" in result.warnings
        assert any("No structured strategy link" in w for w in result.warnings)


# -- Ranking -------------------------------------------------------------------


class TestRanking:
    def test_sorted_descending(self):
        bots = [
            _make_bottleneck("BOT-001", impact="low"),
            _make_bottleneck("BOT-002", impact="high"),
        ]
        data = _make_data(bottlenecks=bots)
        engine = DecisionEngine()
        result = engine.evaluate(data)
        assert len(result.recommendations) == 2
        scores = [r.priority_score for r in result.recommendations]
        assert scores[0] >= scores[1]


# -- Deduplication -------------------------------------------------------------


class TestDeduplication:
    def test_no_duplicate_ids(self):
        bots = [_make_bottleneck(f"BOT-{i:03d}") for i in range(5)]
        data = _make_data(bottlenecks=bots)
        engine = DecisionEngine()
        result = engine.evaluate(data)
        ids = [r.recommendation_id for r in result.recommendations]
        assert len(ids) == len(set(ids))


# -- Statelessness -------------------------------------------------------------


class TestStatelessness:
    def test_same_result_twice(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        r1 = engine.evaluate(data)
        r2 = engine.evaluate(data)
        assert len(r1.recommendations) == len(r2.recommendations)
        assert r1.recommendations[0].priority_score == r2.recommendations[0].priority_score
        assert len(r1.warnings) == len(r2.warnings)


# -- No mutation ---------------------------------------------------------------


class TestNoMutation:
    def test_business_data_unchanged(self):
        bot = _make_bottleneck()
        data = _make_data(bottlenecks=[bot], warnings=["original"])
        original_warnings = list(data.warnings)
        original_bot_status = bot.status
        engine = DecisionEngine()
        engine.evaluate(data)
        assert data.warnings == original_warnings
        assert bot.status == original_bot_status

    def test_no_decision_objects_in_result(self):
        data = _make_data(bottlenecks=[_make_bottleneck()])
        engine = DecisionEngine()
        result = engine.evaluate(data)
        for rec in result.recommendations:
            assert isinstance(rec, Recommendation)


# -- Integration: AKosmicAnimals ----------------------------------------------


class TestAKosmicAnimalsEvaluation:
    """Integration tests against the real AKosmicAnimals directory."""

    @pytest.fixture()
    def result(self):
        loader = BusinessDataLoader()
        data = loader.load(AKOSMICANIMALS)
        engine = DecisionEngine()
        return engine.evaluate(data)

    def test_business_id(self, result):
        assert result.business_id == "biz_akosmicanimals"

    def test_four_bottleneck_recommendations(self, result):
        bot_recs = [r for r in result.recommendations if r.source_type == "bottleneck"]
        assert len(bot_recs) == 4

    def test_zero_kpi_recommendations(self, result):
        kpi_recs = [r for r in result.recommendations if r.source_type == "kpi"]
        assert len(kpi_recs) == 0

    def test_zero_opportunity_recommendations(self, result):
        opp_recs = [r for r in result.recommendations if r.source_type == "opportunity"]
        assert len(opp_recs) == 0

    def test_total_four_recommendations(self, result):
        assert len(result.recommendations) == 4

    def test_scores_in_range(self, result):
        for rec in result.recommendations:
            assert 1.0 <= rec.priority_score <= 5.0
            for ds in rec.dimension_scores:
                assert 1 <= ds.score <= 5

    def test_seven_dimensions_each(self, result):
        for rec in result.recommendations:
            assert len(rec.dimension_scores) == 7
            dims = {s.dimension for s in rec.dimension_scores}
            assert dims == set(DIMENSIONS)

    def test_sorted_descending(self, result):
        scores = [r.priority_score for r in result.recommendations]
        assert scores == sorted(scores, reverse=True)

    def test_suggested_actions_present(self, result):
        valid = {"decide", "experiment", "monitor"}
        for rec in result.recommendations:
            assert rec.suggested_action in valid

    def test_confidence_values_present(self, result):
        valid = {"low", "medium", "high"}
        for rec in result.recommendations:
            assert rec.confidence in valid

    def test_warnings_propagated(self, result):
        assert any("Goals.md" in w for w in result.warnings)

    def test_source_ids(self, result):
        bot_recs = [r for r in result.recommendations if r.source_type == "bottleneck"]
        source_ids = {r.source_id for r in bot_recs}
        assert source_ids == {"BOT-001", "BOT-002", "BOT-003", "BOT-004"}
