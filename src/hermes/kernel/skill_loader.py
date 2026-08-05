"""SkillLoader — loads skills from the filesystem.

Sprint 53: Extended with typed SkillManifest loading.

Two APIs are provided:

Legacy API (preserved from pre-Sprint 53):
  load(plan) → list[LoadedSkill]
  Used by HermesService for ExecutionPlan-based capability resolution.

Typed API (Sprint 53):
  load_skill(skill_dir) → InstalledSkill
    Load and validate one skill from a directory.

  load_all_skills() → list[InstalledSkill]
    Two-pass load: parse all manifests, then validate each against the
    full registry for dependency checking. Returns skills sorted by ID.

The loader:
- Reads skill.yaml from disk
- Parses YAML into SkillManifest via SkillManifest.from_dict()
- Validates via SkillValidator
- Discovers knowledge/ and sops/ directories
- Returns InstalledSkill objects

The loader never executes anything. No provider calls. No inference.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
import yaml.scanner

from hermes import config
from hermes.kernel.skill_validator import SkillValidator
from hermes.models import ExecutionPlan, LoadedSkill
from hermes.models.skill import InstalledSkill, SkillManifest


class SkillNotFoundError(Exception):
    """Raised when no registered skill satisfies a capability requirement."""


class SkillLoadError(Exception):
    """Raised when a skill cannot be loaded or its manifest is invalid."""


class SkillLoader:
    """Loads skills from the filesystem.

    The loader is deterministic: given the same skills root and files on
    disk, it always produces the same output.

    Args:
        skills_root: Root directory of the skills registry. Defaults to
            the value of the HERMES_SKILLS environment variable.
        validator: SkillValidator instance. Defaults to a new instance.
    """

    def __init__(
        self,
        skills_root: Path | None = None,
        validator: SkillValidator | None = None,
    ) -> None:
        self.skills_root = (
            Path(skills_root) if skills_root is not None else config.skills_root()
        )
        self._validator = validator or SkillValidator()

    # ── Legacy API ────────────────────────────────────────────────────────────

    def load(self, plan: ExecutionPlan) -> list[LoadedSkill]:
        """Load skills required by an ExecutionPlan (legacy API).

        Resolves each step's capability_id to a LoadedSkill by searching
        skill manifests on disk. Raises SkillNotFoundError if any
        capability cannot be satisfied.

        This method is preserved for backward compatibility with
        HermesService and pre-Sprint 53 callers.
        """
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

    # ── Typed API (Sprint 53) ─────────────────────────────────────────────────

    def load_skill(self, skill_dir: Path) -> InstalledSkill:
        """Load and validate one skill from a directory.

        Reads skill.yaml, parses the manifest, validates it, and
        discovers knowledge and SOP files. Dependency availability
        is NOT checked here (only within load_all_skills, which has
        the full registry).

        Args:
            skill_dir: Path to the skill directory containing skill.yaml.

        Returns:
            InstalledSkill with a validated SkillManifest.

        Raises:
            SkillLoadError: If skill.yaml is missing, unparseable, or
                fails validation.
        """
        manifest_path = skill_dir / "skill.yaml"
        if not manifest_path.exists():
            raise SkillLoadError(
                f"No skill.yaml found in {skill_dir}"
            )

        raw = self._read_yaml(manifest_path)

        try:
            manifest = SkillManifest.from_dict(raw)
        except (KeyError, ValueError) as exc:
            raise SkillLoadError(
                f"Failed to parse manifest in {skill_dir}: {exc}"
            ) from exc

        result = self._validator.validate(manifest)
        if not result.valid:
            messages = "; ".join(
                f"{e.field}: {e.message}" for e in result.errors
            )
            raise SkillLoadError(
                f"Skill manifest in {skill_dir} failed validation: {messages}"
            )

        return InstalledSkill(
            manifest=manifest,
            path=skill_dir.resolve(),
            knowledge_paths=self._discover_knowledge(skill_dir),
            sop_paths=self._discover_sops(skill_dir),
        )

    def load_all_skills(self) -> list[InstalledSkill]:
        """Load all valid skills from the skills root.

        Two-pass algorithm:

        Pass 1 — Parse: collect all skill.yaml files, parse each into a
          SkillManifest, record skill IDs. First-manifest-wins for
          duplicate IDs (sorted directory order). Unparseable manifests
          are silently skipped.

        Pass 2 — Validate: validate each manifest against the complete
          set of available skill IDs (enabling dependency checking).
          Manifests that fail validation are silently skipped.

        Returns skills sorted by manifest ID. Deterministic: same files
        on disk always produce the same list in the same order.

        Returns:
            List of InstalledSkill objects, sorted by manifest.id.
        """
        skill_dirs = [
            p.parent
            for p in sorted(self.skills_root.rglob("skill.yaml"))
        ]

        # Pass 1: parse all manifests
        parsed: dict[str, tuple[SkillManifest, Path]] = {}
        for skill_dir in skill_dirs:
            try:
                raw = self._read_yaml(skill_dir / "skill.yaml")
                manifest = SkillManifest.from_dict(raw)
            except (KeyError, ValueError, yaml.YAMLError):
                continue  # skip unparseable manifests
            if manifest.id not in parsed:  # first-manifest-wins
                parsed[manifest.id] = (manifest, skill_dir)

        available_ids = frozenset(parsed.keys())

        # Pass 2: validate against full registry
        installed: list[InstalledSkill] = []
        for manifest_id in sorted(parsed):
            manifest, skill_dir = parsed[manifest_id]
            result = self._validator.validate(
                manifest, available_skill_ids=available_ids
            )
            if not result.valid:
                continue  # skip invalid skills
            installed.append(InstalledSkill(
                manifest=manifest,
                path=skill_dir.resolve(),
                knowledge_paths=self._discover_knowledge(skill_dir),
                sop_paths=self._discover_sops(skill_dir),
            ))

        return installed

    # ── Private helpers ───────────────────────────────────────────────────────

    def _discover_knowledge(self, skill_dir: Path) -> tuple[str, ...]:
        """Discover knowledge documents in a skill directory.

        Returns relative paths (relative to skill_dir) of all .md files
        found under skill_dir/knowledge/, sorted lexicographically.
        Returns an empty tuple if the directory does not exist.
        """
        knowledge_dir = skill_dir / "knowledge"
        if not knowledge_dir.is_dir():
            return ()
        return tuple(
            str(p.relative_to(skill_dir))
            for p in sorted(knowledge_dir.rglob("*.md"))
        )

    def _discover_sops(self, skill_dir: Path) -> tuple[str, ...]:
        """Discover SOP files in a skill directory.

        Returns relative paths (relative to skill_dir) of all .md files
        found under skill_dir/sops/, sorted lexicographically.
        Returns an empty tuple if the directory does not exist.
        """
        sops_dir = skill_dir / "sops"
        if not sops_dir.is_dir():
            return ()
        return tuple(
            str(p.relative_to(skill_dir))
            for p in sorted(sops_dir.rglob("*.md"))
        )

    def _discover_manifests(self) -> list[tuple[dict[str, Any], Path]]:
        """Discover all skill.yaml files under skills_root (legacy helper)."""
        result = []
        for path in sorted(self.skills_root.rglob("skill.yaml")):
            manifest = self._read_yaml(path)
            result.append((manifest, path.parent))
        return result

    def _build_capability_index(
        self,
        manifests: list[tuple[dict[str, Any], Path]],
    ) -> dict[str, tuple[dict[str, Any], Path]]:
        """Build a capability-ID → (manifest, path) index (legacy helper)."""
        index: dict[str, tuple[dict[str, Any], Path]] = {}
        for manifest, skill_path in manifests:
            for capability_id in manifest.get("capabilities", []):
                index.setdefault(capability_id, (manifest, skill_path))
        return index

    @staticmethod
    def _read_yaml(path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
