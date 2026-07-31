from dataclasses import dataclass


@dataclass(slots=True)
class Task:
    id: str
    business: str
    request: str
