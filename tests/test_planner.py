from pathlib import Path

from hermes.kernel.capability_engine import CapabilityEngine
from hermes.kernel.planner import Planner
from hermes.models import (
    Context,
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


def test_plan_for_homepage_copy_task():
    context = _build_context("Write homepage copy for AVANZIA")
    execution_plan = Planner().create(context)

    capability_ids = [step.capability_id for step in execution_plan.steps]
    assert capability_ids == ["copywriting", None]
    assert execution_plan.steps[-1].description == "Await user approval"


def test_plan_for_python_refactor_task():
    context = _build_context("Refactor the Python backend")
    execution_plan = Planner().create(context)

    capability_ids = [step.capability_id for step in execution_plan.steps]
    assert capability_ids == ["python", None]
    assert execution_plan.steps[-1].description == "Await user approval"


def test_plan_for_unknown_task_still_appends_approval_step():
    context = _build_context("Plan a company offsite retreat")
    execution_plan = Planner().create(context)

    assert len(execution_plan.steps) == 1
    assert execution_plan.steps[0].capability_id is None
    assert execution_plan.steps[0].description == "Await user approval"
