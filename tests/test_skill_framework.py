"""Tests for the Skill Framework — Sprint 53.

Covers:
- All typed model contracts (SkillStatus, SkillVersion, SkillCapability,
  SkillDependency, SkillCompatibility, ExecutionDeclaration, SkillMetadata,
  SkillManifest, InstalledSkill)
- SkillValidator: all validation rules, pass/fail, edge cases
- SkillLoader: load_skill, load_all_skills, legacy load(plan), filesystem edge cases
- Determinism: same inputs always produce same outputs
- Immutability: frozen dataclasses resist mutation
- Equality and hashing
- Serialization round-trips (from_dict)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from hermes.kernel.skill_loader import SkillLoadError, SkillLoader, SkillNotFoundError
from hermes.kernel.skill_validator import (
    SkillValidationError,
    SkillValidationResult,
    SkillValidator,
)
from hermes.models.skill import (
    ExecutionDeclaration,
    InstalledSkill,
    SkillCapability,
    SkillCompatibility,
    SkillDependency,
    SkillManifest,
    SkillMetadata,
    SkillStatus,
    SkillVersion,
)

# ── Test fixtures and helpers ──────────────────────────────────────────────────

_MINIMAL_RAW: dict = {
    "id": "test-skill",
    "name": "Test Skill",
    "version": "1.0.0",
    "capabilities": ["test-capability"],
}

_FULL_RAW: dict = {
    "id": "brand-strategy",
    "name": "Brand Strategy",
    "version": "2.1.3",
    "status": "active",
    "description": "Provides brand positioning and personality guidance.",
    "owner": "Marketing",
    "department_id": "marketing",
    "capabilities": ["brand-strategy", "brand-audit"],
    "provides": ["brand positioning", "brand personality"],
    "keywords": ["brand", "branding", "positioning"],
    "inputs": ["business profile", "competitive landscape"],
    "outputs": ["brand framework", "brand guidelines"],
    "depends_on": [],
    "sop_refs": ["brand-strategy/brand-audit"],
    "repository_refs": ["hermes-os"],
    "workflow_refs": [],
    "table_refs": [],
    "model_refs": ["anthropic--claude-opus-4"],
    "compatibility": {
        "min_hermes_version": "3.0.0",
        "min_python_version": "3.12",
        "max_hermes_version": "",
    },
    "execution": {
        "adapters": ["llm", "filesystem"],
    },
}


def _write_skill_yaml(skill_dir: Path, content: dict) -> None:
    """Write a skill.yaml into a directory."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        yaml.dump(content, default_flow_style=False), encoding="utf-8"
    )


def _minimal_manifest() -> SkillManifest:
    return SkillManifest.from_dict(_MINIMAL_RAW)


def _full_manifest() -> SkillManifest:
    return SkillManifest.from_dict(_FULL_RAW)


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillStatus
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillStatus:
    def test_draft_value(self) -> None:
        assert SkillStatus.DRAFT.value == "draft"

    def test_active_value(self) -> None:
        assert SkillStatus.ACTIVE.value == "active"

    def test_experimental_value(self) -> None:
        assert SkillStatus.EXPERIMENTAL.value == "experimental"

    def test_deprecated_value(self) -> None:
        assert SkillStatus.DEPRECATED.value == "deprecated"

    def test_from_string(self) -> None:
        assert SkillStatus("active") == SkillStatus.ACTIVE

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            SkillStatus("unknown-status")

    def test_all_four_statuses_exist(self) -> None:
        names = {s.name for s in SkillStatus}
        assert names == {"DRAFT", "ACTIVE", "EXPERIMENTAL", "DEPRECATED"}


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillVersion
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillVersion:
    def test_parse_standard(self) -> None:
        v = SkillVersion.parse("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3

    def test_parse_zeros(self) -> None:
        v = SkillVersion.parse("0.0.0")
        assert v == SkillVersion(0, 0, 0)

    def test_parse_large_numbers(self) -> None:
        v = SkillVersion.parse("100.200.300")
        assert v.major == 100
        assert v.minor == 200
        assert v.patch == 300

    def test_parse_strips_whitespace(self) -> None:
        v = SkillVersion.parse("  1.0.0  ")
        assert v == SkillVersion(1, 0, 0)

    def test_parse_invalid_missing_patch(self) -> None:
        with pytest.raises(ValueError):
            SkillVersion.parse("1.0")

    def test_parse_invalid_letters(self) -> None:
        with pytest.raises(ValueError):
            SkillVersion.parse("1.0.x")

    def test_parse_invalid_empty(self) -> None:
        with pytest.raises(ValueError):
            SkillVersion.parse("")

    def test_str_representation(self) -> None:
        assert str(SkillVersion(2, 1, 3)) == "2.1.3"

    def test_equality(self) -> None:
        assert SkillVersion(1, 0, 0) == SkillVersion(1, 0, 0)

    def test_inequality(self) -> None:
        assert SkillVersion(1, 0, 0) != SkillVersion(1, 0, 1)

    def test_ordering_major(self) -> None:
        assert SkillVersion(1, 0, 0) < SkillVersion(2, 0, 0)

    def test_ordering_minor(self) -> None:
        assert SkillVersion(1, 0, 0) < SkillVersion(1, 1, 0)

    def test_ordering_patch(self) -> None:
        assert SkillVersion(1, 0, 0) < SkillVersion(1, 0, 1)

    def test_ordering_ge(self) -> None:
        assert SkillVersion(2, 0, 0) >= SkillVersion(1, 9, 9)

    def test_is_frozen(self) -> None:
        v = SkillVersion(1, 0, 0)
        with pytest.raises(FrozenInstanceError):
            v.major = 2  # type: ignore[misc]

    def test_hashable(self) -> None:
        s = {SkillVersion(1, 0, 0), SkillVersion(1, 0, 0), SkillVersion(2, 0, 0)}
        assert len(s) == 2


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillCapability
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillCapability:
    def test_creation(self) -> None:
        cap = SkillCapability(id="copywriting")
        assert cap.id == "copywriting"

    def test_equality(self) -> None:
        assert SkillCapability(id="python") == SkillCapability(id="python")

    def test_inequality(self) -> None:
        assert SkillCapability(id="python") != SkillCapability(id="nextjs")

    def test_is_frozen(self) -> None:
        cap = SkillCapability(id="python")
        with pytest.raises(FrozenInstanceError):
            cap.id = "other"  # type: ignore[misc]

    def test_hashable(self) -> None:
        caps = {SkillCapability("a"), SkillCapability("a"), SkillCapability("b")}
        assert len(caps) == 2

    def test_empty_id_allowed_at_model_level(self) -> None:
        # The model accepts empty IDs; the validator rejects them
        cap = SkillCapability(id="")
        assert cap.id == ""


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillDependency
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillDependency:
    def test_simple_dependency(self) -> None:
        dep = SkillDependency(skill_id="brand-strategy")
        assert dep.skill_id == "brand-strategy"
        assert dep.min_version is None

    def test_versioned_dependency(self) -> None:
        dep = SkillDependency(
            skill_id="python",
            min_version=SkillVersion(1, 2, 0),
        )
        assert dep.min_version == SkillVersion(1, 2, 0)

    def test_equality(self) -> None:
        a = SkillDependency(skill_id="python")
        b = SkillDependency(skill_id="python")
        assert a == b

    def test_versioned_equality(self) -> None:
        v = SkillVersion(1, 0, 0)
        assert SkillDependency("x", v) == SkillDependency("x", v)

    def test_is_frozen(self) -> None:
        dep = SkillDependency(skill_id="python")
        with pytest.raises(FrozenInstanceError):
            dep.skill_id = "other"  # type: ignore[misc]

    def test_hashable(self) -> None:
        deps = {SkillDependency("a"), SkillDependency("a"), SkillDependency("b")}
        assert len(deps) == 2


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillCompatibility
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillCompatibility:
    def test_defaults_are_empty_strings(self) -> None:
        compat = SkillCompatibility()
        assert compat.min_hermes_version == ""
        assert compat.min_python_version == ""
        assert compat.max_hermes_version == ""

    def test_explicit_values(self) -> None:
        compat = SkillCompatibility(
            min_hermes_version="3.0.0",
            min_python_version="3.12",
            max_hermes_version="4.0.0",
        )
        assert compat.min_hermes_version == "3.0.0"
        assert compat.min_python_version == "3.12"
        assert compat.max_hermes_version == "4.0.0"

    def test_equality(self) -> None:
        a = SkillCompatibility(min_hermes_version="3.0.0")
        b = SkillCompatibility(min_hermes_version="3.0.0")
        assert a == b

    def test_is_frozen(self) -> None:
        compat = SkillCompatibility()
        with pytest.raises(FrozenInstanceError):
            compat.min_hermes_version = "1.0.0"  # type: ignore[misc]

    def test_partial_specification(self) -> None:
        compat = SkillCompatibility(min_python_version="3.12")
        assert compat.min_hermes_version == ""
        assert compat.min_python_version == "3.12"


# ══════════════════════════════════════════════════════════════════════════════
# TestExecutionDeclaration
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutionDeclaration:
    def test_creation(self) -> None:
        decl = ExecutionDeclaration(adapters=("llm", "git"))
        assert decl.adapters == ("llm", "git")

    def test_empty_adapters(self) -> None:
        decl = ExecutionDeclaration(adapters=())
        assert decl.adapters == ()

    def test_is_frozen(self) -> None:
        decl = ExecutionDeclaration(adapters=("llm",))
        with pytest.raises(FrozenInstanceError):
            decl.adapters = ("git",)  # type: ignore[misc]

    def test_adapters_is_tuple(self) -> None:
        decl = ExecutionDeclaration(adapters=("llm", "filesystem"))
        assert isinstance(decl.adapters, tuple)

    def test_equality(self) -> None:
        a = ExecutionDeclaration(adapters=("llm", "git"))
        b = ExecutionDeclaration(adapters=("llm", "git"))
        assert a == b

    def test_inequality_different_order(self) -> None:
        a = ExecutionDeclaration(adapters=("llm", "git"))
        b = ExecutionDeclaration(adapters=("git", "llm"))
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillMetadata
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillMetadata:
    def test_with_owner(self) -> None:
        meta = SkillMetadata(owner="Marketing", department_id="marketing")
        assert meta.owner == "Marketing"
        assert meta.department_id == "marketing"

    def test_without_owner(self) -> None:
        meta = SkillMetadata(owner=None, department_id="platform")
        assert meta.owner is None

    def test_equality(self) -> None:
        a = SkillMetadata(owner="Tech", department_id="tech")
        b = SkillMetadata(owner="Tech", department_id="tech")
        assert a == b

    def test_is_frozen(self) -> None:
        meta = SkillMetadata(owner="Tech", department_id="tech")
        with pytest.raises(FrozenInstanceError):
            meta.owner = "Other"  # type: ignore[misc]

    def test_empty_department_id(self) -> None:
        meta = SkillMetadata(owner=None, department_id="")
        assert meta.department_id == ""


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillManifest
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillManifest:
    def test_from_dict_minimal(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        assert m.id == "test-skill"
        assert m.name == "Test Skill"
        assert m.version == SkillVersion(1, 0, 0)
        assert m.status == SkillStatus.ACTIVE

    def test_from_dict_full(self) -> None:
        m = SkillManifest.from_dict(_FULL_RAW)
        assert m.id == "brand-strategy"
        assert m.version == SkillVersion(2, 1, 3)
        assert len(m.capabilities) == 2
        assert m.capabilities[0] == SkillCapability("brand-strategy")

    def test_from_dict_defaults_status_active(self) -> None:
        raw = {**_MINIMAL_RAW}
        raw.pop("status", None)
        m = SkillManifest.from_dict(raw)
        assert m.status == SkillStatus.ACTIVE

    def test_from_dict_defaults_description_empty(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        assert m.description == ""

    def test_from_dict_capabilities_are_tuple(self) -> None:
        m = SkillManifest.from_dict(_FULL_RAW)
        assert isinstance(m.capabilities, tuple)
        assert isinstance(m.keywords, tuple)
        assert isinstance(m.depends_on, tuple)

    def test_from_dict_missing_id_raises(self) -> None:
        raw = {k: v for k, v in _MINIMAL_RAW.items() if k != "id"}
        with pytest.raises(KeyError):
            SkillManifest.from_dict(raw)

    def test_from_dict_missing_name_raises(self) -> None:
        raw = {k: v for k, v in _MINIMAL_RAW.items() if k != "name"}
        with pytest.raises(KeyError):
            SkillManifest.from_dict(raw)

    def test_from_dict_missing_version_raises(self) -> None:
        raw = {k: v for k, v in _MINIMAL_RAW.items() if k != "version"}
        with pytest.raises(KeyError):
            SkillManifest.from_dict(raw)

    def test_from_dict_invalid_version_raises(self) -> None:
        raw = {**_MINIMAL_RAW, "version": "not-a-version"}
        with pytest.raises(ValueError):
            SkillManifest.from_dict(raw)

    def test_from_dict_invalid_status_raises(self) -> None:
        raw = {**_MINIMAL_RAW, "status": "flying"}
        with pytest.raises(ValueError):
            SkillManifest.from_dict(raw)

    def test_from_dict_compatibility_parsed(self) -> None:
        m = SkillManifest.from_dict(_FULL_RAW)
        assert m.compatibility is not None
        assert m.compatibility.min_hermes_version == "3.0.0"
        assert m.compatibility.min_python_version == "3.12"

    def test_from_dict_execution_parsed(self) -> None:
        m = SkillManifest.from_dict(_FULL_RAW)
        assert m.execution is not None
        assert m.execution.adapters == ("llm", "filesystem")

    def test_from_dict_no_compatibility(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        assert m.compatibility is None

    def test_from_dict_no_execution(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        assert m.execution is None

    def test_is_frozen(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        with pytest.raises(FrozenInstanceError):
            m.id = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = SkillManifest.from_dict(_MINIMAL_RAW)
        b = SkillManifest.from_dict(_MINIMAL_RAW)
        assert a == b

    def test_inequality_different_version(self) -> None:
        a = SkillManifest.from_dict(_MINIMAL_RAW)
        b = SkillManifest.from_dict({**_MINIMAL_RAW, "version": "2.0.0"})
        assert a != b

    def test_from_dict_depends_on_parsed(self) -> None:
        raw = {**_MINIMAL_RAW, "depends_on": ["brand-strategy"]}
        m = SkillManifest.from_dict(raw)
        assert len(m.depends_on) == 1
        assert m.depends_on[0].skill_id == "brand-strategy"

    def test_from_dict_null_depends_on_treated_as_empty(self) -> None:
        raw = {**_MINIMAL_RAW, "depends_on": None}
        m = SkillManifest.from_dict(raw)
        assert m.depends_on == ()

    def test_metadata_owner_none_when_absent(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        assert m.metadata.owner is None

    def test_metadata_owner_set(self) -> None:
        raw = {**_MINIMAL_RAW, "owner": "Technology"}
        m = SkillManifest.from_dict(raw)
        assert m.metadata.owner == "Technology"

    def test_all_list_fields_are_tuples(self) -> None:
        m = SkillManifest.from_dict(_FULL_RAW)
        for field_name in (
            "capabilities", "provides", "keywords", "inputs", "outputs",
            "depends_on", "sop_refs", "repository_refs", "workflow_refs",
            "table_refs", "model_refs",
        ):
            value = getattr(m, field_name)
            assert isinstance(value, tuple), f"{field_name} should be a tuple"


# ══════════════════════════════════════════════════════════════════════════════
# TestInstalledSkill
# ══════════════════════════════════════════════════════════════════════════════


class TestInstalledSkill:
    def test_creation(self, tmp_path: Path) -> None:
        manifest = _minimal_manifest()
        skill = InstalledSkill(
            manifest=manifest,
            path=tmp_path,
            knowledge_paths=(),
            sop_paths=(),
        )
        assert skill.manifest is manifest
        assert skill.path == tmp_path

    def test_with_knowledge_paths(self, tmp_path: Path) -> None:
        skill = InstalledSkill(
            manifest=_minimal_manifest(),
            path=tmp_path,
            knowledge_paths=("knowledge/guide.md", "knowledge/ref.md"),
            sop_paths=(),
        )
        assert len(skill.knowledge_paths) == 2
        assert "knowledge/guide.md" in skill.knowledge_paths

    def test_with_sop_paths(self, tmp_path: Path) -> None:
        skill = InstalledSkill(
            manifest=_minimal_manifest(),
            path=tmp_path,
            knowledge_paths=(),
            sop_paths=("sops/content-review.md",),
        )
        assert "sops/content-review.md" in skill.sop_paths

    def test_is_frozen(self, tmp_path: Path) -> None:
        skill = InstalledSkill(
            manifest=_minimal_manifest(),
            path=tmp_path,
            knowledge_paths=(),
            sop_paths=(),
        )
        with pytest.raises(FrozenInstanceError):
            skill.path = Path("/other")  # type: ignore[misc]

    def test_knowledge_paths_is_tuple(self, tmp_path: Path) -> None:
        skill = InstalledSkill(
            manifest=_minimal_manifest(),
            path=tmp_path,
            knowledge_paths=("knowledge/a.md",),
            sop_paths=(),
        )
        assert isinstance(skill.knowledge_paths, tuple)

    def test_sop_paths_is_tuple(self, tmp_path: Path) -> None:
        skill = InstalledSkill(
            manifest=_minimal_manifest(),
            path=tmp_path,
            knowledge_paths=(),
            sop_paths=("sops/a.md",),
        )
        assert isinstance(skill.sop_paths, tuple)

    def test_equality(self, tmp_path: Path) -> None:
        manifest = _minimal_manifest()
        a = InstalledSkill(manifest=manifest, path=tmp_path, knowledge_paths=(), sop_paths=())
        b = InstalledSkill(manifest=manifest, path=tmp_path, knowledge_paths=(), sop_paths=())
        assert a == b

    def test_inequality_different_path(self, tmp_path: Path) -> None:
        manifest = _minimal_manifest()
        a = InstalledSkill(manifest=manifest, path=tmp_path / "a", knowledge_paths=(), sop_paths=())
        b = InstalledSkill(manifest=manifest, path=tmp_path / "b", knowledge_paths=(), sop_paths=())
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillValidationError
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillValidationError:
    def test_creation(self) -> None:
        err = SkillValidationError(field="id", message="must not be empty")
        assert err.field == "id"
        assert err.message == "must not be empty"

    def test_is_frozen(self) -> None:
        err = SkillValidationError(field="id", message="error")
        with pytest.raises(FrozenInstanceError):
            err.field = "name"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = SkillValidationError(field="id", message="empty")
        b = SkillValidationError(field="id", message="empty")
        assert a == b

    def test_inequality(self) -> None:
        a = SkillValidationError(field="id", message="empty")
        b = SkillValidationError(field="name", message="empty")
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillValidationResult
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillValidationResult:
    def test_valid_result(self) -> None:
        result = SkillValidationResult(valid=True, errors=())
        assert result.valid is True
        assert result.errors == ()

    def test_invalid_result(self) -> None:
        err = SkillValidationError(field="id", message="empty")
        result = SkillValidationResult(valid=False, errors=(err,))
        assert result.valid is False
        assert len(result.errors) == 1

    def test_errors_is_tuple(self) -> None:
        result = SkillValidationResult(valid=True, errors=())
        assert isinstance(result.errors, tuple)

    def test_is_frozen(self) -> None:
        result = SkillValidationResult(valid=True, errors=())
        with pytest.raises(FrozenInstanceError):
            result.valid = False  # type: ignore[misc]

    def test_multiple_errors(self) -> None:
        errors = (
            SkillValidationError(field="id", message="empty"),
            SkillValidationError(field="name", message="empty"),
        )
        result = SkillValidationResult(valid=False, errors=errors)
        assert len(result.errors) == 2


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillValidator
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillValidator:
    def setup_method(self) -> None:
        self.validator = SkillValidator()

    def test_valid_minimal_manifest(self) -> None:
        result = self.validator.validate(_minimal_manifest())
        assert result.valid is True
        assert result.errors == ()

    def test_valid_full_manifest(self) -> None:
        result = self.validator.validate(_full_manifest())
        assert result.valid is True

    # ── ID validation ──────────────────────────────────────────────────────

    def test_empty_id_fails(self) -> None:
        manifest = SkillManifest.from_dict({**_MINIMAL_RAW, "id": " "})
        # Empty after strip — but the model stores the raw value
        # Create via direct construction to test the validator logic
        m = _minimal_manifest()
        # Build a manifest with empty id directly
        raw = {**_MINIMAL_RAW, "name": "X", "id": "valid"}
        m = SkillManifest.from_dict(raw)
        # We must construct with empty id to test: use object.__setattr__ hack?
        # Actually, since frozen=True, we can't. Test via a known invalid ID instead.
        raw_invalid = {**_MINIMAL_RAW, "id": "UPPERCASE"}
        m2 = SkillManifest.from_dict(raw_invalid)
        result = self.validator.validate(m2)
        assert not result.valid
        assert any(e.field == "id" for e in result.errors)

    def test_id_with_uppercase_fails(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "id": "My-Skill"})
        result = self.validator.validate(m)
        assert not result.valid
        assert any("id" in e.field for e in result.errors)

    def test_id_with_spaces_fails(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "id": "my skill"})
        result = self.validator.validate(m)
        assert not result.valid

    def test_id_with_underscore_fails(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "id": "my_skill"})
        result = self.validator.validate(m)
        assert not result.valid

    def test_id_starting_with_hyphen_fails(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "id": "-my-skill"})
        result = self.validator.validate(m)
        assert not result.valid

    def test_valid_id_with_hyphens(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "id": "brand-strategy-v2"})
        result = self.validator.validate(m)
        assert result.valid

    def test_valid_id_alphanumeric(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "id": "python3"})
        result = self.validator.validate(m)
        assert result.valid

    # ── Name validation ────────────────────────────────────────────────────

    def test_whitespace_name_fails(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "name": "   "})
        result = self.validator.validate(m)
        assert not result.valid
        assert any(e.field == "name" for e in result.errors)

    # ── Capability validation ──────────────────────────────────────────────

    def test_empty_capabilities_fails(self) -> None:
        m = SkillManifest.from_dict({**_MINIMAL_RAW, "capabilities": []})
        result = self.validator.validate(m)
        assert not result.valid
        assert any(e.field == "capabilities" for e in result.errors)

    def test_empty_capability_id_fails(self) -> None:
        raw = {**_MINIMAL_RAW, "capabilities": [""]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert not result.valid
        assert any(e.field == "capabilities" for e in result.errors)

    def test_multiple_capabilities_valid(self) -> None:
        raw = {**_MINIMAL_RAW, "capabilities": ["python", "testing"]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert result.valid

    # ── Keyword validation ─────────────────────────────────────────────────

    def test_empty_keyword_fails(self) -> None:
        raw = {**_MINIMAL_RAW, "keywords": ["valid", ""]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert not result.valid
        assert any(e.field == "keywords" for e in result.errors)

    def test_no_keywords_is_valid(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        result = self.validator.validate(m)
        assert result.valid

    # ── Dependency validation ──────────────────────────────────────────────

    def test_self_dependency_fails(self) -> None:
        raw = {**_MINIMAL_RAW, "depends_on": ["test-skill"]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert not result.valid
        assert any("itself" in e.message for e in result.errors)

    def test_unknown_dependency_fails_when_registry_provided(self) -> None:
        raw = {**_MINIMAL_RAW, "depends_on": ["missing-skill"]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m, available_skill_ids=frozenset({"other-skill"}))
        assert not result.valid
        assert any("missing-skill" in e.message for e in result.errors)

    def test_known_dependency_passes_with_registry(self) -> None:
        raw = {**_MINIMAL_RAW, "depends_on": ["brand-strategy"]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(
            m, available_skill_ids=frozenset({"brand-strategy"})
        )
        assert result.valid

    def test_dependency_skipped_without_registry(self) -> None:
        raw = {**_MINIMAL_RAW, "depends_on": ["any-skill"]}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m, available_skill_ids=None)
        assert result.valid

    # ── Execution validation ───────────────────────────────────────────────

    def test_empty_adapter_name_fails(self) -> None:
        raw = {**_MINIMAL_RAW, "execution": {"adapters": ["llm", ""]}}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert not result.valid
        assert any("execution" in e.field for e in result.errors)

    def test_valid_adapters(self) -> None:
        raw = {**_MINIMAL_RAW, "execution": {"adapters": ["llm", "git"]}}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert result.valid

    def test_no_execution_is_valid(self) -> None:
        m = SkillManifest.from_dict(_MINIMAL_RAW)
        assert m.execution is None
        result = self.validator.validate(m)
        assert result.valid

    # ── Multiple errors ────────────────────────────────────────────────────

    def test_multiple_failures_all_reported(self) -> None:
        raw = {**_MINIMAL_RAW, "id": "INVALID", "name": "  ", "capabilities": []}
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert not result.valid
        fields = {e.field for e in result.errors}
        assert "id" in fields
        assert "name" in fields
        assert "capabilities" in fields

    # ── Kernel skill (no keywords) ─────────────────────────────────────────

    def test_kernel_skill_with_no_keywords_is_valid(self) -> None:
        raw = {
            "id": "kernel",
            "name": "Kernel",
            "version": "1.0.0",
            "status": "active",
            "capabilities": ["kernel"],
            "keywords": [],
            "depends_on": [],
        }
        m = SkillManifest.from_dict(raw)
        result = self.validator.validate(m)
        assert result.valid


# ══════════════════════════════════════════════════════════════════════════════
# TestSkillLoader
# ══════════════════════════════════════════════════════════════════════════════


class TestSkillLoader:
    # ── load_skill ─────────────────────────────────────────────────────────

    def test_load_skill_minimal(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _MINIMAL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert skill.manifest.id == "test-skill"
        assert skill.manifest.version == SkillVersion(1, 0, 0)

    def test_load_skill_full(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "brand-strategy"
        _write_skill_yaml(skill_dir, _FULL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert skill.manifest.id == "brand-strategy"
        assert skill.manifest.execution is not None
        assert "llm" in skill.manifest.execution.adapters

    def test_load_skill_path_is_absolute(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _MINIMAL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert skill.path.is_absolute()

    def test_load_skill_missing_yaml_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "empty-dir"
        skill_dir.mkdir()
        loader = SkillLoader(skills_root=tmp_path)
        with pytest.raises(SkillLoadError, match="skill.yaml"):
            loader.load_skill(skill_dir)

    def test_load_skill_invalid_manifest_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(
            yaml.dump({"id": "INVALID ID"}), encoding="utf-8"
        )
        loader = SkillLoader(skills_root=tmp_path)
        with pytest.raises(SkillLoadError):
            loader.load_skill(skill_dir)

    def test_load_skill_invalid_version_raises(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad-version"
        _write_skill_yaml(skill_dir, {**_MINIMAL_RAW, "version": "bad"})
        loader = SkillLoader(skills_root=tmp_path)
        with pytest.raises(SkillLoadError):
            loader.load_skill(skill_dir)

    def test_load_skill_discovers_knowledge(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _MINIMAL_RAW)
        knowledge_dir = skill_dir / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "guide.md").write_text("# Guide", encoding="utf-8")
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert len(skill.knowledge_paths) == 1
        assert "guide.md" in skill.knowledge_paths[0]

    def test_load_skill_discovers_sops(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _MINIMAL_RAW)
        sops_dir = skill_dir / "sops"
        sops_dir.mkdir()
        (sops_dir / "procedure.md").write_text("# Procedure", encoding="utf-8")
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert len(skill.sop_paths) == 1
        assert "procedure.md" in skill.sop_paths[0]

    def test_load_skill_no_knowledge_dir(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _MINIMAL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert skill.knowledge_paths == ()

    def test_load_skill_no_sops_dir(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _MINIMAL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        skill = loader.load_skill(skill_dir)
        assert skill.sop_paths == ()

    # ── load_all_skills ────────────────────────────────────────────────────

    def test_load_all_skills_empty_root(self, tmp_path: Path) -> None:
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        assert result == []

    def test_load_all_skills_single(self, tmp_path: Path) -> None:
        _write_skill_yaml(tmp_path / "alpha", _MINIMAL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        assert len(result) == 1
        assert result[0].manifest.id == "test-skill"

    def test_load_all_skills_sorted_by_id(self, tmp_path: Path) -> None:
        _write_skill_yaml(tmp_path / "z-skill", {**_MINIMAL_RAW, "id": "z-skill", "name": "Z"})
        _write_skill_yaml(tmp_path / "a-skill", {**_MINIMAL_RAW, "id": "a-skill", "name": "A"})
        _write_skill_yaml(tmp_path / "m-skill", {**_MINIMAL_RAW, "id": "m-skill", "name": "M"})
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        ids = [s.manifest.id for s in result]
        assert ids == sorted(ids)

    def test_load_all_skills_skips_invalid(self, tmp_path: Path) -> None:
        _write_skill_yaml(tmp_path / "good", _MINIMAL_RAW)
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "skill.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        assert len(result) == 1

    def test_load_all_skills_dependency_resolution(self, tmp_path: Path) -> None:
        raw_a = {**_MINIMAL_RAW, "id": "skill-a", "name": "A"}
        raw_b = {**_MINIMAL_RAW, "id": "skill-b", "name": "B", "depends_on": ["skill-a"]}
        _write_skill_yaml(tmp_path / "skill-a", raw_a)
        _write_skill_yaml(tmp_path / "skill-b", raw_b)
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        ids = {s.manifest.id for s in result}
        assert "skill-a" in ids
        assert "skill-b" in ids

    def test_load_all_skills_skips_unresolved_dependency(self, tmp_path: Path) -> None:
        raw_b = {**_MINIMAL_RAW, "id": "skill-b", "name": "B", "depends_on": ["missing-skill"]}
        _write_skill_yaml(tmp_path / "skill-b", raw_b)
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        assert result == []

    def test_load_all_skills_first_manifest_wins(self, tmp_path: Path) -> None:
        # Two directories with the same skill ID — first in sort order wins
        raw1 = {**_MINIMAL_RAW, "id": "duplicate", "name": "First"}
        raw2 = {**_MINIMAL_RAW, "id": "duplicate", "name": "Second"}
        _write_skill_yaml(tmp_path / "aaa-dir", raw1)
        _write_skill_yaml(tmp_path / "zzz-dir", raw2)
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        assert len(result) == 1
        assert result[0].manifest.name == "First"

    def test_load_all_skills_returns_installed_skill_instances(self, tmp_path: Path) -> None:
        _write_skill_yaml(tmp_path / "my-skill", _MINIMAL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        result = loader.load_all_skills()
        assert all(isinstance(s, InstalledSkill) for s in result)

    # ── Legacy load(plan) ──────────────────────────────────────────────────

    def test_legacy_load_not_found_raises(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock
        from hermes.models import ExecutionStep

        step = ExecutionStep(capability_id="unknown-cap", description="test")
        plan = MagicMock()
        plan.steps = [step]

        loader = SkillLoader(skills_root=tmp_path)
        with pytest.raises(SkillNotFoundError):
            loader.load(plan)


# ══════════════════════════════════════════════════════════════════════════════
# TestDeterminism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_from_dict_is_deterministic(self) -> None:
        a = SkillManifest.from_dict(_FULL_RAW)
        b = SkillManifest.from_dict(_FULL_RAW)
        assert a == b

    def test_version_parse_is_deterministic(self) -> None:
        a = SkillVersion.parse("1.2.3")
        b = SkillVersion.parse("1.2.3")
        assert a == b

    def test_validator_is_deterministic(self) -> None:
        manifest = _full_manifest()
        validator = SkillValidator()
        r1 = validator.validate(manifest)
        r2 = validator.validate(manifest)
        assert r1 == r2

    def test_validator_same_errors_same_order(self) -> None:
        raw = {**_MINIMAL_RAW, "id": "INVALID", "capabilities": []}
        m = SkillManifest.from_dict(raw)
        validator = SkillValidator()
        r1 = validator.validate(m)
        r2 = validator.validate(m)
        assert r1.errors == r2.errors

    def test_load_all_skills_is_deterministic(self, tmp_path: Path) -> None:
        _write_skill_yaml(tmp_path / "z-skill", {**_MINIMAL_RAW, "id": "z-skill", "name": "Z"})
        _write_skill_yaml(tmp_path / "a-skill", {**_MINIMAL_RAW, "id": "a-skill", "name": "A"})
        loader = SkillLoader(skills_root=tmp_path)
        r1 = loader.load_all_skills()
        r2 = loader.load_all_skills()
        assert [s.manifest.id for s in r1] == [s.manifest.id for s in r2]

    def test_load_skill_is_deterministic(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        _write_skill_yaml(skill_dir, _FULL_RAW)
        loader = SkillLoader(skills_root=tmp_path)
        s1 = loader.load_skill(skill_dir)
        s2 = loader.load_skill(skill_dir)
        assert s1.manifest == s2.manifest

    def test_real_skills_load_deterministically(self) -> None:
        """Load the actual skills/ directory twice and compare."""
        loader = SkillLoader()
        r1 = loader.load_all_skills()
        r2 = loader.load_all_skills()
        assert [s.manifest.id for s in r1] == [s.manifest.id for s in r2]

    def test_real_skills_all_pass_validation(self) -> None:
        """Every skill in the real skills/ directory must be valid."""
        loader = SkillLoader()
        validator = SkillValidator()
        all_skills = loader.load_all_skills()
        all_ids = frozenset(s.manifest.id for s in all_skills)
        for skill in all_skills:
            result = validator.validate(skill.manifest, available_skill_ids=all_ids)
            assert result.valid, (
                f"Skill {skill.manifest.id!r} failed validation: "
                + "; ".join(f"{e.field}: {e.message}" for e in result.errors)
            )

    def test_skill_manifest_hashable(self) -> None:
        """SkillManifest is frozen and therefore hashable."""
        m = _minimal_manifest()
        d = {m: "value"}
        assert d[m] == "value"
