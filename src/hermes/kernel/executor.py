from datetime import datetime

from hermes.models import ExecutionPlan, ExecutionResult, LoadedSkill


class Executor:
    def execute(
        self, plan: ExecutionPlan, skills: list[LoadedSkill]
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

        finished_at = datetime.now()

        return ExecutionResult(
            task=plan.task,
            project=plan.project,
            completed_steps=completed_steps,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
        )
