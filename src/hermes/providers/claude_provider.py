import os
from collections import defaultdict

from anthropic import Anthropic

from hermes.kernel.file_content_reader import FileContentReader
from hermes.models import Context, ExecutionPlan, FileContent, LoadedSkill, Task, WorkspaceFile, WorkspaceSnapshot
from hermes.providers.ai_provider import AIProvider

DEFAULT_MODEL = "claude-sonnet-5"
MAX_KNOWLEDGE_DOCUMENTS_IN_PROMPT = 3
MAX_WORKSPACE_FILES_IN_PROMPT = 20


def _build_workspace_file_summary(files: list[WorkspaceFile]) -> str:
    if not files:
        return "-"

    if any(f.repository for f in files):
        groups: dict[str, list[WorkspaceFile]] = defaultdict(list)
        for f in files:
            groups[f.repository or ""].append(f)

        sections = []
        for repo_name, repo_files in groups.items():
            file_list = "\n".join(f"- {f.path}" for f in repo_files)
            sections.append(f"[{repo_name}]\n{file_list}")
        return "\n\n".join(sections)

    return "\n".join(f"- {f.path}" for f in files)


def _build_file_contents_section(file_contents: list[FileContent]) -> str:
    if not file_contents:
        return "-"

    blocks = []
    for fc in file_contents:
        lang = FileContentReader.language(fc.path)
        fence = f"```{lang}" if lang else "```"
        header = f"Repository: {fc.repository}\nFile: {fc.path}" if fc.repository else f"File: {fc.path}"
        blocks.append(f"{header}\n{fence}\n{fc.content}\n```")
    return "\n\n".join(blocks)


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
        file_contents: list[FileContent] | None = None,
    ) -> str:
        prompt = self._build_prompt(task, context, plan, skills, workspace, file_contents)

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
        file_contents: list[FileContent] | None = None,
    ) -> str:
        knowledge_docs = context.knowledge.documents[:MAX_KNOWLEDGE_DOCUMENTS_IN_PROMPT]
        knowledge_section = (
            "\n\n---\n\n".join(
                f"## {doc.title}\n\n{doc.content}" for doc in knowledge_docs
            )
            or "-"
        )
        skill_summary = "\n".join(f"- {skill.name}" for skill in skills) or "-"
        step_summary = "\n".join(f"- {step.description}" for step in plan.steps) or "-"
        file_summary = _build_workspace_file_summary(
            workspace.files[:MAX_WORKSPACE_FILES_IN_PROMPT]
        )
        repo_files_section = _build_file_contents_section(file_contents or [])

        return (
            f"=== Knowledge ===\n\n"
            f"Project: {context.project.name}\n\n"
            f"Relevant knowledge:\n{knowledge_section}\n\n"
            f"Loaded skills:\n{skill_summary}\n\n"
            f"Execution plan:\n{step_summary}\n\n"
            f"=== Repository Files ===\n\n"
            f"Workspace snapshot ({len(workspace.files)} files read):\n{file_summary}\n\n"
            f"{repo_files_section}\n\n"
            f"=== User Request ===\n\n"
            f"{task.request}\n\n"
            "Using the context above, draft a proposal for completing this task."
        )
