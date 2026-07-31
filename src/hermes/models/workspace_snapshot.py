from dataclasses import dataclass

from hermes.models.workspace_file import WorkspaceFile


@dataclass(slots=True)
class WorkspaceSnapshot:
    root: str
    files: list[WorkspaceFile]

    def __str__(self) -> str:
        return f"WorkspaceSnapshot(root={self.root}, files={len(self.files)})"
