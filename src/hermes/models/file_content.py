from dataclasses import dataclass


@dataclass(slots=True)
class FileContent:
    repository: str
    path: str
    content: str
