from dataclasses import dataclass

from hermes.models.workspace import Workspace


@dataclass(slots=True)
class WorkspaceContext:
    workspace: Workspace
    exists: bool
    is_git_repo: bool
    branch: str | None
    is_clean: bool | None
    capabilities: list[str]
