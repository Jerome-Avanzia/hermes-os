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
    WorkspaceSnapshot,
)
from hermes.providers.ai_provider import AIProvider

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"

EMPTY_WORKSPACE_SNAPSHOT = WorkspaceSnapshot(root="/tmp/avanzia", files=[])


class FakeProvider(AIProvider):
    def __init__(self, response: str = "Generated proposal text") -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate(self, *, task, context, plan, skills, workspace, file_contents=None) -> str:
        self.calls.append(
            {
                "task": task,
                "context": context,
                "plan": plan,
                "skills": skills,
                "workspace": workspace,
                "file_contents": file_contents,
            }
        )
        return self.response


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


def _execute(
    request: str, provider: AIProvider | None = None
) -> tuple[ExecutionResult, ExecutionPlan]:
    context = _build_context(request)
    plan = Planner().create(context)
    skills = SkillLoader(skills_root=SKILLS_ROOT).load(plan)
    result = Executor().execute(
        plan, skills, EMPTY_WORKSPACE_SNAPSHOT, provider=provider
    )
    return result, plan


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
    # Kernel fallback is always matched, so it appears as a completed step.
    assert "Kernel" in result.completed_steps


def test_timestamps_are_recorded_in_order():
    result, _ = _execute("Refactor the Python backend")

    assert isinstance(result.started_at, datetime)
    assert isinstance(result.finished_at, datetime)
    assert result.finished_at >= result.started_at


def test_provider_none_preserves_deterministic_behavior():
    result, _ = _execute("Refactor the Python backend", provider=None)

    assert result.generated_output is None
    assert result.status == "awaiting_approval"
    assert result.completed_steps == ["Python"]


def test_provider_is_invoked_when_supplied():
    fake_provider = FakeProvider()

    result, plan = _execute("Refactor the Python backend", provider=fake_provider)

    assert len(fake_provider.calls) == 1
    call = fake_provider.calls[0]
    assert call["task"] is plan.task
    assert call["context"] is plan.context
    assert call["plan"] is plan
    assert call["workspace"] is EMPTY_WORKSPACE_SNAPSHOT


def test_generated_output_is_stored_from_provider():
    fake_provider = FakeProvider(response="A drafted proposal.")

    result, _ = _execute("Refactor the Python backend", provider=fake_provider)

    assert result.generated_output == "A drafted proposal."
    # Deterministic fields are unaffected by the provider being supplied.
    assert result.status == "awaiting_approval"
    assert result.completed_steps == ["Python"]
