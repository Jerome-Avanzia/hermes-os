"""Integration tests for AVANZIA Business Knowledge loading.

Validates that businesses/AVANZIA/ loads through BusinessDataLoader
with zero warnings and produces the expected typed objects.
"""

from pathlib import Path

from hermes.kernel.business_data_loader import BusinessDataLoader


_AVANZIA_DIR = Path(__file__).resolve().parent.parent / "businesses" / "AVANZIA"


def test_avanzia_loads_without_warnings():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert data.warnings == [], f"Unexpected warnings: {data.warnings}"


def test_avanzia_business_object():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert data.business is not None
    assert data.business.name == "AVANZIA"
    assert data.business.business_id == "biz_avanzia"
    assert "AI-powered businesses" in data.business.mission


def test_avanzia_strategy_object():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert data.strategy is not None
    assert data.strategy.business_id == "biz_avanzia"
    assert "AI-native venture company" in data.strategy.objective
    assert data.strategy.review_frequency == "monthly"


def test_avanzia_goals_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.goals) == 5
    ids = [g.goal_id for g in data.goals]
    assert ids == ["GOAL-001", "GOAL-002", "GOAL-003", "GOAL-004", "GOAL-005"]
    assert all(g.status == "active" for g in data.goals)
    assert all(g.owner == "Founder" for g in data.goals)
    assert all(g.target_date == "2027-07-31" for g in data.goals)


def test_avanzia_bottlenecks_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.bottlenecks) == 5
    assert data.bottlenecks[0].bottleneck_id == "BOT-001"
    assert data.bottlenecks[0].status == "mitigating"


def test_avanzia_opportunities_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.opportunities) == 6
    assert data.opportunities[0].opportunity_id == "OPP-001"
    assert data.opportunities[0].status == "active"


def test_avanzia_decisions_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.decisions) == 6
    assert all(d.status == "implemented" for d in data.decisions)


def test_avanzia_experiments_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.experiments) == 5
    statuses = [e.status for e in data.experiments]
    assert statuses.count("running") == 3
    assert statuses.count("planned") == 2


def test_avanzia_lessons_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.lessons) == 6
    sources = {l.source for l in data.lessons}
    assert sources == {"project", "decision", "experiment", "review"}


def test_avanzia_kpis_loaded():
    data = BusinessDataLoader().load(_AVANZIA_DIR)
    assert len(data.kpis) == 8
    assert data.kpis[0].name == "Active Ventures"
    assert data.kpis[0].goal_id == "GOAL-001"
    assert data.kpis[0].current_value == 1.0
    assert data.kpis[0].target_value == 2.0
    assert data.kpis[0].status == "on_track"
    on_track = [k for k in data.kpis if k.status == "on_track"]
    at_risk = [k for k in data.kpis if k.status == "at_risk"]
    assert len(on_track) == 3
    assert len(at_risk) == 5


def test_avanzia_all_nine_files_exist():
    expected = [
        "Business_Profile.md",
        "Strategy.md",
        "Goals.md",
        "KPIs.md",
        "Bottlenecks.md",
        "Opportunities.md",
        "Decisions.md",
        "Experiments.md",
        "Lessons_Learned.md",
    ]
    for filename in expected:
        assert (_AVANZIA_DIR / filename).is_file(), f"Missing: {filename}"
