from pathlib import Path
from typing import Any

import yaml

from hermes.models import Capability, Task

DEFAULT_SKILLS_ROOT = Path("skills")


class CapabilityEngine:
    def __init__(self, skills_root: Path = DEFAULT_SKILLS_ROOT) -> None:
        self.skills_root = Path(skills_root)
        self.registry_path = self.skills_root / "registry.yaml"

    def match(self, task: Task) -> list[Capability]:
        registry = self._read_yaml(self.registry_path)
        skills = registry.get("skills", {})
        haystack = f"{task.business} {task.request}".lower()

        capabilities = []
        for skill_id, entry in skills.items():
            manifest = self._read_yaml(
                self.skills_root / entry["path"] / "skill.yaml"
            )
            keywords = manifest.get("keywords", [])

            if self._matches(keywords, haystack):
                capabilities.append(
                    Capability(
                        id=manifest.get("id", skill_id),
                        name=manifest.get("name", skill_id),
                        version=manifest.get("version", ""),
                        provides=manifest.get("provides", []),
                        keywords=keywords,
                    )
                )

        return capabilities

    @staticmethod
    def _matches(keywords: list[str], haystack: str) -> bool:
        return any(keyword.lower() in haystack for keyword in keywords)

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
