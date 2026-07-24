from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import ExecutionPlan, LoadedSkill


class SkillNotFoundError(Exception):
    pass


class SkillLoader:
    def __init__(self, skills_root: Path | None = None) -> None:
        self.skills_root = (
            Path(skills_root) if skills_root is not None else config.skills_root()
        )

    def load(self, plan: ExecutionPlan) -> list[LoadedSkill]:
        capability_index = self._build_capability_index(self._discover_manifests())

        loaded_skills = []
        for step in plan.steps:
            capability_id = step.capability_id
            if capability_id is None:
                continue

            entry = capability_index.get(capability_id)
            if entry is None:
                raise SkillNotFoundError(
                    f"No registered skill satisfies capability: {capability_id}"
                )

            manifest, skill_path = entry
            loaded_skills.append(
                LoadedSkill(
                    id=manifest.get("id", capability_id),
                    name=manifest.get("name", capability_id),
                    version=manifest.get("version", ""),
                    path=skill_path,
                )
            )

        return loaded_skills

    def _discover_manifests(self) -> list[tuple[dict[str, Any], Path]]:
        result = []
        for path in sorted(self.skills_root.rglob("skill.yaml")):
            manifest = self._read_yaml(path)
            result.append((manifest, path.parent))
        return result

    def _build_capability_index(
        self, manifests: list[tuple[dict[str, Any], Path]]
    ) -> dict[str, tuple[dict[str, Any], Path]]:
        index: dict[str, tuple[dict[str, Any], Path]] = {}
        for manifest, skill_path in manifests:
            for capability_id in manifest.get("capabilities", []):
                index.setdefault(capability_id, (manifest, skill_path))
        return index

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
