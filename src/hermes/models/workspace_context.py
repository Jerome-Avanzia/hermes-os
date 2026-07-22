from dataclasses import dataclass

from hermes.models.workspace import Workspace


@dataclass(slots=True)
class WorkspaceContext:
    workspace: Workspace
    exists: bool
    is_git_repo: bool
    branch: str | None
    is_clean: bool | None
    environment: list[str]

    def __str__(self) -> str:
        status = "-" if self.is_clean is None else ("clean" if self.is_clean else "dirty")
        environment = ", ".join(self.environment) or "-"
        return (
            f"Workspace({self.workspace.path}, exists={self.exists}, "
            f"git={self.is_git_repo}, branch={self.branch or '-'}, "
            f"status={status}, environment=[{environment}])"
        )
