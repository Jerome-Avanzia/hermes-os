from hermes.models import Context, Plan


class PlanningEngine:
    def create(self, context: Context) -> Plan:
        return Plan(
            task=context.task,
            steps=[
                "Analyze request"
            ],
        )
