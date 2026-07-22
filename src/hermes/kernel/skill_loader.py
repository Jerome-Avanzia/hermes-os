from pathlib import Path
from typing import Any

import yaml

from hermes.models import ExecutionPlan, LoadedSkill

DEFAULT_SKILLS_ROOT = Path("skills")


class SkillNotFoundError(Exception):
    pass


class SkillLoader:
    def __init__(self, skills_root: Path = DEFAULT_SKILLS_ROOT) -> None:
        self.skills_root = Path(skills_root)
        self.registry_path = self.skills_root / "registry.yaml"

    def load(self, plan: ExecutionPlan) -> list[LoadedSkill]:
        registry = self._read_yaml(self.registry_path)
        entries = registry.get("skills", {})
        capability_index = self._build_capability_index(entries)

        loaded_skills = []
        for step in plan.steps:
            capability_id = step.capability_id
            if capability_id is None:
                continue

            skill_id = capability_index.get(capability_id)
            if skill_id is None:
                raise SkillNotFoundError(
                    f"No registered skill satisfies capability: {capability_id}"
                )

            skill_path = self.skills_root / entries[skill_id]["path"]
            manifest = self._read_yaml(skill_path / "skill.yaml")

            loaded_skills.append(
                LoadedSkill(
                    id=manifest.get("id", skill_id),
                    name=manifest.get("name", skill_id),
                    version=manifest.get("version", ""),
                    path=skill_path,
                )
            )

        return loaded_skills

    def _build_capability_index(self, entries: dict[str, Any]) -> dict[str, str]:
        index: dict[str, str] = {}
        for skill_id, entry in entries.items():
            manifest = self._read_yaml(
                self.skills_root / entry["path"] / "skill.yaml"
            )
            for capability_id in manifest.get("capabilities", []):
                index.setdefault(capability_id, skill_id)
        return index

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
