from dataclasses import dataclass


@dataclass(slots=True)
class Workspace:
    project_id: str
    path: str
