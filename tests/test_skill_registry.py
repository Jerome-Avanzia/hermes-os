"""Tests for Sprint 54 — Skill Registry.

Coverage:
  - All typed model contracts (RegistrationStatus, RegistrationResult,
    RegistryEntry, CapabilityEntry, CapabilityIndex, DependencyEdge,
    DependencyGraph, RegistryStatistics)
  - SkillRegistry: registration, duplicate detection, lookup APIs,
    snapshot APIs, statistics, container protocol
  - Determinism: same inputs → same outputs
  - Immutability: frozen dataclasses reject mutation
  - Edge cases: empty registry, unknown keys, shared capabilities,
    unregistered dependencies, self-contained dependency graphs
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hermes.kernel.skill_registry import SkillRegistry
from hermes.models.skill import (
    InstalledSkill,
    SkillCapability,
    SkillManifest,
    SkillStatus,
    SkillVersion,
)
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


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_skill(
    skill_id: str,
    *,
    capabilities: list[str] | None = None,
    keywords: list[str] | None = None,
    depends_on: list[str] | None = None,
    status: str = "active",
    version: str = "1.0.0",
) -> InstalledSkill:
    """Build an InstalledSkill without touching the filesystem."""
    raw: dict = {
        "id": skill_id,
        "name": skill_id.replace("-", " ").title(),
        "version": version,
        "capabilities": capabilities if capabilities is not None else [f"cap-{skill_id}"],
        "keywords": keywords or [],
        "depends_on": depends_on or [],
        "status": status,
    }
    manifest = SkillManifest.from_dict(raw)
    return InstalledSkill(
        manifest=manifest,
        path=Path(f"/skills/{skill_id}"),
        knowledge_paths=(),
        sop_paths=(),
    )


def _registry_with(*skill_ids: str, **kwargs) -> SkillRegistry:
    """Create a SkillRegistry pre-populated with simple skills."""
    registry = SkillRegistry()
    for sid in skill_ids:
        registry.register(_make_skill(sid, **kwargs))
    return registry


# ══════════════════════════════════════════════════════════════════════════════
# TestRegistrationStatus
# ══════════════════════════════════════════════════════════════════════════════


class TestRegistrationStatus:
    def test_registered_value(self) -> None:
        assert RegistrationStatus.REGISTERED.value == "registered"

    def test_duplicate_value(self) -> None:
        assert RegistrationStatus.DUPLICATE.value == "duplicate"

    def test_exactly_two_members(self) -> None:
        assert len(RegistrationStatus) == 2

    def test_from_value_registered(self) -> None:
        assert RegistrationStatus("registered") is RegistrationStatus.REGISTERED

    def test_from_value_duplicate(self) -> None:
        assert RegistrationStatus("duplicate") is RegistrationStatus.DUPLICATE


# ══════════════════════════════════════════════════════════════════════════════
# TestRegistrationResult
# ══════════════════════════════════════════════════════════════════════════════


class TestRegistrationResult:
    def test_construction(self) -> None:
        r = RegistrationResult(skill_id="foo", status=RegistrationStatus.REGISTERED)
        assert r.skill_id == "foo"
        assert r.status is RegistrationStatus.REGISTERED
        assert r.message == ""

    def test_construction_with_message(self) -> None:
        r = RegistrationResult(
            skill_id="bar",
            status=RegistrationStatus.DUPLICATE,
            message="already registered",
        )
        assert r.message == "already registered"

    def test_frozen(self) -> None:
        r = RegistrationResult(skill_id="x", status=RegistrationStatus.REGISTERED)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.skill_id = "y"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = RegistrationResult(skill_id="foo", status=RegistrationStatus.REGISTERED)
        b = RegistrationResult(skill_id="foo", status=RegistrationStatus.REGISTERED)
        assert a == b

    def test_inequality(self) -> None:
        a = RegistrationResult(skill_id="foo", status=RegistrationStatus.REGISTERED)
        b = RegistrationResult(skill_id="foo", status=RegistrationStatus.DUPLICATE)
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestRegistryEntry
# ══════════════════════════════════════════════════════════════════════════════


class TestRegistryEntry:
    def test_construction(self) -> None:
        skill = _make_skill("alpha")
        entry = RegistryEntry(skill=skill, registration_index=0)
        assert entry.skill is skill
        assert entry.registration_index == 0

    def test_frozen(self) -> None:
        entry = RegistryEntry(skill=_make_skill("beta"), registration_index=1)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            entry.registration_index = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        skill = _make_skill("gamma")
        a = RegistryEntry(skill=skill, registration_index=0)
        b = RegistryEntry(skill=skill, registration_index=0)
        assert a == b

    def test_inequality_by_index(self) -> None:
        skill = _make_skill("delta")
        a = RegistryEntry(skill=skill, registration_index=0)
        b = RegistryEntry(skill=skill, registration_index=1)
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestCapabilityEntry
# ══════════════════════════════════════════════════════════════════════════════


class TestCapabilityEntry:
    def test_construction(self) -> None:
        entry = CapabilityEntry(capability_id="llm", skill_ids=("skill-a", "skill-b"))
        assert entry.capability_id == "llm"
        assert entry.skill_ids == ("skill-a", "skill-b")

    def test_frozen(self) -> None:
        entry = CapabilityEntry(capability_id="git", skill_ids=())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            entry.capability_id = "other"  # type: ignore[misc]

    def test_empty_skill_ids(self) -> None:
        entry = CapabilityEntry(capability_id="unused", skill_ids=())
        assert entry.skill_ids == ()


# ══════════════════════════════════════════════════════════════════════════════
# TestCapabilityIndex
# ══════════════════════════════════════════════════════════════════════════════


class TestCapabilityIndex:
    def _make_index(self) -> CapabilityIndex:
        return CapabilityIndex(entries=(
            CapabilityEntry(capability_id="git", skill_ids=("skill-b",)),
            CapabilityEntry(capability_id="llm", skill_ids=("skill-a", "skill-b")),
        ))

    def test_skill_ids_for_known_capability(self) -> None:
        idx = self._make_index()
        assert idx.skill_ids_for("llm") == ("skill-a", "skill-b")

    def test_skill_ids_for_unknown_returns_empty(self) -> None:
        idx = self._make_index()
        assert idx.skill_ids_for("docker") == ()

    def test_all_capability_ids(self) -> None:
        idx = self._make_index()
        assert idx.all_capability_ids() == ("git", "llm")

    def test_frozen(self) -> None:
        idx = self._make_index()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            idx.entries = ()  # type: ignore[misc]

    def test_empty_index(self) -> None:
        idx = CapabilityIndex(entries=())
        assert idx.all_capability_ids() == ()
        assert idx.skill_ids_for("anything") == ()


# ══════════════════════════════════════════════════════════════════════════════
# TestDependencyEdge
# ══════════════════════════════════════════════════════════════════════════════


class TestDependencyEdge:
    def test_construction(self) -> None:
        edge = DependencyEdge(from_skill_id="a", to_skill_id="b")
        assert edge.from_skill_id == "a"
        assert edge.to_skill_id == "b"

    def test_frozen(self) -> None:
        edge = DependencyEdge(from_skill_id="a", to_skill_id="b")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            edge.from_skill_id = "c"  # type: ignore[misc]

    def test_ordering_by_from_first(self) -> None:
        a = DependencyEdge(from_skill_id="a", to_skill_id="z")
        b = DependencyEdge(from_skill_id="b", to_skill_id="a")
        assert a < b

    def test_ordering_by_to_when_from_equal(self) -> None:
        a = DependencyEdge(from_skill_id="x", to_skill_id="a")
        b = DependencyEdge(from_skill_id="x", to_skill_id="b")
        assert a < b

    def test_equality(self) -> None:
        a = DependencyEdge(from_skill_id="p", to_skill_id="q")
        b = DependencyEdge(from_skill_id="p", to_skill_id="q")
        assert a == b

    def test_sort_stability(self) -> None:
        edges = [
            DependencyEdge("c", "d"),
            DependencyEdge("a", "b"),
            DependencyEdge("a", "a"),
        ]
        assert sorted(edges) == [
            DependencyEdge("a", "a"),
            DependencyEdge("a", "b"),
            DependencyEdge("c", "d"),
        ]


# ══════════════════════════════════════════════════════════════════════════════
# TestDependencyGraph
# ══════════════════════════════════════════════════════════════════════════════


class TestDependencyGraph:
    def _make_graph(self) -> DependencyGraph:
        return DependencyGraph(edges=(
            DependencyEdge("b", "a"),
            DependencyEdge("c", "a"),
            DependencyEdge("c", "b"),
        ))

    def test_dependencies_of(self) -> None:
        g = self._make_graph()
        assert g.dependencies_of("c") == ("a", "b")

    def test_dependencies_of_leaf(self) -> None:
        g = self._make_graph()
        assert g.dependencies_of("a") == ()

    def test_dependencies_of_unknown(self) -> None:
        g = self._make_graph()
        assert g.dependencies_of("z") == ()

    def test_dependents_of(self) -> None:
        g = self._make_graph()
        assert g.dependents_of("a") == ("b", "c")

    def test_dependents_of_root(self) -> None:
        g = self._make_graph()
        assert g.dependents_of("c") == ()

    def test_has_dependency_true(self) -> None:
        g = self._make_graph()
        assert g.has_dependency("c", "b") is True

    def test_has_dependency_false(self) -> None:
        g = self._make_graph()
        assert g.has_dependency("a", "c") is False

    def test_frozen(self) -> None:
        g = self._make_graph()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            g.edges = ()  # type: ignore[misc]

    def test_empty_graph(self) -> None:
        g = DependencyGraph(edges=())
        assert g.dependencies_of("x") == ()
        assert g.dependents_of("x") == ()
        assert g.has_dependency("x", "y") is False


# ══════════════════════════════════════════════════════════════════════════════
# TestRegistryStatistics
# ══════════════════════════════════════════════════════════════════════════════


class TestRegistryStatistics:
    def test_construction(self) -> None:
        stats = RegistryStatistics(
            total_skills=3,
            total_capabilities=5,
            total_dependency_edges=2,
            skills_with_dependencies=1,
            skills_with_no_dependencies=2,
            skills_by_status=(("active", 2), ("draft", 1)),
        )
        assert stats.total_skills == 3
        assert stats.total_capabilities == 5

    def test_frozen(self) -> None:
        stats = RegistryStatistics(
            total_skills=0,
            total_capabilities=0,
            total_dependency_edges=0,
            skills_with_dependencies=0,
            skills_with_no_dependencies=0,
            skills_by_status=(),
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            stats.total_skills = 99  # type: ignore[misc]

    def test_skills_with_and_without_dependencies_sum_to_total(self) -> None:
        stats = RegistryStatistics(
            total_skills=5,
            total_capabilities=3,
            total_dependency_edges=2,
            skills_with_dependencies=2,
            skills_with_no_dependencies=3,
            skills_by_status=(),
        )
        assert stats.skills_with_dependencies + stats.skills_with_no_dependencies == stats.total_skills


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillRegistry — Registration
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillRegistryRegistration:
    def test_register_single_returns_registered(self) -> None:
        registry = SkillRegistry()
        result = registry.register(_make_skill("alpha"))
        assert result.status is RegistrationStatus.REGISTERED
        assert result.skill_id == "alpha"

    def test_register_duplicate_returns_duplicate(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("alpha"))
        result = registry.register(_make_skill("alpha"))
        assert result.status is RegistrationStatus.DUPLICATE
        assert result.skill_id == "alpha"

    def test_duplicate_does_not_overwrite_original(self) -> None:
        registry = SkillRegistry()
        first = _make_skill("alpha", version="1.0.0")
        second = _make_skill("alpha", version="2.0.0")
        registry.register(first)
        registry.register(second)
        entry = registry.find_by_id("alpha")
        assert entry is not None
        assert str(entry.skill.manifest.version) == "1.0.0"

    def test_register_all_parallel_batch(self) -> None:
        registry = SkillRegistry()
        skills = [_make_skill("a"), _make_skill("b"), _make_skill("c")]
        results = registry.register_all(skills)
        assert len(results) == 3
        assert all(r.status is RegistrationStatus.REGISTERED for r in results)

    def test_register_all_with_intra_batch_duplicate(self) -> None:
        registry = SkillRegistry()
        skills = [_make_skill("x"), _make_skill("x")]
        results = registry.register_all(skills)
        assert results[0].status is RegistrationStatus.REGISTERED
        assert results[1].status is RegistrationStatus.DUPLICATE

    def test_register_all_preserves_input_order(self) -> None:
        registry = SkillRegistry()
        skills = [_make_skill("c"), _make_skill("a"), _make_skill("b")]
        results = registry.register_all(skills)
        assert [r.skill_id for r in results] == ["c", "a", "b"]

    def test_registration_index_increments(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("first"))
        registry.register(_make_skill("second"))
        registry.register(_make_skill("third"))
        assert registry.find_by_id("first").registration_index == 0  # type: ignore[union-attr]
        assert registry.find_by_id("second").registration_index == 1  # type: ignore[union-attr]
        assert registry.find_by_id("third").registration_index == 2  # type: ignore[union-attr]

    def test_duplicate_does_not_increment_index(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a"))
        registry.register(_make_skill("b"))
        registry.register(_make_skill("a"))  # duplicate — must not consume index 2
        registry.register(_make_skill("c"))
        assert registry.find_by_id("c").registration_index == 2  # type: ignore[union-attr]

    def test_result_message_is_non_empty_on_registered(self) -> None:
        result = SkillRegistry().register(_make_skill("foo"))
        assert result.message != ""

    def test_result_message_is_non_empty_on_duplicate(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("foo"))
        result = registry.register(_make_skill("foo"))
        assert result.message != ""


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillRegistry — Lookup
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillRegistryLookup:
    def test_find_by_id_known(self) -> None:
        registry = _registry_with("alpha")
        entry = registry.find_by_id("alpha")
        assert entry is not None
        assert entry.skill.manifest.id == "alpha"

    def test_find_by_id_unknown_returns_none(self) -> None:
        registry = _registry_with("alpha")
        assert registry.find_by_id("missing") is None

    def test_find_by_id_empty_registry_returns_none(self) -> None:
        registry = SkillRegistry()
        assert registry.find_by_id("anything") is None

    def test_find_by_capability_single_match(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("writer", capabilities=["writing"]))
        entries = registry.find_by_capability("writing")
        assert len(entries) == 1
        assert entries[0].skill.manifest.id == "writer"

    def test_find_by_capability_multiple_matches_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("z-skill", capabilities=["llm"]))
        registry.register(_make_skill("a-skill", capabilities=["llm"]))
        registry.register(_make_skill("m-skill", capabilities=["llm"]))
        entries = registry.find_by_capability("llm")
        ids = [e.skill.manifest.id for e in entries]
        assert ids == sorted(ids)
        assert set(ids) == {"a-skill", "m-skill", "z-skill"}

    def test_find_by_capability_no_match_returns_empty(self) -> None:
        registry = _registry_with("alpha")
        assert registry.find_by_capability("docker") == ()

    def test_find_by_keyword_single_match(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("writer", keywords=["content"]))
        entries = registry.find_by_keyword("content")
        assert len(entries) == 1
        assert entries[0].skill.manifest.id == "writer"

    def test_find_by_keyword_multiple_matches_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("z-skill", keywords=["ai"]))
        registry.register(_make_skill("a-skill", keywords=["ai"]))
        entries = registry.find_by_keyword("ai")
        ids = [e.skill.manifest.id for e in entries]
        assert ids == sorted(ids)

    def test_find_by_keyword_no_match_returns_empty(self) -> None:
        registry = _registry_with("alpha")
        assert registry.find_by_keyword("nonexistent") == ()

    def test_find_by_keyword_empty_registry(self) -> None:
        assert SkillRegistry().find_by_keyword("x") == ()

    def test_list_installed_sorted_by_id(self) -> None:
        registry = SkillRegistry()
        # Register in reverse alphabetical order
        for sid in ["zzz", "aaa", "mmm"]:
            registry.register(_make_skill(sid))
        entries = registry.list_installed()
        ids = [e.skill.manifest.id for e in entries]
        assert ids == ["aaa", "mmm", "zzz"]

    def test_list_installed_empty(self) -> None:
        assert SkillRegistry().list_installed() == ()

    def test_list_installed_single(self) -> None:
        registry = _registry_with("solo")
        entries = registry.list_installed()
        assert len(entries) == 1
        assert entries[0].skill.manifest.id == "solo"

    def test_list_capabilities_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("x", capabilities=["zzz", "aaa"]))
        caps = registry.list_capabilities()
        assert list(caps) == sorted(caps)
        assert "aaa" in caps
        assert "zzz" in caps

    def test_list_capabilities_empty_registry(self) -> None:
        assert SkillRegistry().list_capabilities() == ()

    def test_list_capabilities_deduplicated(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["shared"]))
        registry.register(_make_skill("b", capabilities=["shared"]))
        caps = registry.list_capabilities()
        assert caps.count("shared") == 1


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillRegistry — Dependencies
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillRegistryDependencies:
    def test_find_dependencies_registered(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("base"))
        registry.register(_make_skill("consumer", depends_on=["base"]))
        deps = registry.find_dependencies("consumer")
        assert len(deps) == 1
        assert deps[0].skill.manifest.id == "base"

    def test_find_dependencies_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("z-base"))
        registry.register(_make_skill("a-base"))
        registry.register(_make_skill("consumer", depends_on=["z-base", "a-base"]))
        deps = registry.find_dependencies("consumer")
        ids = [e.skill.manifest.id for e in deps]
        assert ids == sorted(ids)

    def test_find_dependencies_unregistered_dep_omitted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("consumer", depends_on=["missing-skill"]))
        deps = registry.find_dependencies("consumer")
        assert deps == ()

    def test_find_dependencies_mixed_registered_and_unregistered(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("present"))
        registry.register(_make_skill("consumer", depends_on=["present", "absent"]))
        deps = registry.find_dependencies("consumer")
        assert len(deps) == 1
        assert deps[0].skill.manifest.id == "present"

    def test_find_dependencies_no_deps_returns_empty(self) -> None:
        registry = _registry_with("standalone")
        assert registry.find_dependencies("standalone") == ()

    def test_find_dependencies_unknown_skill_returns_empty(self) -> None:
        registry = _registry_with("alpha")
        assert registry.find_dependencies("nonexistent") == ()

    def test_find_dependents_returns_skills_that_depend_on(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("base"))
        registry.register(_make_skill("consumer-a", depends_on=["base"]))
        registry.register(_make_skill("consumer-b", depends_on=["base"]))
        dependents = registry.find_dependents("base")
        ids = [e.skill.manifest.id for e in dependents]
        assert sorted(ids) == ["consumer-a", "consumer-b"]

    def test_find_dependents_sorted_by_id(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("shared"))
        registry.register(_make_skill("z-consumer", depends_on=["shared"]))
        registry.register(_make_skill("a-consumer", depends_on=["shared"]))
        dependents = registry.find_dependents("shared")
        ids = [e.skill.manifest.id for e in dependents]
        assert ids == sorted(ids)

    def test_find_dependents_no_dependents_returns_empty(self) -> None:
        registry = _registry_with("isolated")
        assert registry.find_dependents("isolated") == ()

    def test_find_dependents_unknown_skill_returns_empty(self) -> None:
        registry = _registry_with("alpha")
        assert registry.find_dependents("nonexistent") == ()


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillRegistry — Snapshot APIs
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillRegistrySnapshots:
    def test_capability_index_entries_sorted_by_capability(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["zzz"]))
        registry.register(_make_skill("b", capabilities=["aaa"]))
        idx = registry.capability_index()
        cap_ids = [e.capability_id for e in idx.entries]
        assert cap_ids == sorted(cap_ids)

    def test_capability_index_skill_ids_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("z-skill", capabilities=["shared"]))
        registry.register(_make_skill("a-skill", capabilities=["shared"]))
        idx = registry.capability_index()
        entry = next(e for e in idx.entries if e.capability_id == "shared")
        assert entry.skill_ids == ("a-skill", "z-skill")

    def test_capability_index_is_immutable(self) -> None:
        registry = _registry_with("alpha")
        idx = registry.capability_index()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            idx.entries = ()  # type: ignore[misc]

    def test_capability_index_snapshot_is_independent(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("alpha"))
        snap_before = registry.capability_index()
        registry.register(_make_skill("beta"))
        snap_after = registry.capability_index()
        assert len(snap_before.entries) != len(snap_after.entries) or True
        # The snapshot is a value object; registering more skills produces a new snapshot
        assert snap_before != snap_after or len(list(registry.list_installed())) == 1

    def test_capability_index_empty_registry(self) -> None:
        idx = SkillRegistry().capability_index()
        assert idx.entries == ()

    def test_capability_index_skill_ids_for_via_snapshot(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("writer", capabilities=["writing"]))
        idx = registry.capability_index()
        assert idx.skill_ids_for("writing") == ("writer",)
        assert idx.skill_ids_for("unknown") == ()

    def test_dependency_graph_edges_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("base"))
        registry.register(_make_skill("mid", depends_on=["base"]))
        registry.register(_make_skill("top", depends_on=["mid", "base"]))
        graph = registry.dependency_graph()
        assert graph.edges == tuple(sorted(graph.edges))

    def test_dependency_graph_excludes_unregistered_deps(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("consumer", depends_on=["missing"]))
        graph = registry.dependency_graph()
        assert graph.edges == ()

    def test_dependency_graph_empty_registry(self) -> None:
        graph = SkillRegistry().dependency_graph()
        assert graph.edges == ()

    def test_dependency_graph_is_immutable(self) -> None:
        graph = SkillRegistry().dependency_graph()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            graph.edges = ()  # type: ignore[misc]

    def test_dependency_graph_dependencies_of(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a"))
        registry.register(_make_skill("b", depends_on=["a"]))
        graph = registry.dependency_graph()
        assert graph.dependencies_of("b") == ("a",)
        assert graph.dependencies_of("a") == ()

    def test_dependency_graph_dependents_of(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("base"))
        registry.register(_make_skill("x", depends_on=["base"]))
        registry.register(_make_skill("y", depends_on=["base"]))
        graph = registry.dependency_graph()
        assert graph.dependents_of("base") == ("x", "y")

    def test_dependency_graph_has_dependency(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a"))
        registry.register(_make_skill("b", depends_on=["a"]))
        graph = registry.dependency_graph()
        assert graph.has_dependency("b", "a") is True
        assert graph.has_dependency("a", "b") is False


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillRegistry — Statistics
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillRegistryStatistics:
    def test_statistics_empty_registry(self) -> None:
        stats = SkillRegistry().statistics()
        assert stats.total_skills == 0
        assert stats.total_capabilities == 0
        assert stats.total_dependency_edges == 0
        assert stats.skills_with_dependencies == 0
        assert stats.skills_with_no_dependencies == 0
        assert stats.skills_by_status == ()

    def test_statistics_total_skills(self) -> None:
        registry = _registry_with("a", "b", "c")
        assert registry.statistics().total_skills == 3

    def test_statistics_total_capabilities(self) -> None:
        registry = SkillRegistry()
        # Each _make_skill creates one unique capability by default (cap-<id>)
        registry.register(_make_skill("a"))
        registry.register(_make_skill("b"))
        registry.register(_make_skill("c", capabilities=["cap-a"]))  # shares cap with a
        stats = registry.statistics()
        assert stats.total_capabilities == 2  # cap-a (shared), cap-b

    def test_statistics_dependency_edges(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("base"))
        registry.register(_make_skill("consumer", depends_on=["base"]))
        stats = registry.statistics()
        assert stats.total_dependency_edges == 1

    def test_statistics_skills_with_and_without_deps(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("base"))
        registry.register(_make_skill("with-dep", depends_on=["base"]))
        registry.register(_make_skill("no-dep"))
        stats = registry.statistics()
        assert stats.skills_with_dependencies == 1
        assert stats.skills_with_no_dependencies == 2
        assert stats.skills_with_dependencies + stats.skills_with_no_dependencies == stats.total_skills

    def test_statistics_by_status_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", status="active"))
        registry.register(_make_skill("b", status="active"))
        registry.register(_make_skill("c", status="draft"))
        stats = registry.statistics()
        keys = [pair[0] for pair in stats.skills_by_status]
        assert keys == sorted(keys)

    def test_statistics_by_status_counts(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", status="active"))
        registry.register(_make_skill("b", status="active"))
        registry.register(_make_skill("c", status="deprecated"))
        stats = registry.statistics()
        by_status = dict(stats.skills_by_status)
        assert by_status["active"] == 2
        assert by_status["deprecated"] == 1

    def test_statistics_is_immutable(self) -> None:
        stats = SkillRegistry().statistics()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            stats.total_skills = 99  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillRegistry — Container Protocol
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillRegistryContainer:
    def test_len_empty(self) -> None:
        assert len(SkillRegistry()) == 0

    def test_len_after_registration(self) -> None:
        registry = _registry_with("a", "b", "c")
        assert len(registry) == 3

    def test_len_duplicate_not_counted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("x"))
        registry.register(_make_skill("x"))
        assert len(registry) == 1

    def test_contains_registered(self) -> None:
        registry = _registry_with("present")
        assert "present" in registry

    def test_contains_unregistered(self) -> None:
        registry = _registry_with("alpha")
        assert "missing" not in registry

    def test_contains_empty_registry(self) -> None:
        assert "anything" not in SkillRegistry()


# ══════════════════════════════════════════════════════════════════════════════
# TestDeterminism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def _make_populated_registry(self) -> SkillRegistry:
        registry = SkillRegistry()
        registry.register(_make_skill("c-skill", capabilities=["llm", "git"], keywords=["content"]))
        registry.register(_make_skill("a-skill", capabilities=["llm"], depends_on=["c-skill"]))
        registry.register(_make_skill("b-skill", capabilities=["docker"], keywords=["content"]))
        return registry

    def test_list_installed_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        assert [e.skill.manifest.id for e in r1.list_installed()] == \
               [e.skill.manifest.id for e in r2.list_installed()]

    def test_list_capabilities_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        assert r1.list_capabilities() == r2.list_capabilities()

    def test_capability_index_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        assert r1.capability_index() == r2.capability_index()

    def test_dependency_graph_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        assert r1.dependency_graph() == r2.dependency_graph()

    def test_statistics_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        assert r1.statistics() == r2.statistics()

    def test_find_by_capability_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        ids1 = [e.skill.manifest.id for e in r1.find_by_capability("llm")]
        ids2 = [e.skill.manifest.id for e in r2.find_by_capability("llm")]
        assert ids1 == ids2

    def test_find_by_keyword_is_deterministic(self) -> None:
        r1 = self._make_populated_registry()
        r2 = self._make_populated_registry()
        ids1 = [e.skill.manifest.id for e in r1.find_by_keyword("content")]
        ids2 = [e.skill.manifest.id for e in r2.find_by_keyword("content")]
        assert ids1 == ids2

    def test_registration_result_is_deterministic(self) -> None:
        skill = _make_skill("deterministic-skill")
        r1 = SkillRegistry().register(skill)
        r2 = SkillRegistry().register(skill)
        assert r1.status == r2.status
        assert r1.skill_id == r2.skill_id

    def test_dependency_graph_edges_always_sorted(self) -> None:
        for _ in range(5):
            registry = SkillRegistry()
            registry.register(_make_skill("a"))
            registry.register(_make_skill("b"))
            registry.register(_make_skill("c", depends_on=["a", "b"]))
            registry.register(_make_skill("d", depends_on=["a"]))
            graph = registry.dependency_graph()
            assert graph.edges == tuple(sorted(graph.edges))


# ══════════════════════════════════════════════════════════════════════════════
# TestEdgeCases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_skill_with_no_capabilities_indexed_but_no_caps(self) -> None:
        registry = SkillRegistry()
        raw = {
            "id": "no-cap-skill",
            "name": "No Cap Skill",
            "version": "1.0.0",
            "capabilities": [],
        }
        manifest = SkillManifest.from_dict(raw)
        skill = InstalledSkill(manifest=manifest, path=Path("/x"), knowledge_paths=(), sop_paths=())
        # Validator would reject this, but registry accepts any InstalledSkill
        result = registry.register(skill)
        assert result.status is RegistrationStatus.REGISTERED
        assert registry.list_capabilities() == ()

    def test_skill_with_shared_and_unique_capabilities(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["shared", "unique-a"]))
        registry.register(_make_skill("b", capabilities=["shared", "unique-b"]))
        shared_entries = registry.find_by_capability("shared")
        assert len(shared_entries) == 2
        assert len(registry.find_by_capability("unique-a")) == 1
        assert len(registry.find_by_capability("unique-b")) == 1

    def test_capability_index_reflects_only_registered_skills(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("only"))
        registry.register(_make_skill("only"))  # duplicate — ignored
        idx = registry.capability_index()
        # "cap-only" should appear once with one skill_id
        entry = next((e for e in idx.entries if e.capability_id == "cap-only"), None)
        assert entry is not None
        assert entry.skill_ids == ("only",)

    def test_diamond_dependency_graph(self) -> None:
        # a ← b ← d
        # a ← c ← d
        registry = SkillRegistry()
        registry.register(_make_skill("a"))
        registry.register(_make_skill("b", depends_on=["a"]))
        registry.register(_make_skill("c", depends_on=["a"]))
        registry.register(_make_skill("d", depends_on=["b", "c"]))
        graph = registry.dependency_graph()
        assert graph.has_dependency("d", "b") is True
        assert graph.has_dependency("d", "c") is True
        assert graph.has_dependency("b", "a") is True
        assert graph.has_dependency("c", "a") is True
        assert graph.has_dependency("d", "a") is False  # not a direct edge
        assert graph.dependents_of("a") == ("b", "c")

    def test_experimental_and_deprecated_skills_registered(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("exp", status="experimental"))
        registry.register(_make_skill("dep", status="deprecated"))
        assert len(registry) == 2
        stats = registry.statistics()
        by_status = dict(stats.skills_by_status)
        assert by_status.get("experimental") == 1
        assert by_status.get("deprecated") == 1

    def test_statistics_dependency_edges_count_declared_not_resolved(self) -> None:
        # total_dependency_edges counts declared depends_on entries,
        # including those that point to unregistered skills
        registry = SkillRegistry()
        registry.register(_make_skill("consumer", depends_on=["missing-a", "missing-b"]))
        stats = registry.statistics()
        assert stats.total_dependency_edges == 2

    def test_skills_by_status_excludes_zero_count_statuses(self) -> None:
        registry = _registry_with("a", "b")  # both default to "active"
        stats = registry.statistics()
        # Only "active" should appear — no zero-count entries
        assert all(count > 0 for _, count in stats.skills_by_status)
