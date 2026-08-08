"""Tests for Phase 6 — EngineeringCoordinator.

Coverage:
  - Autonomous mode: planner.plan() called, result passed to workflow
  - Autonomous mode: planner failure → WorkflowExecutionReport(success=False)
  - Autonomous mode: ambiguous plan → WorkflowExecutionReport(success=False, error starts "ambiguous_plan:")
  - Deterministic mode: planner NOT called; single-op plan constructed
  - Deterministic mode: intent="create" when file doesn't exist
  - Deterministic mode: intent="modify" when file exists
  - Both modes: workflow.execute(plan, goal) called with correct args
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from hermes.kernel.engineering_coordinator import EngineeringCoordinator
from hermes.kernel.engineering_planner import EngineeringPlanner
from hermes.models.engineering_plan import (
    EngineeringPlan,
    EngineeringPlanResult,
    PlannedOperation,
    PlanningMetadata,
)
from hermes.models.engineering_workflow import FounderGoal, WorkflowExecutionReport
from hermes.models.repository_intelligence import RepositoryFile, RepositorySnapshot


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_snapshot(
    *,
    snapshot_id: str = "snap-abc123",
    file_count: int = 3,
    files: tuple | None = None,
) -> RepositorySnapshot:
    if files is None:
        files = (
            RepositoryFile(path="src/hello.py", extension=".py", size_bytes=100, is_directory=False),
            RepositoryFile(path="src/utils.py", extension=".py", size_bytes=200, is_directory=False),
            RepositoryFile(path="tests/test_hello.py", extension=".py", size_bytes=150, is_directory=False),
        )
    return RepositorySnapshot(
        snapshot_id=snapshot_id,
        repository_path="my-project",
        scanned_at="2026-08-08T00:00:00+00:00",
        primary_language="python",
        languages=(),
        build_system=None,
        entry_points=(),
        test_locations=(),
        config_files=(),
        documentation=(),
        files=files,
        file_count=file_count,
        directory_count=1,
        total_size_bytes=450,
        git_present=True,
        metadata=(),
    )


def _make_planning_metadata(
    *,
    context: str = "repository_snapshot_v1",
    entries: str = "3",
    snapshot_id: str = "snap-abc123",
) -> PlanningMetadata:
    return PlanningMetadata(
        planning_context=context,
        planning_snapshot_entries=entries,
        planning_snapshot_id=snapshot_id,
    )


def _make_goal(output_path: str = "", workspace_path: str = "/workspace") -> FounderGoal:
    return FounderGoal(
        goal_id="test-goal",
        description="Add hello function",
        workspace_path=workspace_path,
        repository_path="my-project",
        output_path=output_path,
    )


def _make_success_report() -> WorkflowExecutionReport:
    return WorkflowExecutionReport(
        report_id="report-test-goal",
        goal_id="test-goal",
        mission_id="mission-test-goal",
        job_id="job-test-goal",
        steps=(),
        success=True,
        error=None,
        execution_sequence=(),
        metadata=(("goal_id", "test-goal"),),
    )


def _make_failure_report(error: str = "test_error") -> WorkflowExecutionReport:
    return WorkflowExecutionReport(
        report_id="report-test-goal",
        goal_id="test-goal",
        mission_id="mission-test-goal",
        job_id="job-test-goal",
        steps=(),
        success=False,
        error=error,
        execution_sequence=(),
        metadata=(("goal_id", "test-goal"),),
    )


def _make_plan(confidence: str = "high") -> EngineeringPlan:
    ops = (PlannedOperation(
        operation_id="op-0",
        target="src/hello.py",
        intent="create",
        goal="Create hello module",
    ),) if confidence == "high" else ()
    candidates = ("src/a.py", "src/b.py") if confidence == "ambiguous" else ()
    return EngineeringPlan(
        plan_id="plan-test-goal",
        goal_id="test-goal",
        confidence=confidence,
        basis="test basis",
        operations=ops,
        candidates=candidates,
    )


def _make_coordinator(
    planner: MagicMock | None = None,
    workflow: MagicMock | None = None,
) -> tuple[EngineeringCoordinator, MagicMock, MagicMock]:
    if planner is None:
        planner = MagicMock(spec=EngineeringPlanner)
    if workflow is None:
        workflow = MagicMock()
    coordinator = EngineeringCoordinator(planner=planner, workflow=workflow)
    return coordinator, planner, workflow


# ── Autonomous mode ───────────────────────────────────────────────────────────


class TestAutonomousMode:
    def test_planner_called_in_autonomous_mode(self):
        goal = _make_goal(output_path="")
        snapshot = _make_snapshot()
        plan = _make_plan("high")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True, plan=plan, error=None
        )
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, snapshot)

        mock_planner.plan.assert_called_once_with(goal, snapshot)

    def test_workflow_called_with_plan_and_goal(self):
        goal = _make_goal(output_path="")
        plan = _make_plan("high")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True, plan=plan, error=None
        )
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        mock_workflow.execute.assert_called_once_with(plan, goal)

    def test_planner_failure_returns_failure_report(self):
        goal = _make_goal(output_path="")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=False,
            plan=None,
            error="json_parse_error: unexpected token",
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert report.success is False
        mock_workflow.execute.assert_not_called()

    def test_planner_failure_error_in_report(self):
        goal = _make_goal(output_path="")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=False,
            plan=None,
            error="gateway_dispatch_failed: no adapter",
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert "gateway_dispatch_failed" in (report.error or "")

    def test_planner_failure_metadata_has_planning_stage(self):
        goal = _make_goal(output_path="")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=False, plan=None, error="some_error"
        )

        report = coordinator.execute(goal, _make_snapshot())

        meta = dict(report.metadata)
        assert meta.get("failure_stage") == "planning"

    def test_ambiguous_plan_returns_failure_report(self):
        goal = _make_goal(output_path="")
        plan = _make_plan("ambiguous")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True, plan=plan, error=None
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert report.success is False

    def test_ambiguous_plan_error_starts_with_ambiguous_plan(self):
        goal = _make_goal(output_path="")
        plan = _make_plan("ambiguous")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True, plan=plan, error=None
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert report.error is not None
        assert report.error.startswith("ambiguous_plan:")

    def test_ambiguous_plan_candidates_in_error(self):
        goal = _make_goal(output_path="")
        plan = _make_plan("ambiguous")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True, plan=plan, error=None
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert "src/a.py" in (report.error or "")
        assert "src/b.py" in (report.error or "")

    def test_ambiguous_plan_workflow_not_called(self):
        goal = _make_goal(output_path="")
        plan = _make_plan("ambiguous")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True, plan=plan, error=None
        )

        coordinator.execute(goal, _make_snapshot())

        mock_workflow.execute.assert_not_called()


# ── Deterministic mode ────────────────────────────────────────────────────────


class TestDeterministicMode:
    def test_planner_not_called_in_deterministic_mode(self):
        goal = _make_goal(output_path="my-project/hello.py")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        mock_planner.plan.assert_not_called()

    def test_workflow_called_in_deterministic_mode(self):
        goal = _make_goal(output_path="my-project/hello.py")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        mock_workflow.execute.assert_called_once()

    def test_deterministic_mode_passes_goal_to_workflow(self):
        goal = _make_goal(output_path="my-project/hello.py")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        call_args = mock_workflow.execute.call_args
        # Second argument is the goal
        passed_goal = call_args[0][1]
        assert passed_goal is goal

    def test_deterministic_mode_intent_create_for_nonexistent_file(self, tmp_path):
        # File does not exist
        workspace = str(tmp_path)
        goal = FounderGoal(
            goal_id="det-create",
            description="Create new module",
            workspace_path=workspace,
            repository_path=".",
            output_path="new_file.py",
        )
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        call_args = mock_workflow.execute.call_args
        plan = call_args[0][0]
        assert plan.operations[0].intent == "create"

    def test_deterministic_mode_intent_modify_for_existing_file(self, tmp_path):
        # File exists
        existing_file = tmp_path / "existing.py"
        existing_file.write_text("# content")

        workspace = str(tmp_path)
        goal = FounderGoal(
            goal_id="det-modify",
            description="Modify existing module",
            workspace_path=workspace,
            repository_path=".",
            output_path="existing.py",
        )
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        call_args = mock_workflow.execute.call_args
        plan = call_args[0][0]
        assert plan.operations[0].intent == "modify"

    def test_deterministic_mode_single_operation_plan(self):
        goal = _make_goal(output_path="my-project/hello.py")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        call_args = mock_workflow.execute.call_args
        plan = call_args[0][0]
        assert len(plan.operations) == 1

    def test_deterministic_mode_plan_confidence_high(self):
        goal = _make_goal(output_path="my-project/hello.py")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        coordinator.execute(goal, _make_snapshot())

        call_args = mock_workflow.execute.call_args
        plan = call_args[0][0]
        assert plan.confidence == "high"


# ── Report IDs ────────────────────────────────────────────────────────────────


class TestReportIDs:
    def test_failure_report_has_correct_goal_id(self):
        goal = _make_goal(output_path="")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=False, plan=None, error="error"
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert report.goal_id == "test-goal"

    def test_failure_report_has_correct_mission_id(self):
        goal = _make_goal(output_path="")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=False, plan=None, error="error"
        )

        report = coordinator.execute(goal, _make_snapshot())

        assert report.mission_id == "mission-test-goal"


class TestCoordinatorSnapshotMetadata:
    def test_autonomous_success_report_has_planning_context(self):
        goal = _make_goal(output_path="")
        snapshot = _make_snapshot()
        plan = _make_plan("high")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True,
            plan=plan,
            error=None,
            planning_metadata=_make_planning_metadata(
                context="repository_snapshot_v1",
                entries="5",
                snapshot_id="snap-test-001",
            ),
        )
        mock_workflow.execute.return_value = _make_success_report()

        report = coordinator.execute(goal, snapshot)

        meta = dict(report.metadata)
        assert meta.get("planning_context") == "repository_snapshot_v1"

    def test_autonomous_success_report_has_snapshot_entries(self):
        goal = _make_goal(output_path="")
        snapshot = _make_snapshot()
        plan = _make_plan("high")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True,
            plan=plan,
            error=None,
            planning_metadata=_make_planning_metadata(entries="5"),
        )
        mock_workflow.execute.return_value = _make_success_report()

        report = coordinator.execute(goal, snapshot)

        meta = dict(report.metadata)
        assert meta.get("planning_snapshot_entries") == "5"

    def test_autonomous_success_report_has_snapshot_id(self):
        goal = _make_goal(output_path="")
        snapshot = _make_snapshot()
        plan = _make_plan("high")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True,
            plan=plan,
            error=None,
            planning_metadata=_make_planning_metadata(snapshot_id="snap-test-001"),
        )
        mock_workflow.execute.return_value = _make_success_report()

        report = coordinator.execute(goal, snapshot)

        meta = dict(report.metadata)
        assert meta.get("planning_snapshot_id") == "snap-test-001"

    def test_deterministic_mode_report_has_no_planning_context(self):
        goal = _make_goal(output_path="my-project/hello.py")
        snapshot = _make_snapshot()
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_workflow.execute.return_value = _make_success_report()

        report = coordinator.execute(goal, snapshot)

        meta = dict(report.metadata)
        assert "planning_context" not in meta

    def test_existing_workflow_metadata_preserved(self):
        goal = _make_goal(output_path="")
        snapshot = _make_snapshot()
        plan = _make_plan("high")
        coordinator, mock_planner, mock_workflow = _make_coordinator()
        mock_planner.plan.return_value = EngineeringPlanResult(
            success=True,
            plan=plan,
            error=None,
            planning_metadata=_make_planning_metadata(),
        )
        mock_workflow.execute.return_value = _make_success_report()  # has goal_id in metadata

        report = coordinator.execute(goal, snapshot)

        meta = dict(report.metadata)
        assert meta.get("goal_id") == "test-goal"  # from _make_success_report
        assert "planning_context" in meta  # added by coordinator
