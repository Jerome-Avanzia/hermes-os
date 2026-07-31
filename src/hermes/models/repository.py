from dataclasses import dataclass


@dataclass(slots=True)
class Repository:
    name: str
    path: str
    exists: bool
    is_git_repo: bool
    branch: str | None
    is_clean: bool | None
    environment: list[str]
