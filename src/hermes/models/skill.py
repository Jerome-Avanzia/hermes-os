"""Typed Skill Framework — data contracts.

Sprint 53: A Skill is an installable capability package.

Skills are DATA. A Skill declares what Hermes can do in a specific domain,
packages the knowledge and SOPs needed to do it, and references the external
tools required. A Skill never executes.

Pipeline integration:
  SkillManifest (parsed from skill.yaml)
      → loaded by SkillLoader
      → validated by SkillValidator
      → assembled into InstalledSkill

Immutability:
  All types are frozen dataclasses with slots.
  Lists from YAML are stored as tuples (immutable).
  Same inputs always produce the same objects.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path

# ── Patterns ──────────────────────────────────────────────────────────────────

# Valid skill ID: lowercase letters, digits, hyphens; must start with letter/digit
_SLUG_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Semantic version: exactly X.Y.Z where each part is one or more digits
_SEMVER_PATTERN: re.Pattern[str] = re.compile(r"^\d+\.\d+\.\d+$")


# ── Enums ──────────────────────────────────────────────────────────────────────


class SkillStatus(enum.Enum):
    """Lifecycle status of a skill.

    Controls whether the skill is indexed and available for matching.
    Skills are never deleted — they are deprecated.
    """

    DRAFT = "draft"                # not yet ready; excluded from matching
    ACTIVE = "active"              # production-ready; available for matching
    EXPERIMENTAL = "experimental"  # available but flagged for caution
    DEPRECATED = "deprecated"      # available but discouraged; will be removed


# ── SkillVersion ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class SkillVersion:
    """Semantic version for a skill (X.Y.Z).

    Comparable: SkillVersion(1, 2, 0) < SkillVersion(2, 0, 0).
    Ordering is derived from field declaration order (major → minor → patch).

    Immutable after construction.
    """

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, version_str: str) -> "SkillVersion":
        """Parse a semver string into a SkillVersion.

        Args:
            version_str: A string in "X.Y.Z" format where each part is
                a non-negative integer.

        Returns:
            SkillVersion instance.

        Raises:
            ValueError: If the string does not match X.Y.Z format.
        """
        stripped = version_str.strip()
        if not _SEMVER_PATTERN.match(stripped):
            raise ValueError(
                f"Invalid skill version: {version_str!r}. Expected X.Y.Z "
                f"(e.g. '1.0.0')."
            )
        parts = stripped.split(".")
        return cls(major=int(parts[0]), minor=int(parts[1]), patch=int(parts[2]))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


# ── SkillCapability ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillCapability:
    """A single capability declared by a skill.

    A capability ID is a string label (e.g. "copywriting", "python", "git").
    It is the key used by the CapabilityRegistry for matching.

    Immutable after construction.
    """

    id: str


# ── SkillDependency ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillDependency:
    """A declared dependency on another skill.

    skill_id must match the id of another registered skill.
    min_version is optional; if present, the dependency must be at or
    above this version.

    Immutable after construction.
    """

    skill_id: str
    min_version: SkillVersion | None = None


# ── SkillCompatibility ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillCompatibility:
    """Platform compatibility requirements for a skill.

    Declares the minimum (and optionally maximum) Hermes and Python
    versions required to use this skill. All fields are optional strings.
    Empty string means no constraint declared.

    Immutable after construction.
    """

    min_hermes_version: str = ""    # e.g. "3.0.0"
    min_python_version: str = ""    # e.g. "3.12"
    max_hermes_version: str = ""    # optional upper bound (e.g. "4.0.0")


# ── ExecutionDeclaration ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecutionDeclaration:
    """Declares which Execution Gateway adapters this skill requires.

    Populated from the `execution.adapters` section of skill.yaml.
    The Conductor uses this to validate adapter availability during planning.

    Example adapters: "llm", "git", "docker", "http", "filesystem",
    "database", "automation".

    Immutable after construction.
    """

    adapters: tuple[str, ...]


# ── SkillMetadata ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Organizational ownership metadata for a skill.

    Immutable after construction.
    """

    owner: str | None        # owning team or person (optional)
    department_id: str       # department that owns this skill


# ── SkillManifest ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """Typed, immutable representation of a parsed skill.yaml manifest.

    Sprint 53: The canonical typed contract for a skill's declaration.
    Produced by SkillLoader from a parsed YAML file.
    Never modified after construction.

    All list-typed YAML fields are stored as tuples to guarantee immutability.
    """

    # ── Identity ──────────────────────────────────────────────────────
    id: str                                      # unique slug (e.g. "copywriting")
    name: str                                    # display name
    version: SkillVersion                        # semantic version
    status: SkillStatus                          # lifecycle status
    description: str                             # human-readable summary

    # ── Organization ──────────────────────────────────────────────────
    metadata: SkillMetadata                      # owner and department

    # ── Capabilities ──────────────────────────────────────────────────
    capabilities: tuple[SkillCapability, ...]    # declared capability IDs
    provides: tuple[str, ...]                    # output types the skill provides

    # ── Interface ─────────────────────────────────────────────────────
    keywords: tuple[str, ...]                    # for CapabilityRegistry matching
    inputs: tuple[str, ...]                      # expected input types
    outputs: tuple[str, ...]                     # expected output types

    # ── Dependencies ──────────────────────────────────────────────────
    depends_on: tuple[SkillDependency, ...]      # required other skills

    # ── Cross-references ──────────────────────────────────────────────
    sop_refs: tuple[str, ...]                    # SOP IDs this skill follows
    repository_refs: tuple[str, ...]             # related repositories
    workflow_refs: tuple[str, ...]               # related automation workflows
    table_refs: tuple[str, ...]                  # related data tables
    model_refs: tuple[str, ...]                  # preferred LLM model IDs

    # ── Platform (optional) ───────────────────────────────────────────
    compatibility: SkillCompatibility | None     # platform version constraints
    execution: ExecutionDeclaration | None       # gateway adapter requirements

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, raw: dict) -> "SkillManifest":
        """Build a SkillManifest from a raw YAML dict.

        Required keys: id, name, version.
        All other keys are optional with sensible defaults.

        Raises:
            KeyError: If a required key is absent.
            ValueError: If a field value is invalid (e.g. bad version format,
                unknown status value).
        """
        skill_id = str(raw["id"])
        name = str(raw["name"])
        version = SkillVersion.parse(str(raw["version"]))
        status = SkillStatus(str(raw.get("status", "active")))
        description = str(raw.get("description", ""))

        metadata = SkillMetadata(
            owner=raw.get("owner") or None,
            department_id=str(raw.get("department_id", "")),
        )

        capabilities = tuple(
            SkillCapability(id=str(cap_id))
            for cap_id in (raw.get("capabilities") or [])
        )
        provides = tuple(str(p) for p in (raw.get("provides") or []))
        keywords = tuple(str(k) for k in (raw.get("keywords") or []))
        inputs = tuple(str(i) for i in (raw.get("inputs") or []))
        outputs = tuple(str(o) for o in (raw.get("outputs") or []))

        depends_on = tuple(
            SkillDependency(skill_id=str(dep))
            for dep in (raw.get("depends_on") or [])
        )

        sop_refs = tuple(str(r) for r in (raw.get("sop_refs") or []))
        repository_refs = tuple(str(r) for r in (raw.get("repository_refs") or []))
        workflow_refs = tuple(str(r) for r in (raw.get("workflow_refs") or []))
        table_refs = tuple(str(r) for r in (raw.get("table_refs") or []))
        model_refs = tuple(str(r) for r in (raw.get("model_refs") or []))

        # Optional compatibility section
        compatibility: SkillCompatibility | None = None
        compat_raw = raw.get("compatibility")
        if compat_raw:
            compatibility = SkillCompatibility(
                min_hermes_version=str(compat_raw.get("min_hermes_version", "")),
                min_python_version=str(compat_raw.get("min_python_version", "")),
                max_hermes_version=str(compat_raw.get("max_hermes_version", "")),
            )

        # Optional execution section
        execution: ExecutionDeclaration | None = None
        exec_raw = raw.get("execution")
        if exec_raw:
            execution = ExecutionDeclaration(
                adapters=tuple(str(a) for a in (exec_raw.get("adapters") or [])),
            )

        return cls(
            id=skill_id,
            name=name,
            version=version,
            status=status,
            description=description,
            metadata=metadata,
            capabilities=capabilities,
            provides=provides,
            keywords=keywords,
            inputs=inputs,
            outputs=outputs,
            depends_on=depends_on,
            sop_refs=sop_refs,
            repository_refs=repository_refs,
            workflow_refs=workflow_refs,
            table_refs=table_refs,
            model_refs=model_refs,
            compatibility=compatibility,
            execution=execution,
        )


# ── InstalledSkill ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    """A skill that has been loaded from disk and validated.

    Sprint 53: Produced by SkillLoader.load_skill() or
    SkillLoader.load_all_skills(). Contains the validated manifest plus
    filesystem metadata for locating knowledge and SOP documents.

    Immutable after construction.
    """

    manifest: SkillManifest                  # the validated typed manifest
    path: Path                               # absolute path to the skill directory
    knowledge_paths: tuple[str, ...]         # paths to knowledge files (relative to path)
    sop_paths: tuple[str, ...]               # paths to SOP files (relative to path)


# ── Public API ─────────────────────────────────────────────────────────────────

__all__ = [
    "ExecutionDeclaration",
    "InstalledSkill",
    "SkillCapability",
    "SkillCompatibility",
    "SkillDependency",
    "SkillManifest",
    "SkillMetadata",
    "SkillStatus",
    "SkillVersion",
]
