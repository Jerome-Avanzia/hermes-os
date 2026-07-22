from dataclasses import dataclass


@dataclass(slots=True)
class Project:
    id: str
    name: str
    path: str

    def __str__(self) -> str:
        return f"Project(id={self.id}, name={self.name})"
