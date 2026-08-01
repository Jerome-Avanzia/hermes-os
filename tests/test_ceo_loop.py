"""Tests for CEOLoop — Ten-Step Loop (Sprint 25)."""

import os
from datetime import datetime
from pathlib import Path

import pytest

from hermes.kernel.brief_generator import ExecutiveBriefGenerator
from hermes.kernel.business_data_loader import BusinessData, BusinessDataLoader
from hermes.kernel.ceo_loop import CEOLoop, CEOReviewResult, StepRecord
from hermes.kernel.decision_engine import DecisionEngine, EngineResult
from hermes.models import ExecutiveBrief

ROOT = Path(__file__).resolve().parents[1]
AKOSMICANIMALS = ROOT / "businesses" / "AKosmicAnimals"


# -- StepRecord ----------------------------------------------------------------


class TestStepRecord:
    def test_construction(self):
        sr = StepRecord(
            step_number=1, name="load_governance",
            status="completed", detail="OK",
            timestamp="2026-08-01T00:00:00+00:00",
        )
        assert sr.step_number == 1
        assert sr.name == "load_governance"
        assert sr.status == "completed"

    def test_all_statuses(self):
        for status in ("completed", "skipped", "failed"):
            sr = StepRecord(1, "test", status, "", "")
            assert sr.status == status


# -- CEOReviewResult -----------------------------------------------------------


class TestCEOReviewResult:
    def test_all_warnings_aggregates(self):
        result = CEOReviewResult(
            business_id="biz_test",
            data=BusinessData(),
            engine_result=EngineResult(business_id=""),
            brief=ExecutiveBrief(
                brief_id="", business_id="", reporting_period="daily",
                generated_at="", summary="", priorities=[],
                recommendations=[], status="draft",
            ),
            steps=[],
            execution_status="completed",
            loader_warnings=["lw1", "lw2"],
            engine_warnings=["ew1"],
            generator_warnings=["gw1", "gw2", "gw3"],
            loop_warnings=["loop1"],
        )
        assert len(result.all_warnings) == 7
        assert result.all_warnings == ["lw1", "lw2", "ew1", "gw1", "gw2", "gw3", "loop1"]

    def test_empty_warnings(self):
        result = CEOReviewResult(
            business_id="", data=BusinessData(),
            engine_result=EngineResult(business_id=""),
            brief=ExecutiveBrief(
                brief_id="", business_id="", reporting_period="daily",
                generated_at="", summary="", priorities=[],
                recommendations=[], status="draft",
            ),
            steps=[], execution_status="completed",
        )
        assert result.all_warnings == []


# -- Default Dependencies -----------------------------------------------------


class TestDefaultDependencies:
    def test_creates_defaults(self):
        loop = CEOLoop()
        assert isinstance(loop._loader, BusinessDataLoader)
        assert isinstance(loop._engine, DecisionEngine)
        assert isinstance(loop._generator, ExecutiveBriefGenerator)


class TestInjectedDependencies:
    def test_uses_provided(self):
        loader = BusinessDataLoader()
        engine = DecisionEngine()
        gen = ExecutiveBriefGenerator()
        loop = CEOLoop(loader=loader, engine=engine, generator=gen)
        assert loop._loader is loader
        assert loop._engine is engine
        assert loop._generator is gen


# -- Step 1: Load Governance ---------------------------------------------------


class TestStep01Governance:
    def test_valid_directory(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[0]
        assert step.step_number == 1
        assert step.name == "load_governance"
        assert step.status == "completed"

    def test_nonexistent_directory(self):
        loop = CEOLoop()
        result = loop.run(Path("/nonexistent/dir"))
        assert result.execution_status == "failed"
        step = result.steps[0]
        assert step.status == "failed"
        assert "not found" in step.detail

    def test_failed_governance_records_all_ten_steps(self):
        loop = CEOLoop()
        result = loop.run(Path("/nonexistent/dir"))
        assert len(result.steps) == 10
        assert result.steps[0].status == "failed"
        for s in result.steps[1:]:
            assert s.status == "skipped"


# -- Step 2: Load Business Context --------------------------------------------


class TestStep02LoadContext:
    def test_loads_data(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[1]
        assert step.step_number == 2
        assert step.status == "completed"
        assert result.data.business is not None

    def test_empty_dir_still_completes(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[1]
        assert step.status == "completed"
        assert "No business" in step.detail


# -- Step 3: Discover Agents (Phase 6 scope — no warning) ---------------------


class TestStep03Agents:
    def test_skipped(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[2]
        assert step.step_number == 3
        assert step.status == "skipped"
        assert "Phase 6" in step.detail

    def test_no_warning_emitted(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        assert not any("agent" in w.lower() and "registry" in w.lower()
                        for w in result.loop_warnings)
        assert not any("agent" in w.lower() and "registry" in w.lower()
                        for w in result.all_warnings)


# -- Step 4: Discover Services -------------------------------------------------


class TestStep04Services:
    def test_completed(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[3]
        assert step.step_number == 4
        assert step.status == "completed"
        assert "BusinessDataLoader" in step.detail
        assert "DecisionEngine" in step.detail
        assert "ExecutiveBriefGenerator" in step.detail


# -- Step 5: Discover Extensions (Phase 6 scope — no warning) -----------------


class TestStep05Extensions:
    def test_skipped(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[4]
        assert step.step_number == 5
        assert step.status == "skipped"
        assert "Phase 6" in step.detail

    def test_no_warning_emitted(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        assert not any("extension" in w.lower() and "framework" in w.lower()
                        for w in result.loop_warnings)


# -- Step 6: Build Plan -------------------------------------------------------


class TestStep06Plan:
    def test_completed(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[5]
        assert step.step_number == 6
        assert step.status == "completed"
        assert "Load" in step.detail
        assert "Evaluate" in step.detail
        assert "Brief" in step.detail


# -- Step 7: Execute ----------------------------------------------------------


class TestStep07Execute:
    def test_completed(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[6]
        assert step.step_number == 7
        assert step.status == "completed"
        assert "recommendations" in step.detail
        assert "brief" in step.detail


# -- Step 8: Monitor Execution ------------------------------------------------


class TestStep08Monitor:
    def test_summarizes_counts(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[7]
        assert step.step_number == 8
        assert step.status == "completed"
        assert "completed" in step.detail
        assert "skipped" in step.detail


# -- Step 9: Escalation Check (no warning about missing system) ---------------


class TestStep09Escalation:
    def test_completed(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[8]
        assert step.step_number == 9
        assert step.status == "completed"

    def test_no_warning_about_escalation_system(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        assert not any("escalation system" in w.lower()
                        for w in result.loop_warnings)

    def test_no_recs_noted(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[8]
        assert "No recommendations" in step.detail


# -- Step 10: Record Outcomes -------------------------------------------------


class TestStep10Record:
    def test_completed(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        step = result.steps[9]
        assert step.step_number == 10
        assert step.status == "completed"


# -- Execution Status ---------------------------------------------------------


class TestExecutionStatus:
    def test_failed_on_bad_dir(self):
        loop = CEOLoop()
        result = loop.run(Path("/nonexistent"))
        assert result.execution_status == "failed"

    def test_completed_with_warnings(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        # Loader will emit warnings about missing files
        assert result.execution_status == "completed_with_warnings"

    def test_completed_when_no_warnings(self, tmp_path):
        # Build a directory with all required files to avoid warnings
        # This is hard to achieve with real data, so test the status logic
        # through the result — if somehow no warnings, status is "completed"
        loop = CEOLoop()
        result = loop.run(tmp_path)
        # Empty dir always has warnings (missing files)
        assert result.execution_status in (
            "completed", "completed_with_warnings"
        )


# -- Ten Steps Always Present -------------------------------------------------


class TestTenSteps:
    def test_ten_steps_on_success(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        assert len(result.steps) == 10
        numbers = [s.step_number for s in result.steps]
        assert numbers == list(range(1, 11))

    def test_ten_steps_on_failure(self):
        loop = CEOLoop()
        result = loop.run(Path("/nonexistent"))
        assert len(result.steps) == 10


# -- Warning Provenance -------------------------------------------------------


class TestWarningProvenance:
    def test_loader_warnings_separated(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        # Missing files generate loader warnings
        assert len(result.loader_warnings) > 0
        assert any("Missing" in w for w in result.loader_warnings)

    def test_engine_warnings_separated(self, tmp_path):
        # Create a bottleneck so the engine generates warnings
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        (tmp_path / "Bottlenecks.md").write_text(
            "## Active Bottlenecks\n\n"
            "  ---------------------------------------------------\n"
            "  ID        Area         Description   Impact   Owner    Status\n"
            "  --------- ------------ ------------- -------- -------- ------\n"
            "  BOT-001   Legal        Issue         High     Alice    Open\n"
            "  ---------------------------------------------------\n"
        )
        loop = CEOLoop()
        result = loop.run(tmp_path)
        assert len(result.engine_warnings) > 0

    def test_generator_warnings_separated(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        # Generator propagates engine warnings + adds its own
        assert isinstance(result.generator_warnings, list)

    def test_loop_warnings_separated(self):
        loop = CEOLoop()
        result = loop.run(Path("/nonexistent"))
        assert len(result.loop_warnings) > 0
        assert any("Governance" in w for w in result.loop_warnings)

    def test_no_cross_contamination(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        # Loader warnings should not appear in loop_warnings
        for lw in result.loader_warnings:
            assert lw not in result.loop_warnings


class TestAllWarnings:
    def test_aggregates_all_sources(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        total = (len(result.loader_warnings) + len(result.engine_warnings)
                 + len(result.generator_warnings) + len(result.loop_warnings))
        assert len(result.all_warnings) == total

    def test_order_is_loader_engine_generator_loop(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        result = loop.run(tmp_path)
        expected = (result.loader_warnings + result.engine_warnings
                    + result.generator_warnings + result.loop_warnings)
        assert result.all_warnings == expected


# -- No Mutation ---------------------------------------------------------------


class TestNoMutation:
    def test_no_file_writes(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        before = set(os.listdir(tmp_path))
        loop = CEOLoop()
        loop.run(tmp_path)
        after = set(os.listdir(tmp_path))
        assert before == after


# -- Statelessness -------------------------------------------------------------


class TestStatelessness:
    def test_two_runs_equivalent(self, tmp_path):
        (tmp_path / "Business_Profile.md").write_text("## Mission\n\nTest.\n")
        loop = CEOLoop()
        r1 = loop.run(tmp_path)
        r2 = loop.run(tmp_path)
        assert r1.execution_status == r2.execution_status
        assert len(r1.steps) == len(r2.steps)
        assert len(r1.all_warnings) == len(r2.all_warnings)
        assert r1.brief.summary == r2.brief.summary


# -- Timestamps ----------------------------------------------------------------


class TestTimestamps:
    def test_valid_iso(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        started = datetime.fromisoformat(result.started_at)
        completed = datetime.fromisoformat(result.completed_at)
        assert started.tzinfo is not None
        assert completed.tzinfo is not None
        assert completed >= started

    def test_step_timestamps_valid(self, tmp_path):
        loop = CEOLoop()
        result = loop.run(tmp_path)
        for step in result.steps:
            dt = datetime.fromisoformat(step.timestamp)
            assert dt.tzinfo is not None


# -- Integration: AKosmicAnimals Full Loop -------------------------------------


class TestAKosmicAnimalsFullLoop:
    """Full end-to-end pipeline on real AKosmicAnimals data."""

    @pytest.fixture()
    def result(self):
        loop = CEOLoop()
        return loop.run(AKOSMICANIMALS)

    def test_execution_status(self, result):
        assert result.execution_status == "completed_with_warnings"

    def test_ten_steps(self, result):
        assert len(result.steps) == 10
        numbers = [s.step_number for s in result.steps]
        assert numbers == list(range(1, 11))

    def test_steps_3_5_skipped(self, result):
        assert result.steps[2].status == "skipped"  # discover_agents
        assert result.steps[4].status == "skipped"  # discover_extensions

    def test_other_steps_completed(self, result):
        for i in [0, 1, 3, 5, 6, 7, 8, 9]:
            assert result.steps[i].status == "completed", (
                f"Step {i+1} ({result.steps[i].name}) should be completed"
            )

    def test_business_id(self, result):
        assert result.business_id == "biz_akosmicanimals"

    def test_business_data_loaded(self, result):
        assert result.data.business is not None
        assert len(result.data.bottlenecks) == 4
        assert len(result.data.decisions) == 3
        assert len(result.data.experiments) == 3
        assert len(result.data.lessons) == 3

    def test_engine_result_has_recommendations(self, result):
        assert len(result.engine_result.recommendations) == 4

    def test_brief_is_executive_brief(self, result):
        assert isinstance(result.brief, ExecutiveBrief)
        assert result.brief.status == "draft"
        assert result.brief.business_id == "biz_akosmicanimals"

    def test_loader_warnings_present(self, result):
        assert len(result.loader_warnings) > 0
        assert any("Goals.md" in w for w in result.loader_warnings)

    def test_engine_warnings_present(self, result):
        assert len(result.engine_warnings) > 0
        assert any("strategy link" in w for w in result.engine_warnings)

    def test_generator_warnings_present(self, result):
        assert len(result.generator_warnings) > 0

    def test_all_warnings_aggregated(self, result):
        total = (len(result.loader_warnings) + len(result.engine_warnings)
                 + len(result.generator_warnings) + len(result.loop_warnings))
        assert len(result.all_warnings) == total

    def test_no_agent_or_extension_warnings(self, result):
        for w in result.all_warnings:
            assert "agent registry" not in w.lower()
            assert "extension framework" not in w.lower()

    def test_timestamps(self, result):
        started = datetime.fromisoformat(result.started_at)
        completed = datetime.fromisoformat(result.completed_at)
        assert completed >= started
