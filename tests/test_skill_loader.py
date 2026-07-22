from pathlib import Path

import pytest

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader, SkillNotFoundError
from hermes.models import (
    Context,
    ExecutionPlan,
    ExecutionStep,
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


def _loader() -> SkillLoader:
    return SkillLoader(skills_root=SKILLS_ROOT)


def test_loads_copywriting_and_brand_strategy_for_homepage_copy_task():
    context = _build_context(
        "Write homepage copy that reflects our brand strategy"
    )
    execution_plan = Planner().create(context)
    loaded_skills = _loader().load(execution_plan)

    ids = [skill.id for skill in loaded_skills]
    assert set(ids) == {"copywriting", "brand-strategy"}
    assert len(ids) == 2


def test_loads_python_for_python_task():
    context = _build_context("Refactor the Python backend")
    execution_plan = Planner().create(context)
    loaded_skills = _loader().load(execution_plan)

    assert [skill.id for skill in loaded_skills] == ["python"]
    assert loaded_skills[0].name == "Python"


def test_unknown_task_loads_zero_skills():
    context = _build_context("Plan a company offsite retreat")
    execution_plan = Planner().create(context)
    loaded_skills = _loader().load(execution_plan)

    assert loaded_skills == []


def test_missing_skill_raises_skill_not_found_error():
    task = Task(id="t", business="AVANZIA", request="test")
    execution_plan = ExecutionPlan(
        task=task,
        steps=[ExecutionStep(capability_id="does-not-exist", description="x")],
    )

    with pytest.raises(SkillNotFoundError):
        _loader().load(execution_plan)
