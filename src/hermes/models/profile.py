from dataclasses import dataclass, field


@dataclass(slots=True)
class Profile:
    id: str
    name: str
    system_prompt: str
    description: str = ""
    model: str | None = None
