from hermes.models import Context, Task


class ContextEngine:
    def build(self, task: Task) -> Context:
        return Context(
            task=task,
            capabilities=[],
        )
