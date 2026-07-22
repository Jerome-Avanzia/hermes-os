from hermes.models import Context, ExecutionPlan, ExecutionStep


class Planner:
    def create(self, context: Context) -> ExecutionPlan:
        steps = [
            ExecutionStep(
                capability_id=capability.id,
                description=f"Apply capability: {capability.name}",
            )
            for capability in context.capabilities
        ]

        steps.append(
            ExecutionStep(capability_id=None, description="Await user approval")
        )

        return ExecutionPlan(task=context.task, steps=steps)
