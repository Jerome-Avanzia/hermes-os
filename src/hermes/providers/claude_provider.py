import os

from anthropic import Anthropic

from hermes.models import Context, ExecutionPlan, LoadedSkill, Task, WorkspaceSnapshot
from hermes.providers.ai_provider import AIProvider

DEFAULT_MODEL = "claude-sonnet-5"
MAX_WORKSPACE_FILES_IN_PROMPT = 20


class ClaudeConfigurationError(Exception):
    pass


class ClaudeProvider(AIProvider):
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ClaudeConfigurationError(
                "ANTHROPIC_API_KEY is not set. Export it in your environment to "
                "use ClaudeProvider."
            )

        self._client = Anthropic(api_key=api_key)
        self._model = model

    def generate(
        self,
        *,
        task: Task,
        context: Context,
        plan: ExecutionPlan,
        skills: list[LoadedSkill],
        workspace: WorkspaceSnapshot,
    ) -> str:
        prompt = self._build_prompt(task, context, plan, skills, workspace)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        return "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

    @staticmethod
    def _build_prompt(
        task: Task,
        context: Context,
        plan: ExecutionPlan,
        skills: list[LoadedSkill],
        workspace: WorkspaceSnapshot,
    ) -> str:
        knowledge_summary = (
            "\n".join(f"- {document.title}" for document in context.knowledge.documents)
            or "-"
        )
        skill_summary = "\n".join(f"- {skill.name}" for skill in skills) or "-"
        step_summary = "\n".join(f"- {step.description}" for step in plan.steps) or "-"
        file_summary = (
            "\n".join(
                f"- {file.path}"
                for file in workspace.files[:MAX_WORKSPACE_FILES_IN_PROMPT]
            )
            or "-"
        )

        return (
            f"User task:\n{task.request}\n\n"
            f"Project: {context.project.name}\n\n"
            f"Relevant knowledge:\n{knowledge_summary}\n\n"
            f"Workspace snapshot ({len(workspace.files)} files read):\n{file_summary}\n\n"
            f"Loaded skills:\n{skill_summary}\n\n"
            f"Execution plan:\n{step_summary}\n\n"
            "Using the context above, draft a proposal for completing this task."
        )
