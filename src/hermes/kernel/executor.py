import logging
from datetime import datetime

from hermes.models import ExecutionPlan, ExecutionResult, FileContent, LoadedSkill, WorkspaceSnapshot
from hermes.providers.ai_provider import AIProvider

logger = logging.getLogger(__name__)


class Executor:
    def execute(
        self,
        plan: ExecutionPlan,
        skills: list[LoadedSkill],
        workspace_snapshot: WorkspaceSnapshot,
        provider: AIProvider | None = None,
        file_contents: list[FileContent] | None = None,
    ) -> ExecutionResult:
        skill_by_capability = {skill.id: skill for skill in skills}
        started_at = datetime.now()

        completed_steps = []
        status = "completed"

        for step in plan.steps:
            if step.capability_id is None:
                status = "awaiting_approval"
                break

            skill = skill_by_capability.get(step.capability_id)
            completed_steps.append(skill.name if skill else step.description)

        generated_output = None
        if provider is not None:
            logger.info("Invoking provider %s for task %s", type(provider).__name__, plan.task.id)
            generated_output = provider.generate(
                task=plan.task,
                context=plan.context,
                plan=plan,
                skills=skills,
                workspace=workspace_snapshot,
                file_contents=file_contents,
            )

        finished_at = datetime.now()

        logger.info(
            "Executed plan for task %s: status=%s completed_steps=%d",
            plan.task.id,
            status,
            len(completed_steps),
        )

        return ExecutionResult(
            task=plan.task,
            project=plan.project,
            completed_steps=completed_steps,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            generated_output=generated_output,
        )
