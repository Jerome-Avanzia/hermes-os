"""Skill Registry — typed data contracts.

Sprint 54: Immutable data contracts for the SkillRegistry.

These types are DATA. They describe registry state, registration outcomes,
and indexed views. They never execute skills, perform provider calls,
or modify skill definitions.

All types are frozen dataclasses with slots.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from hermes.models.skill import InstalledSkill


# ── RegistrationStatus ────────────────────────────────────────────────────────


class RegistrationStatus(enum.Enum):
    """Outcome of a single skill registration attempt."""

    REGISTERED = "registered"   # skill accepted and indexed
    DUPLICATE = "duplicate"     # skill_id already present; registration rejected


# ── RegistrationResult ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """The outcome of a SkillRegistry.register() call.

    Immutable after construction.
    """

    skill_id: str
    status: RegistrationStatus
    message: str = ""


# ── RegistryEntry ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """A skill that has been accepted into the SkillRegistry.

    Wraps an InstalledSkill with the ordinal index at which it was registered
    (0-based, monotonically increasing). The index provides a stable,
    deterministic tie-breaker when multiple ordering criteria are equal.

    Immutable after construction.
    """

    skill: InstalledSkill
    registration_index: int


# ── CapabilityEntry ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CapabilityEntry:
    """Maps one capability ID to the skill IDs that declare it.

    skill_ids is sorted lexicographically for deterministic output.

    Immutable after construction.
    """

    capability_id: str
    skill_ids: tuple[str, ...]


# ── CapabilityIndex ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CapabilityIndex:
    """Immutable snapshot of the registry's capability → skill ID mapping.

    entries is sorted by capability_id for deterministic iteration.

    Immutable after construction.
    """

    entries: tuple[CapabilityEntry, ...]

    def skill_ids_for(self, capability_id: str) -> tuple[str, ...]:
        """Return all skill IDs that declare a capability, sorted.

        Returns an empty tuple if the capability is not indexed.
        """
        for entry in self.entries:
            if entry.capability_id == capability_id:
                return entry.skill_ids
        return ()

    def all_capability_ids(self) -> tuple[str, ...]:
        """Return all indexed capability IDs in sorted order."""
        return tuple(e.capability_id for e in self.entries)


# ── DependencyEdge ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class DependencyEdge:
    """A single directed dependency edge in the registry's dependency graph.

    Semantics: from_skill_id declares a dependency on to_skill_id.
    Orderable by (from_skill_id, to_skill_id) for deterministic sort.

    Immutable after construction.
    """

    from_skill_id: str
    to_skill_id: str


# ── DependencyGraph ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """Immutable snapshot of all dependency relationships in the registry.

    edges contains every directed edge, sorted by (from_skill_id, to_skill_id).
    Only edges where both endpoint skill IDs are registered appear here.

    Immutable after construction.
    """

    edges: tuple[DependencyEdge, ...]

    def dependencies_of(self, skill_id: str) -> tuple[str, ...]:
        """Return skill IDs that skill_id directly depends on, sorted."""
        return tuple(sorted(
            e.to_skill_id for e in self.edges if e.from_skill_id == skill_id
        ))

    def dependents_of(self, skill_id: str) -> tuple[str, ...]:
        """Return skill IDs that directly depend on skill_id, sorted."""
        return tuple(sorted(
            e.from_skill_id for e in self.edges if e.to_skill_id == skill_id
        ))

    def has_dependency(self, from_skill_id: str, to_skill_id: str) -> bool:
        """Return True if from_skill_id directly depends on to_skill_id."""
        target = DependencyEdge(from_skill_id=from_skill_id, to_skill_id=to_skill_id)
        return target in self.edges


# ── RegistryStatistics ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RegistryStatistics:
    """Aggregate statistics snapshot for the SkillRegistry.

    skills_by_status is a tuple of (status_value, count) pairs sorted
    by status value for deterministic output.

    Immutable after construction.
    """

    total_skills: int
    total_capabilities: int
    total_dependency_edges: int
    skills_with_dependencies: int
    skills_with_no_dependencies: int
    skills_by_status: tuple[tuple[str, int], ...]  # (status.value, count), sorted


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    "CapabilityEntry",
    "CapabilityIndex",
    "DependencyEdge",
    "DependencyGraph",
    "RegistrationResult",
    "RegistrationStatus",
    "RegistryEntry",
    "RegistryStatistics",
]
