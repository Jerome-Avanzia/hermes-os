"""Legacy demo entry point.

Superseded by the `hermes` CLI (src/hermes/cli/). Kept for now as a
reference wiring of the full pipeline; not the primary interface.
"""

from hermes.models import Plan, Task
from hermes.runtime.context_engine import ContextEngine
from hermes.runtime.execution_engine import ExecutionEngine


def main() -> None:
    print("Hermes OS starting... (legacy demo — see the `hermes` CLI)")

    task = Task(
        id="task-001",
        business="AVANZIA",
        request="Research Etsy opportunity for Black Cat Shirt",
    )
    print(task)

    context = ContextEngine().build(task)
    print(context)

    plan = Plan(
        task=task,
        steps=[
            "Research Etsy Opportunity",
            "Generate Design Concepts",
        ],
    )
    print(plan)

    result = ExecutionEngine().execute(plan)
    print(result)


if __name__ == "__main__":
    main()
