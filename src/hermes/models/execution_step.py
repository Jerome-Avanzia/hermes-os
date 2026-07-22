from dataclasses import dataclass


@dataclass(slots=True)
class ExecutionStep:
    capability_id: str | None
    description: str
