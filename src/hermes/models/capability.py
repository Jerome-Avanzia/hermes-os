from dataclasses import dataclass


@dataclass(slots=True)
class Capability:
    id: str
    name: str
    version: str
    provides: list[str]
    keywords: list[str]
