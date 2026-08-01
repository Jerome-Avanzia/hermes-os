"""Tests for BusinessDataLoader (Sprint 22)."""

from pathlib import Path
from textwrap import dedent

import pytest

from hermes.kernel.business_data_loader import (
    BusinessData,
    BusinessDataLoader,
    _extract_section,
    _normalize_status,
    _parse_table,
)

ROOT = Path(__file__).resolve().parents[1]
AKOSMICANIMALS = ROOT / "businesses" / "AKosmicAnimals"


# -- Unit: _normalize_status --------------------------------------------------


class TestNormalizeStatus:
    def test_lowercase(self):
        assert _normalize_status("Open") == "open"

    def test_spaces_to_underscores(self):
        assert _normalize_status("In Progress") == "in_progress"

    def test_combined(self):
        assert _normalize_status("  At Risk  ") == "at_risk"

    def test_already_normalized(self):
        assert _normalize_status("planned") == "planned"


# -- Unit: _extract_section ---------------------------------------------------


class TestExtractSection:
    PROFILE = dedent("""\
        # Business Profile

        ## Mission

        Help cat lovers express themselves.

        ## North Star Goal

        Reach 1000 USD.
    """)

    def test_extracts_section(self):
        result = _extract_section(self.PROFILE, "Mission")
        assert result == "Help cat lovers express themselves."

    def test_missing_section(self):
        assert _extract_section(self.PROFILE, "Vision") is None

    def test_stops_at_next_heading(self):
        result = _extract_section(self.PROFILE, "Mission")
        assert "North Star" not in result

    def test_multiline_section(self):
        text = dedent("""\
            ## Summary

            Line one.
            Line two.

            ## Next
        """)
        assert _extract_section(text, "Summary") == "Line one. Line two."


# -- Unit: _parse_table -------------------------------------------------------


class TestParseTable:
    FULL_BOUNDARY = dedent("""\
        ## Items

          ---------------------------------------------------
          ID        Name         Status
          --------- ------------ ----------
          I-001     Widget       Active

          I-002     Gadget       Planned
          ---------------------------------------------------
    """)

    SIMPLE = dedent("""\
        ## Items

          ID        Name         Status
          --------- ------------ ----------
          I-001     Widget       Active

          I-002     Gadget       Planned
    """)

    def test_full_boundary_format(self):
        rows = _parse_table(self.FULL_BOUNDARY, "Items")
        assert len(rows) == 2
        assert rows[0]["ID"] == "I-001"
        assert rows[0]["Name"] == "Widget"
        assert rows[0]["Status"] == "Active"
        assert rows[1]["ID"] == "I-002"

    def test_simple_format(self):
        rows = _parse_table(self.SIMPLE, "Items")
        assert len(rows) == 2
        assert rows[0]["ID"] == "I-001"
        assert rows[1]["Name"] == "Gadget"

    def test_missing_section(self):
        assert _parse_table(self.FULL_BOUNDARY, "Missing") == []

    def test_multiline_cells(self):
        text = dedent("""\
            ## Items

              ---------------------------------------------------
              ID        Description
              --------- -----------------------------------------
              I-001     This is a long
                        description that
                        wraps.
              ---------------------------------------------------
        """)
        rows = _parse_table(text, "Items")
        assert len(rows) == 1
        assert "long" in rows[0]["Description"]
        assert "wraps" in rows[0]["Description"]

    def test_blank_line_separates_rows(self):
        text = dedent("""\
            ## Items

              ---------------------------------------------------
              ID        Name
              --------- -----------------------------------------
              I-001     Alpha

              I-002     Beta
              ---------------------------------------------------
        """)
        rows = _parse_table(text, "Items")
        assert len(rows) == 2


# -- Unit: individual loaders via test-local markdown -------------------------


class TestLoadBusiness:
    def test_loads_from_profile(self, tmp_path):
        md = tmp_path / "Business_Profile.md"
        md.write_text(dedent("""\
            # Profile

            ## Mission

            Test mission statement.

            ## Other

            Stuff.
        """))
        loader = BusinessDataLoader()
        warnings = []
        biz = loader._load_business(md, "biz_test", "TestBiz", warnings)
        assert biz is not None
        assert biz.business_id == "biz_test"
        assert biz.name == "TestBiz"
        assert biz.mission == "Test mission statement."
        assert warnings == []

    def test_missing_file(self, tmp_path):
        loader = BusinessDataLoader()
        warnings = []
        biz = loader._load_business(
            tmp_path / "nope.md", "biz_x", "X", warnings
        )
        assert biz is None
        assert any("Missing" in w for w in warnings)

    def test_missing_mission_section(self, tmp_path):
        md = tmp_path / "Business_Profile.md"
        md.write_text("# Profile\n\n## Other\n\nStuff.\n")
        loader = BusinessDataLoader()
        warnings = []
        biz = loader._load_business(md, "biz_x", "X", warnings)
        assert biz is None
        assert any("Mission" in w for w in warnings)


class TestLoadStrategy:
    def test_loads_strategy(self, tmp_path):
        md = tmp_path / "Strategy.md"
        md.write_text(dedent("""\
            # Strategy

            ## Strategic Objective

            Build a great brand that scales.

            ## Review Cadence

            - Daily: Executive Brief
            - Monthly: Strategy review
        """))
        loader = BusinessDataLoader()
        warnings = []
        strat = loader._load_strategy(md, "biz_test", "TestBiz", warnings)
        assert strat is not None
        assert strat.strategy_id == "strat_testbiz_001"
        assert strat.business_id == "biz_test"
        assert strat.title == "Build a great brand that scales"
        assert strat.objective == "Build a great brand that scales."
        assert strat.review_frequency == "monthly"
        assert warnings == []

    def test_missing_file(self, tmp_path):
        loader = BusinessDataLoader()
        warnings = []
        strat = loader._load_strategy(
            tmp_path / "nope.md", "biz_x", "X", warnings
        )
        assert strat is None
        assert any("Missing" in w for w in warnings)

    def test_no_review_cadence(self, tmp_path):
        md = tmp_path / "Strategy.md"
        md.write_text(dedent("""\
            # Strategy

            ## Strategic Objective

            Just do it.
        """))
        loader = BusinessDataLoader()
        warnings = []
        strat = loader._load_strategy(md, "biz_x", "X", warnings)
        assert strat is not None
        assert strat.review_frequency is None


class TestLoadBottlenecks:
    TABLE_MD = dedent("""\
        ## Active Bottlenecks

          ---------------------------------------------------
          ID        Area         Description   Impact   Owner    Status
          --------- ------------ ------------- -------- -------- ----------
          BOT-001   Product      Too slow      High     Alice    Open

          BOT-002   Marketing    Low traffic   Medium   Bob      Mitigating
          ---------------------------------------------------
    """)

    def test_loads_two(self, tmp_path):
        md = tmp_path / "Bottlenecks.md"
        md.write_text(self.TABLE_MD)
        loader = BusinessDataLoader()
        warnings = []
        bots = loader._load_bottlenecks(md, "biz_t", warnings)
        assert len(bots) == 2
        assert bots[0].bottleneck_id == "BOT-001"
        assert bots[0].title == "Too slow"
        assert bots[0].category == "product"
        assert bots[0].impact == "high"
        assert bots[0].status == "open"
        assert bots[0].owner == "Alice"
        assert bots[1].bottleneck_id == "BOT-002"
        assert bots[1].category == "marketing"
        assert bots[1].status == "mitigating"

    def test_unknown_category_warning(self, tmp_path):
        md = tmp_path / "Bottlenecks.md"
        md.write_text(dedent("""\
            ## Active Bottlenecks

              ---------------------------------------------------
              ID        Area         Description   Impact   Owner    Status
              --------- ------------ ------------- -------- -------- ----------
              BOT-001   Legal        Compliance    High     Alice    Open
              ---------------------------------------------------
        """))
        loader = BusinessDataLoader()
        warnings = []
        loader._load_bottlenecks(md, "biz_t", warnings)
        assert any("unknown category 'legal'" in w for w in warnings)

    def test_duplicate_id_skipped(self, tmp_path):
        md = tmp_path / "Bottlenecks.md"
        md.write_text(dedent("""\
            ## Active Bottlenecks

              ---------------------------------------------------
              ID        Area         Description   Impact   Owner    Status
              --------- ------------ ------------- -------- -------- ----------
              BOT-001   Product      First         High     Alice    Open

              BOT-001   Marketing    Second        Medium   Bob      Open
              ---------------------------------------------------
        """))
        loader = BusinessDataLoader()
        warnings = []
        bots = loader._load_bottlenecks(md, "biz_t", warnings)
        assert len(bots) == 1
        assert any("Duplicate" in w for w in warnings)


class TestLoadDecisions:
    TABLE_MD = dedent("""\
        ## Decision Log

          ---------------------------------------------------
          ID        Date         Status        Decision        Expected Impact
          --------- ------------ ------------- --------------- ----------------
          DEC-001   2026-07-19   Implemented   Focus on cats   Better SEO
          ---------------------------------------------------
    """)

    def test_loads_decision(self, tmp_path):
        md = tmp_path / "Decisions.md"
        md.write_text(self.TABLE_MD)
        loader = BusinessDataLoader()
        warnings = []
        decs = loader._load_decisions(md, "biz_t", warnings)
        assert len(decs) == 1
        assert decs[0].decision_id == "DEC-001"
        assert decs[0].title == "Focus on cats"
        assert decs[0].context == "Focus on cats"
        assert decs[0].rationale == "Better SEO"
        assert decs[0].status == "implemented"
        assert decs[0].decision_date == "2026-07-19"

    def test_unknown_status_warning(self, tmp_path):
        md = tmp_path / "Decisions.md"
        md.write_text(dedent("""\
            ## Decision Log

              ---------------------------------------------------
              ID        Date         Status        Decision        Expected Impact
              --------- ------------ ------------- --------------- ----------------
              DEC-001   2026-07-19   In Progress   Do thing        Some impact
              ---------------------------------------------------
        """))
        loader = BusinessDataLoader()
        warnings = []
        decs = loader._load_decisions(md, "biz_t", warnings)
        assert len(decs) == 1
        assert decs[0].status == "in_progress"
        assert any("unknown status 'in_progress'" in w for w in warnings)


class TestLoadExperiments:
    TABLE_MD = dedent("""\
        ## Experiment Log

          ---------------------------------------------------
          ID        Date         Hypothesis         Metric    Status    Result
          --------- ------------ ------------------ --------- --------- --------
          EXP-001   2026-07-19   Mockups increase   CTR       Planned   TBD
                                 CTR compared to
                                 flat lays.
          ---------------------------------------------------
    """)

    def test_loads_experiment(self, tmp_path):
        md = tmp_path / "Experiments.md"
        md.write_text(self.TABLE_MD)
        loader = BusinessDataLoader()
        warnings = []
        exps = loader._load_experiments(md, "biz_t", warnings)
        assert len(exps) == 1
        assert exps[0].experiment_id == "EXP-001"
        assert exps[0].title == "Mockups increase CTR compared to flat lays"
        assert "flat lays." in exps[0].hypothesis
        assert exps[0].success_criteria == "CTR"
        assert exps[0].status == "planned"
        assert exps[0].outcome is None
        assert exps[0].start_date == "2026-07-19"

    def test_result_not_tbd(self, tmp_path):
        md = tmp_path / "Experiments.md"
        md.write_text(dedent("""\
            ## Experiment Log

              ---------------------------------------------------
              ID        Date         Hypothesis    Metric    Status      Result
              --------- ------------ ------------- --------- ----------- --------
              EXP-001   2026-07-19   Test hyp      CTR       Completed   +20%
              ---------------------------------------------------
        """))
        loader = BusinessDataLoader()
        warnings = []
        exps = loader._load_experiments(md, "biz_t", warnings)
        assert exps[0].outcome == "+20%"


class TestLoadLessons:
    TABLE_MD = dedent("""\
        ## Lesson Log

          ---------------------------------------------------
          ID        Date         Category      Lesson           Action
          --------- ------------ ------------- ---------------- ----------
          LES-001   2026-07-19   Workflow      Doc first        Continue
          ---------------------------------------------------
    """)

    def test_loads_lesson(self, tmp_path):
        md = tmp_path / "Lessons_Learned.md"
        md.write_text(self.TABLE_MD)
        loader = BusinessDataLoader()
        warnings = []
        lessons = loader._load_lessons(md, "biz_t", warnings)
        assert len(lessons) == 1
        assert lessons[0].lesson_id == "LES-001"
        assert lessons[0].title == "Doc first"
        assert lessons[0].summary == "Doc first"
        assert lessons[0].source == "workflow"
        assert lessons[0].recommendation == "Continue"
        assert lessons[0].date == "2026-07-19"

    def test_unknown_source_warning(self, tmp_path):
        md = tmp_path / "Lessons_Learned.md"
        md.write_text(self.TABLE_MD)
        loader = BusinessDataLoader()
        warnings = []
        loader._load_lessons(md, "biz_t", warnings)
        assert any("unknown source 'workflow'" in w for w in warnings)


class TestLoadOpportunities:
    def test_missing_owner_skips(self, tmp_path):
        md = tmp_path / "Opportunities.md"
        md.write_text(dedent("""\
            ## Opportunity Pipeline

              ---------------------------------------------------
              ID        Opportunity     Expected Impact  Effort    Status
              --------- --------------- ---------------- --------- --------
              OPP-001   Test opp        High             Medium    Planned
              ---------------------------------------------------
        """))
        loader = BusinessDataLoader()
        warnings = []
        opps = loader._load_opportunities(md, "biz_t", warnings)
        assert len(opps) == 0
        assert any("Owner" in w for w in warnings)

    def test_loads_with_owner(self, tmp_path):
        md = tmp_path / "Opportunities.md"
        md.write_text(dedent("""\
            ## Opportunity Pipeline

              ---------------------------------------------------
              ID        Opportunity     Expected Impact  Effort    Owner    Status
              --------- --------------- ---------------- --------- -------- --------
              OPP-001   Test opp        High             Medium    Alice    Planned
              ---------------------------------------------------
        """))
        loader = BusinessDataLoader()
        warnings = []
        opps = loader._load_opportunities(md, "biz_t", warnings)
        assert len(opps) == 1
        assert opps[0].title == "Test opp"
        assert opps[0].description == "Test opp"
        assert opps[0].expected_impact == "high"
        assert opps[0].estimated_effort == "medium"
        assert opps[0].owner == "Alice"
        assert opps[0].status == "planned"


class TestLoadKPIs:
    def test_incomplete_data_skips(self, tmp_path):
        md = tmp_path / "KPIs.md"
        md.write_text(dedent("""\
            ## Growth KPIs

              KPI                  Current   Target
              -------------------- --------- ----------
              Active Listings      TBD       100
        """))
        loader = BusinessDataLoader()
        warnings = []
        kpis = loader._load_kpis(md, "biz_t", "TestBiz", warnings)
        assert len(kpis) == 0
        assert any("missing or unparseable" in w for w in warnings)

    def test_loads_complete_kpi(self, tmp_path):
        md = tmp_path / "KPIs.md"
        md.write_text(dedent("""\
            ## Growth KPIs

              ID     KPI          Goal   Unit    Current  Target  Frequency  Status  Owner
              ------ ------------ ------ ------- -------- ------- ---------- ------- ------
              K-001  Revenue      G-001  USD     500.0    1000.0  monthly    on_track Alice
        """))
        loader = BusinessDataLoader()
        warnings = []
        kpis = loader._load_kpis(md, "biz_t", "TestBiz", warnings)
        assert len(kpis) == 1
        assert kpis[0].name == "Revenue"
        assert kpis[0].current_value == 500.0
        assert kpis[0].target_value == 1000.0


class TestLoadGoals:
    def test_missing_file(self, tmp_path):
        loader = BusinessDataLoader()
        warnings = []
        goals = loader._load_goals(tmp_path / "Goals.md", "biz_t", warnings)
        assert goals == []
        assert any("Missing" in w for w in warnings)

    def test_loads_goal(self, tmp_path):
        md = tmp_path / "Goals.md"
        md.write_text(dedent("""\
            ## Goals

              ID      Goal       Description    Target   Target Date  Owner    Status
              ------- ---------- -------------- -------- ------------ -------- --------
              G-001   Revenue    Hit target     1000     2026-12-31   Alice    active
        """))
        loader = BusinessDataLoader()
        warnings = []
        goals = loader._load_goals(md, "biz_t", warnings)
        assert len(goals) == 1
        assert goals[0].goal_id == "G-001"
        assert goals[0].title == "Revenue"
        assert goals[0].status == "active"


# -- Integration: full load() ------------------------------------------------


class TestFullLoad:
    def test_nonexistent_directory(self):
        loader = BusinessDataLoader()
        with pytest.raises(FileNotFoundError):
            loader.load(Path("/nonexistent/dir"))

    def test_empty_directory(self, tmp_path):
        loader = BusinessDataLoader()
        data = loader.load(tmp_path)
        assert data.business is None
        assert data.strategy is None
        assert data.goals == []
        assert data.kpis == []
        assert data.bottlenecks == []
        assert data.opportunities == []
        assert data.decisions == []
        assert data.experiments == []
        assert data.lessons == []
        assert len(data.warnings) > 0

    def test_returns_business_data_type(self, tmp_path):
        loader = BusinessDataLoader()
        data = loader.load(tmp_path)
        assert isinstance(data, BusinessData)


class TestAKosmicAnimalsIntegration:
    """Integration tests against the real AKosmicAnimals directory."""

    @pytest.fixture()
    def data(self):
        loader = BusinessDataLoader()
        return loader.load(AKOSMICANIMALS)

    def test_business_loaded(self, data):
        assert data.business is not None
        assert data.business.business_id == "biz_akosmicanimals"
        assert data.business.name == "AKosmicAnimals"
        assert "cat lovers" in data.business.mission

    def test_strategy_loaded(self, data):
        assert data.strategy is not None
        assert data.strategy.strategy_id == "strat_akosmicanimals_001"
        assert data.strategy.business_id == "biz_akosmicanimals"
        assert "repeatable" in data.strategy.objective
        assert data.strategy.review_frequency == "monthly"

    def test_bottlenecks_loaded(self, data):
        assert len(data.bottlenecks) == 4
        ids = [b.bottleneck_id for b in data.bottlenecks]
        assert ids == ["BOT-001", "BOT-002", "BOT-003", "BOT-004"]
        assert all(b.business_id == "biz_akosmicanimals" for b in data.bottlenecks)
        assert all(b.status == "open" for b in data.bottlenecks)

    def test_bottleneck_categories_warned(self, data):
        cat_warnings = [w for w in data.warnings if "unknown category" in w]
        assert len(cat_warnings) == 4

    def test_decisions_loaded(self, data):
        assert len(data.decisions) == 3
        ids = [d.decision_id for d in data.decisions]
        assert ids == ["DEC-001", "DEC-002", "DEC-003"]
        assert data.decisions[0].status == "implemented"
        assert data.decisions[2].status == "in_progress"

    def test_decision_in_progress_warned(self, data):
        assert any(
            "DEC-003" in w and "unknown status" in w for w in data.warnings
        )

    def test_experiments_loaded(self, data):
        assert len(data.experiments) == 3
        ids = [e.experiment_id for e in data.experiments]
        assert ids == ["EXP-001", "EXP-002", "EXP-003"]
        assert all(e.status == "planned" for e in data.experiments)
        assert all(e.outcome is None for e in data.experiments)
        assert all(e.start_date == "2026-07-19" for e in data.experiments)

    def test_experiment_title_extraction(self, data):
        assert data.experiments[0].title.startswith("Lifestyle mockups")
        assert "." not in data.experiments[0].title

    def test_lessons_loaded(self, data):
        assert len(data.lessons) == 3
        ids = [l.lesson_id for l in data.lessons]
        assert ids == ["LES-001", "LES-002", "LES-003"]
        assert data.lessons[0].source == "workflow"
        assert data.lessons[1].source == "product"
        assert data.lessons[2].source == "process"

    def test_lesson_unknown_source_warned(self, data):
        source_warnings = [w for w in data.warnings if "unknown source" in w]
        assert len(source_warnings) == 3

    def test_opportunities_all_skipped(self, data):
        assert data.opportunities == []
        opp_warnings = [w for w in data.warnings if "Opportunity" in w and "Owner" in w]
        assert len(opp_warnings) == 5

    def test_kpis_all_skipped(self, data):
        assert data.kpis == []
        kpi_warnings = [w for w in data.warnings if "KPI" in w]
        assert len(kpi_warnings) > 0

    def test_goals_missing(self, data):
        assert data.goals == []
        assert any("Goals.md" in w for w in data.warnings)

    def test_no_fabricated_timestamps(self, data):
        assert data.business.created_at is None
        assert data.business.status is None
        assert data.strategy.status is None

    def test_stateless_loader(self):
        loader = BusinessDataLoader()
        d1 = loader.load(AKOSMICANIMALS)
        d2 = loader.load(AKOSMICANIMALS)
        assert len(d1.bottlenecks) == len(d2.bottlenecks)
        assert len(d1.warnings) == len(d2.warnings)
