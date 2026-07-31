from pathlib import Path
from typing import Any

import yaml

from hermes import config
from hermes.models import Capability, Task


class CapabilityEngine:
    def __init__(self, skills_root: Path | None = None) -> None:
        self.skills_root = (
            Path(skills_root) if skills_root is not None else config.skills_root()
        )

    def match(self, task: Task) -> list[Capability]:
        haystack = f"{task.business} {task.request}".lower()

        capabilities = []
        for manifest, _ in self._discover_manifests():
            keywords = manifest.get("keywords", [])

            if self._matches(keywords, haystack):
                capabilities.append(
                    Capability(
                        id=manifest.get("id", ""),
                        name=manifest.get("name", ""),
                        version=manifest.get("version", ""),
                        provides=manifest.get("provides", []),
                        keywords=keywords,
                    )
                )

        if not capabilities:
            capabilities.append(self._kernel_capability())

        return capabilities

    def _kernel_capability(self) -> Capability:
        manifest = self._read_yaml(self.skills_root / "kernel" / "skill.yaml")
        return Capability(
            id=manifest.get("id", "kernel"),
            name=manifest.get("name", "Kernel"),
            version=manifest.get("version", ""),
            provides=manifest.get("provides", []),
            keywords=[],
        )

    def _discover_manifests(self) -> list[tuple[dict[str, Any], Path]]:
        result = []
        for path in sorted(self.skills_root.rglob("skill.yaml")):
            manifest = self._read_yaml(path)
            result.append((manifest, path.parent))
        return result

    @staticmethod
    def _matches(keywords: list[str], haystack: str) -> bool:
        return any(keyword.lower() in haystack for keyword in keywords)

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
