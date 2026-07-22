from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class LoadedSkill:
    id: str
    name: str
    version: str
    path: Path
