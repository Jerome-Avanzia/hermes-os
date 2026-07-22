from datetime import datetime
from pathlib import Path

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.executor import Executor
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.models import (
    Context,
    ExecutionPlan,
    ExecutionResult,
    KnowledgeContext,
    Project,
    Task,
    Workspace,
    WorkspaceContext,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


def _build_context(request: str) -> Context:
    task = Task(id="t", business="AVANZIA", request=request)
    project = Project(id="AVANZIA", name="AVANZIA", path="knowledge/AVANZIA")
    knowledge = KnowledgeContext(project=project, documents=[])
    workspace = WorkspaceContext(
        workspace=Workspace(project_id="AVANZIA", path="/tmp/avanzia"),
        exists=True,
        is_git_repo=True,
        branch="main",
        is_clean=True,
        environment=[],
    )
    capabilities = CapabilityEngine(skills_root=SKILLS_ROOT).match(task)

    return Context(
        task=task,
        project=project,
        knowledge=knowledge,
        workspace=workspace,
        capabilities=capabilities,
    )


def _execute(request: str) -> tuple[ExecutionResult, ExecutionPlan]:
    context = _build_context(request)
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)
    return Executor().execute(plan, skills), plan


def test_homepage_task_returns_awaiting_approval():
    result, _ = _execute("Write homepage copy that reflects our brand strategy")

    assert isinstance(result, ExecutionResult)
    assert result.status == "awaiting_approval"


def test_completed_steps_contains_every_step_before_approval():
    result, plan = _execute("Write homepage copy that reflects our brand strategy")

    non_approval_steps = [
        step for step in plan.steps if step.capability_id is not None
    ]
    assert len(result.completed_steps) == len(non_approval_steps)
    assert "Brand Strategy" in result.completed_steps
    assert "Copywriting" in result.completed_steps


def test_approval_step_is_not_marked_completed():
    result, _ = _execute("Write homepage copy that reflects our brand strategy")

    assert "Await user approval" not in result.completed_steps


def test_unknown_task_still_returns_awaiting_approval():
    result, _ = _execute("Plan a company offsite retreat")

    assert result.status == "awaiting_approval"
    assert result.completed_steps == []


def test_timestamps_are_recorded_in_order():
    result, _ = _execute("Refactor the Python backend")

    assert isinstance(result.started_at, datetime)
    assert isinstance(result.finished_at, datetime)
    assert result.finished_at >= result.started_at
