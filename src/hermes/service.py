from hermes.kernel.executor import Executor
from hermes.kernel.planner import Planner
from hermes.kernel.skill_loader import SkillLoader
from hermes.kernel.workspace_reader import WorkspaceReader
from hermes.models import ExecutionResult, Task
from hermes.providers.ai_provider import AIProvider
from hermes.runtime.context_engine import ContextEngine


class HermesService:
    def __init__(
        self,
        context_engine: ContextEngine | None = None,
        planner: Planner | None = None,
        skill_loader: SkillLoader | None = None,
        workspace_reader: WorkspaceReader | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.context_engine = context_engine or ContextEngine()
        self.planner = planner or Planner()
        self.skill_loader = skill_loader or SkillLoader()
        self.workspace_reader = workspace_reader or WorkspaceReader()
        self.executor = executor or Executor()

    def generate(
        self, task: str, provider: AIProvider | None = None
    ) -> ExecutionResult:
        hermes_task = Task(id="hermes-service", business="", request=task)

        context = self.context_engine.build(hermes_task)
        plan = self.planner.create(context)
        skills = self.skill_loader.load(plan)
        workspace_snapshot = self.workspace_reader.read(context.workspace)

        return self.executor.execute(
            plan, skills, workspace_snapshot, provider=provider
        )
