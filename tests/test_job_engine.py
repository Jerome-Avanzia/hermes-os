"""Tests for Sprint 55 — Job Engine.

Coverage:
  - All typed contracts (JobStatus, JobPriority, JobCapabilityRequirement,
    JobOperationReference, JobDependency, JobDefinition, JobValidationError,
    JobValidationResult, JobResult)
  - JobEngine: build_job, validate, resolve_capabilities,
    resolve_unresolved_capabilities, determine_execution_order,
    is_blocked, plan
  - Lifecycle states: DEFINED, READY, BLOCKED, FAILED
  - Determinism: same inputs → same outputs
  - Immutability: frozen dataclasses reject mutation
  - Edge cases: empty registry, optional caps, diamond deps,
    all-optional caps unresolvable, zero-operation jobs
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hermes.kernel.job_engine import JobEngine
from hermes.kernel.skill_registry import SkillRegistry
from hermes.models.job import (
    JobCapabilityRequirement,
    JobDefinition,
    JobDependency,
    JobOperationReference,
    JobPriority,
    JobResult,
    JobStatus,
    JobValidationError,
    JobValidationResult,
)
from hermes.models.skill import InstalledSkill, SkillManifest


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _make_skill(skill_id: str, capabilities: list[str] | None = None) -> InstalledSkill:
    raw = {
        "id": skill_id,
        "name": skill_id.replace("-", " ").title(),
        "version": "1.0.0",
        "capabilities": capabilities if capabilities is not None else [f"cap-{skill_id}"],
    }
    return InstalledSkill(
        manifest=SkillManifest.from_dict(raw),
        path=Path(f"/skills/{skill_id}"),
        knowledge_paths=(),
        sop_paths=(),
    )


def _registry_with(*skill_ids: str) -> SkillRegistry:
    registry = SkillRegistry()
    for sid in skill_ids:
        registry.register(_make_skill(sid))
    return registry


def _engine(registry: SkillRegistry | None = None) -> JobEngine:
    return JobEngine(registry=registry or SkillRegistry())


def _req(cap_id: str, required: bool = True) -> JobCapabilityRequirement:
    return JobCapabilityRequirement(capability_id=cap_id, required=required)


def _op(seq: int, op_id: str, cap_id: str = "cap-x") -> JobOperationReference:
    return JobOperationReference(sequence_index=seq, operation_id=op_id, capability_id=cap_id)


def _dep(job_id: str) -> JobDependency:
    return JobDependency(job_id=job_id)


def _minimal_job(engine: JobEngine, **kwargs) -> JobDefinition:
    params = dict(id="job-001", mission_id="m-001", goal="Do something")
    params.update(kwargs)
    return engine.build_job(**params)


# ══════════════════════════════════════════════════════════════════════════════
# TestJobStatus
# ══════════════════════════════════════════════════════════════════════════════


class TestJobStatus:
    def test_defined_value(self) -> None:
        assert JobStatus.DEFINED.value == "defined"

    def test_validated_value(self) -> None:
        assert JobStatus.VALIDATED.value == "validated"

    def test_ready_value(self) -> None:
        assert JobStatus.READY.value == "ready"

    def test_blocked_value(self) -> None:
        assert JobStatus.BLOCKED.value == "blocked"

    def test_failed_value(self) -> None:
        assert JobStatus.FAILED.value == "failed"

    def test_completed_value(self) -> None:
        assert JobStatus.COMPLETED.value == "completed"

    def test_exactly_six_members(self) -> None:
        assert len(JobStatus) == 6

    def test_from_value(self) -> None:
        assert JobStatus("ready") is JobStatus.READY


# ══════════════════════════════════════════════════════════════════════════════
# TestJobPriority
# ══════════════════════════════════════════════════════════════════════════════


class TestJobPriority:
    def test_low_value(self) -> None:
        assert JobPriority.LOW.value == "low"

    def test_normal_value(self) -> None:
        assert JobPriority.NORMAL.value == "normal"

    def test_high_value(self) -> None:
        assert JobPriority.HIGH.value == "high"

    def test_critical_value(self) -> None:
        assert JobPriority.CRITICAL.value == "critical"

    def test_exactly_four_members(self) -> None:
        assert len(JobPriority) == 4

    def test_from_value(self) -> None:
        assert JobPriority("critical") is JobPriority.CRITICAL


# ══════════════════════════════════════════════════════════════════════════════
# TestJobCapabilityRequirement
# ══════════════════════════════════════════════════════════════════════════════


class TestJobCapabilityRequirement:
    def test_construction(self) -> None:
        req = JobCapabilityRequirement(capability_id="llm")
        assert req.capability_id == "llm"
        assert req.required is True

    def test_optional_requirement(self) -> None:
        req = JobCapabilityRequirement(capability_id="docker", required=False)
        assert req.required is False

    def test_frozen(self) -> None:
        req = JobCapabilityRequirement(capability_id="git")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            req.capability_id = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = JobCapabilityRequirement(capability_id="x")
        b = JobCapabilityRequirement(capability_id="x")
        assert a == b

    def test_inequality(self) -> None:
        a = JobCapabilityRequirement(capability_id="x")
        b = JobCapabilityRequirement(capability_id="y")
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestJobOperationReference
# ══════════════════════════════════════════════════════════════════════════════


class TestJobOperationReference:
    def test_construction(self) -> None:
        ref = JobOperationReference(sequence_index=0, operation_id="op-1", capability_id="llm")
        assert ref.sequence_index == 0
        assert ref.operation_id == "op-1"
        assert ref.capability_id == "llm"

    def test_frozen(self) -> None:
        ref = _op(0, "op-a")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ref.sequence_index = 99  # type: ignore[misc]

    def test_ordering_by_sequence_index(self) -> None:
        a = _op(0, "op-a")
        b = _op(1, "op-b")
        assert a < b

    def test_ordering_by_operation_id_when_index_equal(self) -> None:
        a = _op(0, "op-a")
        b = _op(0, "op-b")
        assert a < b

    def test_sort_produces_correct_order(self) -> None:
        refs = [_op(2, "op-c"), _op(0, "op-a"), _op(1, "op-b")]
        assert [r.sequence_index for r in sorted(refs)] == [0, 1, 2]

    def test_equality(self) -> None:
        a = _op(0, "op-x", "cap-y")
        b = _op(0, "op-x", "cap-y")
        assert a == b


# ══════════════════════════════════════════════════════════════════════════════
# TestJobDependency
# ══════════════════════════════════════════════════════════════════════════════


class TestJobDependency:
    def test_construction(self) -> None:
        dep = JobDependency(job_id="job-prereq")
        assert dep.job_id == "job-prereq"

    def test_frozen(self) -> None:
        dep = _dep("job-prereq")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            dep.job_id = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert _dep("a") == _dep("a")

    def test_inequality(self) -> None:
        assert _dep("a") != _dep("b")


# ══════════════════════════════════════════════════════════════════════════════
# TestJobDefinition
# ══════════════════════════════════════════════════════════════════════════════


class TestJobDefinition:
    def _make(self) -> JobDefinition:
        return JobDefinition(
            id="job-001",
            mission_id="m-001",
            goal="Do the thing",
            priority=JobPriority.NORMAL,
            status=JobStatus.DEFINED,
            capability_requirements=(_req("llm"),),
            operation_refs=(_op(0, "op-draft", "llm"),),
            depends_on=(),
        )

    def test_construction(self) -> None:
        job = self._make()
        assert job.id == "job-001"
        assert job.mission_id == "m-001"
        assert job.goal == "Do the thing"
        assert job.priority is JobPriority.NORMAL
        assert job.status is JobStatus.DEFINED
        assert len(job.capability_requirements) == 1
        assert len(job.operation_refs) == 1
        assert job.depends_on == ()

    def test_frozen(self) -> None:
        job = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            job.goal = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert self._make() == self._make()

    def test_inequality_by_id(self) -> None:
        a = self._make()
        b = JobDefinition(
            id="job-002", mission_id="m-001", goal="Do the thing",
            priority=JobPriority.NORMAL, status=JobStatus.DEFINED,
            capability_requirements=(), operation_refs=(), depends_on=(),
        )
        assert a != b


# ══════════════════════════════════════════════════════════════════════════════
# TestJobValidationError / TestJobValidationResult
# ══════════════════════════════════════════════════════════════════════════════


class TestJobValidationError:
    def test_construction(self) -> None:
        err = JobValidationError(field="id", message="must not be empty")
        assert err.field == "id"
        assert err.message == "must not be empty"

    def test_frozen(self) -> None:
        err = JobValidationError(field="x", message="y")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            err.field = "z"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert JobValidationError("f", "m") == JobValidationError("f", "m")


class TestJobValidationResult:
    def test_valid_construction(self) -> None:
        r = JobValidationResult(valid=True, errors=())
        assert r.valid is True
        assert r.errors == ()

    def test_invalid_construction(self) -> None:
        err = JobValidationError(field="id", message="empty")
        r = JobValidationResult(valid=False, errors=(err,))
        assert r.valid is False
        assert len(r.errors) == 1

    def test_frozen(self) -> None:
        r = JobValidationResult(valid=True, errors=())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.valid = False  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestJobResult
# ══════════════════════════════════════════════════════════════════════════════


class TestJobResult:
    def _make(self) -> JobResult:
        return JobResult(
            job_id="job-001",
            status=JobStatus.READY,
            resolved_capabilities=("llm",),
            unresolved_capabilities=(),
            execution_order=("op-draft",),
            blocking_jobs=(),
            validation_result=JobValidationResult(valid=True, errors=()),
        )

    def test_construction(self) -> None:
        r = self._make()
        assert r.job_id == "job-001"
        assert r.status is JobStatus.READY
        assert r.resolved_capabilities == ("llm",)
        assert r.unresolved_capabilities == ()
        assert r.execution_order == ("op-draft",)
        assert r.blocking_jobs == ()
        assert r.validation_result.valid is True

    def test_frozen(self) -> None:
        r = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.status = JobStatus.FAILED  # type: ignore[misc]

    def test_equality(self) -> None:
        assert self._make() == self._make()


# ══════════════════════════════════════════════════════════════════════════════
# TestJobEngine_BuildJob
# ══════════════════════════════════════════════════════════════════════════════


class TestJobEngineBuildJob:
    def test_build_minimal_job(self) -> None:
        engine = _engine()
        job = engine.build_job(id="j-001", mission_id="m-001", goal="Do something")
        assert job.id == "j-001"
        assert job.status is JobStatus.DEFINED
        assert job.priority is JobPriority.NORMAL
        assert job.capability_requirements == ()
        assert job.operation_refs == ()
        assert job.depends_on == ()

    def test_build_with_all_fields(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j-002",
            mission_id="m-001",
            goal="Full job",
            priority=JobPriority.HIGH,
            capability_requirements=[_req("llm"), _req("git", required=False)],
            operation_refs=[_op(1, "op-b"), _op(0, "op-a")],
            depends_on=[_dep("j-001")],
        )
        assert job.priority is JobPriority.HIGH
        # capability_requirements sorted by capability_id
        assert job.capability_requirements[0].capability_id == "git"
        assert job.capability_requirements[1].capability_id == "llm"
        # operation_refs sorted by sequence_index
        assert job.operation_refs[0].sequence_index == 0
        assert job.operation_refs[1].sequence_index == 1

    def test_build_normalises_capability_order(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("zzz"), _req("aaa"), _req("mmm")],
        )
        cap_ids = [r.capability_id for r in job.capability_requirements]
        assert cap_ids == sorted(cap_ids)

    def test_build_normalises_operation_order(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[_op(2, "c"), _op(0, "a"), _op(1, "b")],
        )
        indices = [r.sequence_index for r in job.operation_refs]
        assert indices == [0, 1, 2]

    def test_build_normalises_dependency_order(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("zzz"), _dep("aaa")],
        )
        dep_ids = [d.job_id for d in job.depends_on]
        assert dep_ids == sorted(dep_ids)

    def test_build_status_is_defined(self) -> None:
        job = _engine().build_job(id="j", mission_id="m", goal="g")
        assert job.status is JobStatus.DEFINED

    def test_build_returns_frozen_definition(self) -> None:
        job = _engine().build_job(id="j", mission_id="m", goal="g")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            job.goal = "changed"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestJobEngine_Validate
# ══════════════════════════════════════════════════════════════════════════════


class TestJobEngineValidate:
    def test_valid_minimal_job(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        result = engine.validate(job)
        assert result.valid is True
        assert result.errors == ()

    def test_valid_full_job(self) -> None:
        registry = _registry_with("writer")
        engine = _engine(registry)
        job = engine.build_job(
            id="j-001",
            mission_id="m-001",
            goal="Draft campaign",
            capability_requirements=[_req("cap-writer")],
            operation_refs=[_op(0, "op-draft", "cap-writer")],
            depends_on=[_dep("j-000")],
        )
        result = engine.validate(job)
        assert result.valid is True

    # ── id ────────────────────────────────────────────────────────────────────

    def test_empty_id_is_invalid(self) -> None:
        engine = _engine()
        job = _minimal_job(engine, id="")
        result = engine.validate(job)
        assert result.valid is False
        assert any("id" in e.field for e in result.errors)

    def test_whitespace_id_is_invalid(self) -> None:
        engine = _engine()
        job = _minimal_job(engine, id="   ")
        result = engine.validate(job)
        assert result.valid is False

    # ── goal ──────────────────────────────────────────────────────────────────

    def test_empty_goal_is_invalid(self) -> None:
        engine = _engine()
        job = _minimal_job(engine, goal="")
        result = engine.validate(job)
        assert result.valid is False
        assert any("goal" in e.field for e in result.errors)

    def test_whitespace_goal_is_invalid(self) -> None:
        engine = _engine()
        job = _minimal_job(engine, goal="   ")
        result = engine.validate(job)
        assert result.valid is False

    # ── capability_requirements ───────────────────────────────────────────────

    def test_empty_capability_id_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[JobCapabilityRequirement(capability_id="")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("capability_requirements" in e.field for e in result.errors)

    def test_duplicate_capability_id_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("llm"), _req("llm")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_unique_capability_ids_valid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("llm"), _req("git")],
        )
        result = engine.validate(job)
        assert result.valid is True

    # ── operation_refs ────────────────────────────────────────────────────────

    def test_empty_operation_id_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[JobOperationReference(sequence_index=0, operation_id="", capability_id="x")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("operation_refs" in e.field for e in result.errors)

    def test_duplicate_operation_id_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[_op(0, "op-x"), _op(1, "op-x")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_negative_sequence_index_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[JobOperationReference(sequence_index=-1, operation_id="op-a", capability_id="x")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("negative" in e.message.lower() for e in result.errors)

    def test_duplicate_sequence_index_is_invalid(self) -> None:
        engine = _engine()
        # Build manually to bypass engine sorting (which preserves duplicates if both have same index)
        job = JobDefinition(
            id="j", mission_id="m", goal="g",
            priority=JobPriority.NORMAL, status=JobStatus.DEFINED,
            capability_requirements=(),
            operation_refs=(
                JobOperationReference(sequence_index=0, operation_id="op-a", capability_id="x"),
                JobOperationReference(sequence_index=0, operation_id="op-b", capability_id="y"),
            ),
            depends_on=(),
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("sequence_index" in e.message.lower() for e in result.errors)

    # ── depends_on ────────────────────────────────────────────────────────────

    def test_empty_dependency_job_id_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[JobDependency(job_id="")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("depends_on" in e.field for e in result.errors)

    def test_self_dependency_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="job-self", mission_id="m", goal="g",
            depends_on=[_dep("job-self")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("itself" in e.message.lower() for e in result.errors)

    def test_duplicate_dependency_is_invalid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-prereq"), _dep("j-prereq")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_multiple_distinct_deps_valid(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-a"), _dep("j-b"), _dep("j-c")],
        )
        assert engine.validate(job).valid is True

    def test_multiple_errors_all_reported(self) -> None:
        engine = _engine()
        job = _minimal_job(engine, id="", goal="")
        result = engine.validate(job)
        assert result.valid is False
        assert len(result.errors) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# TestJobEngine_Capabilities
# ══════════════════════════════════════════════════════════════════════════════


class TestJobEngineCapabilities:
    def test_resolve_single_capability(self) -> None:
        registry = _registry_with("writer")
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("cap-writer")],
        )
        assert engine.resolve_capabilities(job) == ("cap-writer",)

    def test_resolve_capabilities_sorted(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["zzz"]))
        registry.register(_make_skill("b", capabilities=["aaa"]))
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("zzz"), _req("aaa")],
        )
        caps = engine.resolve_capabilities(job)
        assert caps == ("aaa", "zzz")

    def test_resolve_capabilities_empty_registry(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("llm")],
        )
        assert engine.resolve_capabilities(job) == ()

    def test_resolve_includes_optional_capabilities(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["optional-cap"]))
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("optional-cap", required=False)],
        )
        assert engine.resolve_capabilities(job) == ("optional-cap",)

    def test_unresolved_returns_only_required_missing(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["present"]))
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[
                _req("present"),            # required, resolved
                _req("missing"),            # required, unresolved
                _req("missing-opt", required=False),  # optional, unresolved — not in unresolved
            ],
        )
        unresolved = engine.resolve_unresolved_capabilities(job)
        assert unresolved == ("missing",)

    def test_unresolved_empty_when_all_resolved(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["cap-a"]))
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("cap-a")],
        )
        assert engine.resolve_unresolved_capabilities(job) == ()

    def test_unresolved_empty_when_no_requirements(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        assert engine.resolve_unresolved_capabilities(job) == ()

    def test_unresolved_sorted(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("zzz"), _req("aaa"), _req("mmm")],
        )
        unresolved = engine.resolve_unresolved_capabilities(job)
        assert list(unresolved) == sorted(unresolved)


# ══════════════════════════════════════════════════════════════════════════════
# TestJobEngine_ExecutionOrder
# ══════════════════════════════════════════════════════════════════════════════


class TestJobEngineExecutionOrder:
    def test_order_by_sequence_index(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[_op(2, "op-c"), _op(0, "op-a"), _op(1, "op-b")],
        )
        assert engine.determine_execution_order(job) == ("op-a", "op-b", "op-c")

    def test_order_single_operation(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[_op(0, "op-only")],
        )
        assert engine.determine_execution_order(job) == ("op-only",)

    def test_order_empty_operations(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        assert engine.determine_execution_order(job) == ()

    def test_order_tie_broken_by_operation_id(self) -> None:
        engine = _engine()
        # Two operations with same sequence_index — broken by operation_id
        job = JobDefinition(
            id="j", mission_id="m", goal="g",
            priority=JobPriority.NORMAL, status=JobStatus.DEFINED,
            capability_requirements=(),
            operation_refs=(
                JobOperationReference(sequence_index=0, operation_id="op-b", capability_id="x"),
                JobOperationReference(sequence_index=0, operation_id="op-a", capability_id="x"),
            ),
            depends_on=(),
        )
        order = engine.determine_execution_order(job)
        assert order == ("op-a", "op-b")

    def test_order_large_gaps_in_index(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[_op(100, "op-c"), _op(0, "op-a"), _op(50, "op-b")],
        )
        assert engine.determine_execution_order(job) == ("op-a", "op-b", "op-c")


# ══════════════════════════════════════════════════════════════════════════════
# TestJobEngine_IsBlocked
# ══════════════════════════════════════════════════════════════════════════════


class TestJobEngineIsBlocked:
    def test_not_blocked_when_no_deps(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        assert engine.is_blocked(job, frozenset()) is False

    def test_blocked_when_dep_not_completed(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-prereq")],
        )
        assert engine.is_blocked(job, frozenset()) is True

    def test_not_blocked_when_dep_completed(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-prereq")],
        )
        assert engine.is_blocked(job, frozenset({"j-prereq"})) is False

    def test_blocked_when_any_dep_incomplete(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-a"), _dep("j-b")],
        )
        assert engine.is_blocked(job, frozenset({"j-a"})) is True

    def test_not_blocked_when_all_deps_completed(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-a"), _dep("j-b")],
        )
        assert engine.is_blocked(job, frozenset({"j-a", "j-b"})) is False


# ══════════════════════════════════════════════════════════════════════════════
# TestJobEngine_Plan
# ══════════════════════════════════════════════════════════════════════════════


class TestJobEnginePlan:
    def test_plan_ready_when_all_clear(self) -> None:
        registry = _registry_with("writer")
        engine = _engine(registry)
        job = engine.build_job(
            id="j-001",
            mission_id="m-001",
            goal="Write the blog post",
            capability_requirements=[_req("cap-writer")],
            operation_refs=[_op(0, "op-draft", "cap-writer")],
        )
        result = engine.plan(job)
        assert result.status is JobStatus.READY
        assert result.job_id == "j-001"
        assert result.resolved_capabilities == ("cap-writer",)
        assert result.unresolved_capabilities == ()
        assert result.execution_order == ("op-draft",)
        assert result.blocking_jobs == ()
        assert result.validation_result.valid is True

    def test_plan_blocked_by_unfinished_dep(self) -> None:
        registry = _registry_with("writer")
        engine = _engine(registry)
        job = engine.build_job(
            id="j-002",
            mission_id="m-001",
            goal="Publish after review",
            capability_requirements=[_req("cap-writer")],
            depends_on=[_dep("j-001")],
        )
        result = engine.plan(job, completed_job_ids=frozenset())
        assert result.status is JobStatus.BLOCKED
        assert result.blocking_jobs == ("j-001",)

    def test_plan_ready_after_dep_completes(self) -> None:
        registry = _registry_with("writer")
        engine = _engine(registry)
        job = engine.build_job(
            id="j-002",
            mission_id="m-001",
            goal="Publish after review",
            capability_requirements=[_req("cap-writer")],
            depends_on=[_dep("j-001")],
        )
        result = engine.plan(job, completed_job_ids=frozenset({"j-001"}))
        assert result.status is JobStatus.READY
        assert result.blocking_jobs == ()

    def test_plan_failed_when_required_cap_missing(self) -> None:
        engine = _engine()  # empty registry
        job = engine.build_job(
            id="j-003",
            mission_id="m-001",
            goal="Needs LLM",
            capability_requirements=[_req("llm")],
        )
        result = engine.plan(job)
        assert result.status is JobStatus.FAILED
        assert result.unresolved_capabilities == ("llm",)

    def test_plan_failed_on_invalid_structure(self) -> None:
        engine = _engine()
        job = _minimal_job(engine, id="", goal="")
        result = engine.plan(job)
        assert result.status is JobStatus.FAILED
        assert result.validation_result.valid is False
        assert result.resolved_capabilities == ()
        assert result.execution_order == ()

    def test_plan_ready_with_no_requirements(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        result = engine.plan(job)
        assert result.status is JobStatus.READY
        assert result.resolved_capabilities == ()
        assert result.unresolved_capabilities == ()

    def test_plan_failed_unresolved_takes_priority_over_blocked(self) -> None:
        # A job that has unresolved required caps AND blocking deps → FAILED
        engine = _engine()  # empty registry
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("missing-cap")],
            depends_on=[_dep("j-prereq")],
        )
        result = engine.plan(job, completed_job_ids=frozenset())
        assert result.status is JobStatus.FAILED

    def test_plan_defaults_completed_jobs_to_empty_frozenset(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("j-prereq")],
        )
        result = engine.plan(job)  # no completed_job_ids argument
        assert result.status is JobStatus.BLOCKED

    def test_plan_blocking_jobs_sorted(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            depends_on=[_dep("zzz"), _dep("aaa"), _dep("mmm")],
        )
        result = engine.plan(job, completed_job_ids=frozenset())
        assert list(result.blocking_jobs) == sorted(result.blocking_jobs)

    def test_plan_execution_order_in_result(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            operation_refs=[_op(2, "op-c"), _op(0, "op-a"), _op(1, "op-b")],
        )
        result = engine.plan(job)
        assert result.execution_order == ("op-a", "op-b", "op-c")

    def test_plan_optional_unresolved_cap_not_in_unresolved(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("missing-optional", required=False)],
        )
        result = engine.plan(job)
        assert result.status is JobStatus.READY
        assert result.unresolved_capabilities == ()


# ══════════════════════════════════════════════════════════════════════════════
# TestDeterminism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def _engine_and_job(self) -> tuple[JobEngine, JobDefinition]:
        registry = _registry_with("writer", "editor")
        engine = _engine(registry)
        job = engine.build_job(
            id="j-001",
            mission_id="m-001",
            goal="Produce content",
            priority=JobPriority.HIGH,
            capability_requirements=[_req("cap-writer"), _req("cap-editor")],
            operation_refs=[_op(1, "op-edit"), _op(0, "op-draft")],
            depends_on=[_dep("j-000")],
        )
        return engine, job

    def test_build_job_is_deterministic(self) -> None:
        e1, j1 = self._engine_and_job()
        e2, j2 = self._engine_and_job()
        assert j1 == j2

    def test_validate_is_deterministic(self) -> None:
        e, j = self._engine_and_job()
        assert e.validate(j) == e.validate(j)

    def test_resolve_capabilities_is_deterministic(self) -> None:
        e, j = self._engine_and_job()
        assert e.resolve_capabilities(j) == e.resolve_capabilities(j)

    def test_execution_order_is_deterministic(self) -> None:
        e, j = self._engine_and_job()
        assert e.determine_execution_order(j) == e.determine_execution_order(j)

    def test_plan_is_deterministic(self) -> None:
        e, j = self._engine_and_job()
        r1 = e.plan(j, completed_job_ids=frozenset())
        r2 = e.plan(j, completed_job_ids=frozenset())
        assert r1 == r2

    def test_plan_results_equal_across_engine_instances(self) -> None:
        e1, j1 = self._engine_and_job()
        e2, j2 = self._engine_and_job()
        r1 = e1.plan(j1, completed_job_ids=frozenset())
        r2 = e2.plan(j2, completed_job_ids=frozenset())
        assert r1 == r2

    def test_capability_requirements_always_sorted(self) -> None:
        engine = _engine()
        for _ in range(5):
            job = engine.build_job(
                id="j", mission_id="m", goal="g",
                capability_requirements=[_req("zzz"), _req("aaa"), _req("mmm")],
            )
            ids = [r.capability_id for r in job.capability_requirements]
            assert ids == sorted(ids)

    def test_operation_refs_always_sorted(self) -> None:
        engine = _engine()
        for _ in range(5):
            job = engine.build_job(
                id="j", mission_id="m", goal="g",
                operation_refs=[_op(5, "op-e"), _op(1, "op-a"), _op(3, "op-c")],
            )
            indices = [r.sequence_index for r in job.operation_refs]
            assert indices == sorted(indices)


# ══════════════════════════════════════════════════════════════════════════════
# TestEdgeCases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_job_with_zero_operations_is_valid(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        assert engine.validate(job).valid is True

    def test_job_with_zero_requirements_is_ready(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        result = engine.plan(job)
        assert result.status is JobStatus.READY

    def test_all_caps_optional_and_missing_is_ready(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[
                _req("llm", required=False),
                _req("docker", required=False),
            ],
        )
        result = engine.plan(job)
        assert result.status is JobStatus.READY
        assert result.unresolved_capabilities == ()

    def test_resolved_includes_optional_resolved_caps(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["opt-cap"]))
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[_req("opt-cap", required=False)],
        )
        result = engine.plan(job)
        assert "opt-cap" in result.resolved_capabilities

    def test_diamond_dependency_all_completed(self) -> None:
        # j-top depends on j-left and j-right, both depend on j-base
        engine = _engine()
        job = engine.build_job(
            id="j-top", mission_id="m", goal="g",
            depends_on=[_dep("j-left"), _dep("j-right")],
        )
        completed = frozenset({"j-base", "j-left", "j-right"})
        result = engine.plan(job, completed_job_ids=completed)
        assert result.status is JobStatus.READY
        assert result.blocking_jobs == ()

    def test_diamond_dependency_partially_completed(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="j-top", mission_id="m", goal="g",
            depends_on=[_dep("j-left"), _dep("j-right")],
        )
        result = engine.plan(job, completed_job_ids=frozenset({"j-left"}))
        assert result.status is JobStatus.BLOCKED
        assert result.blocking_jobs == ("j-right",)

    def test_mixed_required_and_optional_caps_partial_resolution(self) -> None:
        registry = SkillRegistry()
        registry.register(_make_skill("a", capabilities=["required-cap"]))
        engine = _engine(registry)
        job = engine.build_job(
            id="j", mission_id="m", goal="g",
            capability_requirements=[
                _req("required-cap"),           # resolved
                _req("missing-optional", required=False),  # missing but optional
            ],
        )
        result = engine.plan(job)
        assert result.status is JobStatus.READY
        assert "required-cap" in result.resolved_capabilities
        assert "missing-optional" not in result.resolved_capabilities
        assert result.unresolved_capabilities == ()

    def test_plan_result_is_immutable(self) -> None:
        engine = _engine()
        job = _minimal_job(engine)
        result = engine.plan(job)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.status = JobStatus.COMPLETED  # type: ignore[misc]

    def test_engine_with_empty_registry_is_valid(self) -> None:
        engine = _engine(SkillRegistry())
        job = _minimal_job(engine)
        result = engine.plan(job)
        assert result.status is JobStatus.READY

    def test_multiple_validation_errors_all_reported(self) -> None:
        engine = _engine()
        job = engine.build_job(
            id="",
            mission_id="m",
            goal="",
            capability_requirements=[JobCapabilityRequirement(capability_id="")],
            depends_on=[JobDependency(job_id="")],
        )
        result = engine.validate(job)
        assert result.valid is False
        assert len(result.errors) >= 3
