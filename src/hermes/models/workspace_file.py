from dataclasses import dataclass


@dataclass(slots=True)
class WorkspaceFile:
    path: str
    extension: str
    size: int
    content: str
    repository: str = ""

    def __str__(self) -> str:
        return f"WorkspaceFile({self.path}, {self.size}B)"
