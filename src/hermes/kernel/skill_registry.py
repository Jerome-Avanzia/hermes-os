"""SkillRegistry — deterministic single source of truth for installed Skills.

Sprint 54: The registry manages metadata only.

Invariants:
  - The registry never executes Skills.
  - The registry never performs network downloads.
  - The registry never performs provider calls.
  - The registry never modifies Skill definitions.
  - The registry is deterministic: same inputs produce the same outputs.
  - The registry is provider-independent.

Registration policy:
  First-wins: the first registration of a given skill_id is accepted.
  Subsequent registrations of the same skill_id are rejected as DUPLICATE.

Ordering guarantees:
  - list_installed()       → sorted by manifest.id
  - find_by_capability()   → sorted by manifest.id
  - find_by_keyword()      → sorted by manifest.id
  - find_dependencies()    → sorted by manifest.id
  - find_dependents()      → sorted by manifest.id
  - list_capabilities()    → sorted lexicographically
  - capability_index()     → entries sorted by capability_id; skill_ids sorted
  - dependency_graph()     → edges sorted by (from_skill_id, to_skill_id)
  - statistics()           → skills_by_status sorted by status value

Thread-safety: SkillRegistry is not thread-safe. External locking is the
caller's responsibility if concurrent access is required.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from hermes.models.skill import InstalledSkill
from hermes.models.skill_registry import (
    CapabilityEntry,
    CapabilityIndex,
    DependencyEdge,
    DependencyGraph,
    RegistrationResult,
    RegistrationStatus,
    RegistryEntry,
    RegistryStatistics,
)


class SkillRegistry:
    """Deterministic in-memory registry for installed skills.

    Accepts InstalledSkill objects from SkillLoader and provides
    deterministic lookup, indexing, and snapshot APIs.

    Usage::

        registry = SkillRegistry()
        result = registry.register(installed_skill)
        # → RegistrationResult(skill_id='copywriting', status=REGISTERED, ...)

        entry = registry.find_by_id("copywriting")
        entries = registry.find_by_capability("llm")
        graph = registry.dependency_graph()
        stats = registry.statistics()
    """

    def __init__(self) -> None:
        # Primary store: skill_id → RegistryEntry (insertion order preserved
        # by dict in CPython 3.7+, but all public outputs are explicitly sorted)
        self._entries: dict[str, RegistryEntry] = {}
        # Secondary index: capability_id → set of skill_ids
        self._capability_index: dict[str, set[str]] = defaultdict(set)
        # Secondary index: keyword → set of skill_ids
        self._keyword_index: dict[str, set[str]] = defaultdict(set)
        # Monotonically increasing registration counter
        self._registration_counter: int = 0

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, skill: InstalledSkill) -> RegistrationResult:
        """Register an InstalledSkill in the registry.

        Accepts the skill if its ID has not been seen before. Indexes it
        by capability and keyword. Returns DUPLICATE (without modifying the
        registry) if the skill_id is already registered.

        Args:
            skill: A validated InstalledSkill produced by SkillLoader.

        Returns:
            RegistrationResult with status REGISTERED or DUPLICATE.
        """
        skill_id = skill.manifest.id

        if skill_id in self._entries:
            return RegistrationResult(
                skill_id=skill_id,
                status=RegistrationStatus.DUPLICATE,
                message=f"Skill {skill_id!r} is already registered; ignored",
            )

        entry = RegistryEntry(
            skill=skill,
            registration_index=self._registration_counter,
        )
        self._entries[skill_id] = entry
        self._registration_counter += 1

        # Index by capability
        for cap in skill.manifest.capabilities:
            self._capability_index[cap.id].add(skill_id)

        # Index by keyword
        for kw in skill.manifest.keywords:
            self._keyword_index[kw].add(skill_id)

        return RegistrationResult(
            skill_id=skill_id,
            status=RegistrationStatus.REGISTERED,
            message=f"Skill {skill_id!r} registered at index {entry.registration_index}",
        )

    def register_all(
        self, skills: list[InstalledSkill]
    ) -> tuple[RegistrationResult, ...]:
        """Register multiple skills. Results parallel the input list.

        Processes skills in input order. Duplicates within the batch are
        detected and returned as DUPLICATE after the first occurrence wins.

        Args:
            skills: List of InstalledSkill objects to register.

        Returns:
            Tuple of RegistrationResult, one per input skill, in input order.
        """
        return tuple(self.register(skill) for skill in skills)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def find_by_id(self, skill_id: str) -> RegistryEntry | None:
        """Return the RegistryEntry for skill_id, or None if not registered."""
        return self._entries.get(skill_id)

    def find_by_capability(self, capability_id: str) -> tuple[RegistryEntry, ...]:
        """Return all entries that declare a capability, sorted by skill ID."""
        skill_ids = self._capability_index.get(capability_id, set())
        return tuple(
            self._entries[sid]
            for sid in sorted(skill_ids)
            if sid in self._entries
        )

    def find_by_keyword(self, keyword: str) -> tuple[RegistryEntry, ...]:
        """Return all entries matching a keyword, sorted by skill ID."""
        skill_ids = self._keyword_index.get(keyword, set())
        return tuple(
            self._entries[sid]
            for sid in sorted(skill_ids)
            if sid in self._entries
        )

    def find_dependencies(self, skill_id: str) -> tuple[RegistryEntry, ...]:
        """Return registry entries for all direct dependencies of skill_id.

        Only returns entries for dependencies that are themselves registered.
        Dependencies declared on unregistered skills are silently omitted.
        Results sorted by skill ID.

        Returns an empty tuple if skill_id is not registered.
        """
        entry = self._entries.get(skill_id)
        if entry is None:
            return ()
        dep_ids = sorted(dep.skill_id for dep in entry.skill.manifest.depends_on)
        return tuple(
            self._entries[dep_id]
            for dep_id in dep_ids
            if dep_id in self._entries
        )

    def find_dependents(self, skill_id: str) -> tuple[RegistryEntry, ...]:
        """Return registry entries for all skills that directly depend on skill_id.

        Results sorted by skill ID.
        """
        dependents = [
            entry
            for entry in self._entries.values()
            if any(dep.skill_id == skill_id for dep in entry.skill.manifest.depends_on)
        ]
        return tuple(sorted(dependents, key=lambda e: e.skill.manifest.id))

    def list_installed(self) -> tuple[RegistryEntry, ...]:
        """Return all registered entries sorted by skill ID."""
        return tuple(self._entries[sid] for sid in sorted(self._entries))

    def list_capabilities(self) -> tuple[str, ...]:
        """Return all indexed capability IDs in sorted order."""
        return tuple(sorted(self._capability_index))

    # ── Snapshot APIs ─────────────────────────────────────────────────────────

    def capability_index(self) -> CapabilityIndex:
        """Return an immutable snapshot of the capability index.

        Entries sorted by capability_id; skill_ids within each entry sorted
        lexicographically.
        """
        entries = tuple(
            CapabilityEntry(
                capability_id=cap_id,
                skill_ids=tuple(sorted(self._capability_index[cap_id])),
            )
            for cap_id in sorted(self._capability_index)
        )
        return CapabilityIndex(entries=entries)

    def dependency_graph(self) -> DependencyGraph:
        """Return an immutable snapshot of the dependency graph.

        Includes only edges where both endpoint skill IDs are registered.
        Edges sorted by (from_skill_id, to_skill_id).
        """
        edges = []
        for skill_id, entry in self._entries.items():
            for dep in entry.skill.manifest.depends_on:
                if dep.skill_id in self._entries:
                    edges.append(DependencyEdge(
                        from_skill_id=skill_id,
                        to_skill_id=dep.skill_id,
                    ))
        return DependencyGraph(edges=tuple(sorted(edges)))

    def statistics(self) -> RegistryStatistics:
        """Return an immutable aggregate statistics snapshot."""
        status_counts: Counter[str] = Counter(
            entry.skill.manifest.status.value
            for entry in self._entries.values()
        )
        skills_with_deps = sum(
            1 for entry in self._entries.values()
            if entry.skill.manifest.depends_on
        )
        total_edges = sum(
            len(entry.skill.manifest.depends_on)
            for entry in self._entries.values()
        )
        return RegistryStatistics(
            total_skills=len(self._entries),
            total_capabilities=len(self._capability_index),
            total_dependency_edges=total_edges,
            skills_with_dependencies=skills_with_deps,
            skills_with_no_dependencies=len(self._entries) - skills_with_deps,
            skills_by_status=tuple(sorted(status_counts.items())),
        )

    # ── Container protocol ────────────────────────────────────────────────────

    def __len__(self) -> int:
        """Return the number of registered skills."""
        return len(self._entries)

    def __contains__(self, skill_id: object) -> bool:
        """Return True if skill_id is registered."""
        return skill_id in self._entries
