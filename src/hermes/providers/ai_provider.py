from abc import ABC, abstractmethod

from hermes.models import Context, ExecutionPlan, FileContent, LoadedSkill, Task, WorkspaceSnapshot


class AIProvider(ABC):
    @abstractmethod
    def generate(
        self,
        *,
        task: Task,
        context: Context,
        plan: ExecutionPlan,
        skills: list[LoadedSkill],
        workspace: WorkspaceSnapshot,
        file_contents: list[FileContent] | None = None,
    ) -> str: ...
