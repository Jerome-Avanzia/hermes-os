"""Tests for Sprint 57 — Swarm Engine.

Coverage:
  - All typed contracts (SwarmStrategy, SwarmMember, SwarmDependency,
    SwarmDefinition, SwarmValidationError, SwarmValidationResult,
    SwarmExecutionPlan, SwarmResult)
  - SwarmEngine: build_swarm, validate, detect_conflict,
    build_execution_plan, plan
  - Validation: id, job_id, members, context_keys, dependencies
  - Conflict detection: invalid references, direct cycles, transitive cycles,
    diamond DAG (no conflict)
  - Execution ordering: no deps, linear chain, diamond, parallel,
    tie-breaking by (sequence_index, operation_id)
  - Determinism: same inputs → same outputs across instances
  - Immutability: frozen dataclasses reject mutation
  - Edge cases: single member, no deps, all members in cycle,
    empty context_keys, broadcast/pipeline strategies
"""

from __future__ import annotations

import dataclasses

import pytest

from hermes.kernel.swarm_engine import SwarmEngine
from hermes.models.swarm import (
    SwarmDefinition,
    SwarmDependency,
    SwarmExecutionPlan,
    SwarmMember,
    SwarmResult,
    SwarmStrategy,
    SwarmValidationError,
    SwarmValidationResult,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _engine() -> SwarmEngine:
    return SwarmEngine()


def _member(op_id: str, role: str = "", seq: int = 0) -> SwarmMember:
    return SwarmMember(operation_id=op_id, role=role, sequence_index=seq)


def _dep(from_id: str, to_id: str) -> SwarmDependency:
    return SwarmDependency(from_operation_id=from_id, to_operation_id=to_id)


def _minimal_swarm(engine: SwarmEngine, **kwargs) -> SwarmDefinition:
    params: dict = dict(
        id="swarm-001",
        job_id="job-001",
        members=[_member("op-a")],
    )
    params.update(kwargs)
    return engine.build_swarm(**params)


def _chain_swarm(engine: SwarmEngine, *op_ids: str) -> SwarmDefinition:
    """Build a swarm with a linear dependency chain."""
    members = [_member(op_id, seq=i) for i, op_id in enumerate(op_ids)]
    deps = [_dep(op_ids[i - 1], op_ids[i]) for i in range(1, len(op_ids))]
    return engine.build_swarm(
        id="swarm-chain",
        job_id="job-001",
        members=members,
        dependencies=deps,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmStrategy
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmStrategy:
    def test_accumulate_value(self) -> None:
        assert SwarmStrategy.ACCUMULATE.value == "accumulate"

    def test_pipeline_value(self) -> None:
        assert SwarmStrategy.PIPELINE.value == "pipeline"

    def test_broadcast_value(self) -> None:
        assert SwarmStrategy.BROADCAST.value == "broadcast"

    def test_exactly_three_members(self) -> None:
        assert len(SwarmStrategy) == 3

    def test_from_value(self) -> None:
        assert SwarmStrategy("accumulate") is SwarmStrategy.ACCUMULATE


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmMember
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmMember:
    def test_construction(self) -> None:
        m = SwarmMember(operation_id="op-a", role="producer", sequence_index=0)
        assert m.operation_id == "op-a"
        assert m.role == "producer"
        assert m.sequence_index == 0

    def test_defaults(self) -> None:
        m = SwarmMember(operation_id="op-x")
        assert m.role == ""
        assert m.sequence_index == 0

    def test_frozen(self) -> None:
        m = _member("op-x")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            m.operation_id = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert _member("a", "producer", 0) == _member("a", "producer", 0)

    def test_inequality(self) -> None:
        assert _member("a") != _member("b")


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmDependency
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmDependency:
    def test_construction(self) -> None:
        d = SwarmDependency(from_operation_id="op-a", to_operation_id="op-b")
        assert d.from_operation_id == "op-a"
        assert d.to_operation_id == "op-b"

    def test_frozen(self) -> None:
        d = _dep("a", "b")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            d.from_operation_id = "c"  # type: ignore[misc]

    def test_ordering_by_from_first(self) -> None:
        a = _dep("a", "z")
        b = _dep("b", "a")
        assert a < b

    def test_ordering_by_to_when_from_equal(self) -> None:
        a = _dep("x", "a")
        b = _dep("x", "b")
        assert a < b

    def test_equality(self) -> None:
        assert _dep("a", "b") == _dep("a", "b")

    def test_sort_deterministic(self) -> None:
        deps = [_dep("c", "d"), _dep("a", "b"), _dep("a", "a")]
        assert sorted(deps) == [_dep("a", "a"), _dep("a", "b"), _dep("c", "d")]


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmDefinition
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmDefinition:
    def _make(self) -> SwarmDefinition:
        return SwarmDefinition(
            id="swarm-001",
            job_id="job-001",
            strategy=SwarmStrategy.ACCUMULATE,
            members=(_member("op-a", "producer", 0),),
            dependencies=(),
            context_keys=("page_structure",),
        )

    def test_construction(self) -> None:
        s = self._make()
        assert s.id == "swarm-001"
        assert s.job_id == "job-001"
        assert s.strategy is SwarmStrategy.ACCUMULATE
        assert len(s.members) == 1
        assert s.dependencies == ()
        assert s.context_keys == ("page_structure",)

    def test_frozen(self) -> None:
        s = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            s.id = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert self._make() == self._make()


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmValidationError / TestSwarmValidationResult
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmValidationError:
    def test_construction(self) -> None:
        e = SwarmValidationError(field="id", message="must not be empty")
        assert e.field == "id"
        assert e.message == "must not be empty"

    def test_frozen(self) -> None:
        e = SwarmValidationError(field="x", message="y")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            e.field = "z"  # type: ignore[misc]


class TestSwarmValidationResult:
    def test_valid(self) -> None:
        r = SwarmValidationResult(valid=True, errors=())
        assert r.valid is True
        assert r.errors == ()

    def test_invalid(self) -> None:
        err = SwarmValidationError(field="id", message="empty")
        r = SwarmValidationResult(valid=False, errors=(err,))
        assert not r.valid
        assert len(r.errors) == 1

    def test_frozen(self) -> None:
        r = SwarmValidationResult(valid=True, errors=())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.valid = False  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmExecutionPlan
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmExecutionPlan:
    def _make(self) -> SwarmExecutionPlan:
        return SwarmExecutionPlan(
            swarm_id="swarm-001",
            strategy=SwarmStrategy.ACCUMULATE,
            execution_order=("op-a", "op-b"),
            context_keys=("key-1",),
            total_members=2,
        )

    def test_construction(self) -> None:
        p = self._make()
        assert p.swarm_id == "swarm-001"
        assert p.execution_order == ("op-a", "op-b")
        assert p.total_members == 2

    def test_frozen(self) -> None:
        p = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            p.execution_order = ()  # type: ignore[misc]

    def test_equality(self) -> None:
        assert self._make() == self._make()


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmResult
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmResult:
    def _make(self) -> SwarmResult:
        return SwarmResult(
            swarm_id="swarm-001",
            execution_plan=None,
            conflict_detected=False,
            conflict_details=(),
            validation_result=SwarmValidationResult(valid=True, errors=()),
        )

    def test_construction(self) -> None:
        r = self._make()
        assert r.swarm_id == "swarm-001"
        assert r.execution_plan is None
        assert r.conflict_detected is False

    def test_frozen(self) -> None:
        r = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.conflict_detected = True  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmEngine_BuildSwarm
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmEngineBuildSwarm:
    def test_build_minimal(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(id="s", job_id="j", members=[_member("op-a")])
        assert swarm.id == "s"
        assert swarm.job_id == "j"
        assert swarm.strategy is SwarmStrategy.ACCUMULATE
        assert len(swarm.members) == 1
        assert swarm.dependencies == ()
        assert swarm.context_keys == ()

    def test_build_with_all_fields(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s",
            job_id="j",
            strategy=SwarmStrategy.PIPELINE,
            members=[_member("op-a", "producer", 0), _member("op-b", "consumer", 1)],
            dependencies=[_dep("op-a", "op-b")],
            context_keys=["key-z", "key-a"],
        )
        assert swarm.strategy is SwarmStrategy.PIPELINE
        assert len(swarm.members) == 2
        assert len(swarm.dependencies) == 1
        assert swarm.context_keys == ("key-a", "key-z")  # sorted

    def test_build_members_sorted_by_sequence_then_id(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-z", seq=1), _member("op-a", seq=0), _member("op-m", seq=0)],
        )
        op_ids = [m.operation_id for m in swarm.members]
        assert op_ids == ["op-a", "op-m", "op-z"]

    def test_build_deps_sorted(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b"), _member("c")],
            dependencies=[_dep("c", "a"), _dep("a", "b"), _dep("b", "c")],
        )
        assert swarm.dependencies == tuple(sorted(swarm.dependencies))

    def test_build_context_keys_sorted(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            context_keys=["zzz", "aaa", "mmm"],
        )
        assert swarm.context_keys == ("aaa", "mmm", "zzz")

    def test_build_returns_frozen(self) -> None:
        swarm = _engine().build_swarm(id="s", job_id="j", members=[_member("op-a")])
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            swarm.id = "changed"  # type: ignore[misc]

    def test_build_empty_members_list_allowed(self) -> None:
        # build_swarm allows empty; validate() will catch it
        swarm = _engine().build_swarm(id="s", job_id="j")
        assert swarm.members == ()


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmEngine_Validate
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmEngineValidate:
    def test_valid_minimal(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        result = engine.validate(swarm)
        assert result.valid is True
        assert result.errors == ()

    def test_valid_full(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            strategy=SwarmStrategy.BROADCAST,
            members=[_member("op-a", "producer"), _member("op-b", "consumer", 1)],
            dependencies=[_dep("op-a", "op-b")],
            context_keys=["shared-key"],
        )
        assert engine.validate(swarm).valid is True

    # ── id ────────────────────────────────────────────────────────────────────

    def test_empty_id_invalid(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine, id="")
        result = engine.validate(swarm)
        assert not result.valid
        assert any(e.field == "id" for e in result.errors)

    def test_whitespace_id_invalid(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine, id="   ")
        assert not engine.validate(swarm).valid

    # ── job_id ────────────────────────────────────────────────────────────────

    def test_empty_job_id_invalid(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine, job_id="")
        result = engine.validate(swarm)
        assert not result.valid
        assert any(e.field == "job_id" for e in result.errors)

    # ── members ───────────────────────────────────────────────────────────────

    def test_no_members_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(id="s", job_id="j")  # no members
        result = engine.validate(swarm)
        assert not result.valid
        assert any("at least one" in e.message.lower() for e in result.errors)

    def test_empty_member_op_id_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[SwarmMember(operation_id="")],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any(e.field == "members" for e in result.errors)

    def test_duplicate_member_op_id_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-x"), _member("op-x")],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_negative_sequence_index_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[SwarmMember(operation_id="op-x", sequence_index=-1)],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any("negative" in e.message.lower() for e in result.errors)

    def test_zero_sequence_index_valid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[SwarmMember(operation_id="op-x", sequence_index=0)],
        )
        assert engine.validate(swarm).valid is True

    # ── context_keys ──────────────────────────────────────────────────────────

    def test_empty_context_key_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            context_keys=[""],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any(e.field == "context_keys" for e in result.errors)

    def test_duplicate_context_key_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            context_keys=["key-a", "key-a"],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_no_context_keys_valid(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        assert engine.validate(swarm).valid is True

    # ── dependencies ──────────────────────────────────────────────────────────

    def test_empty_from_op_id_in_dep_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            dependencies=[SwarmDependency(from_operation_id="", to_operation_id="op-a")],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any(e.field == "dependencies" for e in result.errors)

    def test_self_dependency_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            dependencies=[_dep("op-a", "op-a")],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any("itself" in e.message.lower() for e in result.errors)

    def test_duplicate_dependency_pair_invalid(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a"), _member("op-b")],
            dependencies=[_dep("op-a", "op-b"), _dep("op-a", "op-b")],
        )
        result = engine.validate(swarm)
        assert not result.valid
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_multiple_errors_all_reported(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(id="", job_id="")
        result = engine.validate(swarm)
        assert not result.valid
        assert len(result.errors) >= 3  # id, job_id, no members


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmEngine_ConflictDetection
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmEngineConflictDetection:
    def test_no_conflict_no_deps(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        found, details = engine.detect_conflict(swarm)
        assert found is False
        assert details == ()

    def test_no_conflict_valid_deps(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b")],
            dependencies=[_dep("a", "b")],
        )
        found, _ = engine.detect_conflict(swarm)
        assert found is False

    def test_no_conflict_diamond_dag(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("base"), _member("left"), _member("right"), _member("top")],
            dependencies=[
                _dep("base", "left"), _dep("base", "right"),
                _dep("left", "top"), _dep("right", "top"),
            ],
        )
        found, _ = engine.detect_conflict(swarm)
        assert found is False

    def test_conflict_dep_references_non_member_from(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            dependencies=[_dep("external-op", "op-a")],
        )
        found, details = engine.detect_conflict(swarm)
        assert found is True
        assert any("external-op" in d for d in details)

    def test_conflict_dep_references_non_member_to(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            dependencies=[_dep("op-a", "non-member")],
        )
        found, details = engine.detect_conflict(swarm)
        assert found is True
        assert any("non-member" in d for d in details)

    def test_conflict_direct_cycle(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b")],
            dependencies=[_dep("a", "b"), _dep("b", "a")],
        )
        found, details = engine.detect_conflict(swarm)
        assert found is True
        assert len(details) > 0

    def test_conflict_transitive_cycle(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b"), _member("c")],
            dependencies=[_dep("a", "b"), _dep("b", "c"), _dep("c", "a")],
        )
        found, _ = engine.detect_conflict(swarm)
        assert found is True

    def test_conflict_details_are_sorted(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a")],
            dependencies=[_dep("z-ext", "a"), _dep("a-ext", "a")],
        )
        _, details = engine.detect_conflict(swarm)
        assert list(details) == sorted(details)

    def test_conflict_detection_deterministic(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b")],
            dependencies=[_dep("a", "b"), _dep("b", "a")],
        )
        r1 = engine.detect_conflict(swarm)
        r2 = engine.detect_conflict(swarm)
        assert r1 == r2


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmEngine_ExecutionPlan
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmEngineExecutionPlan:
    def test_single_member_no_deps(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        plan = engine.build_execution_plan(swarm)
        assert plan.execution_order == ("op-a",)
        assert plan.total_members == 1

    def test_linear_chain_order(self) -> None:
        engine = _engine()
        swarm = _chain_swarm(engine, "a", "b", "c")
        plan = engine.build_execution_plan(swarm)
        assert plan.execution_order == ("a", "b", "c")

    def test_parallel_ops_ordered_by_sequence_then_id(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[
                _member("op-z", seq=0),
                _member("op-a", seq=0),
                _member("op-m", seq=0),
            ],
        )
        plan = engine.build_execution_plan(swarm)
        assert plan.execution_order == ("op-a", "op-m", "op-z")

    def test_diamond_dep_order(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[
                _member("base", seq=0),
                _member("left", seq=1),
                _member("right", seq=1),
                _member("top", seq=2),
            ],
            dependencies=[
                _dep("base", "left"), _dep("base", "right"),
                _dep("left", "top"), _dep("right", "top"),
            ],
        )
        plan = engine.build_execution_plan(swarm)
        assert plan.execution_order[0] == "base"
        assert plan.execution_order[-1] == "top"
        assert set(plan.execution_order[1:3]) == {"left", "right"}

    def test_context_keys_in_plan(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            context_keys=["key-b", "key-a"],
        )
        plan = engine.build_execution_plan(swarm)
        assert plan.context_keys == ("key-a", "key-b")

    def test_strategy_preserved_in_plan(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            strategy=SwarmStrategy.BROADCAST,
            members=[_member("op-a")],
        )
        plan = engine.build_execution_plan(swarm)
        assert plan.strategy is SwarmStrategy.BROADCAST

    def test_plan_total_members_correct(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b"), _member("c")],
        )
        plan = engine.build_execution_plan(swarm)
        assert plan.total_members == 3

    def test_plan_is_frozen(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        plan = engine.build_execution_plan(swarm)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            plan.execution_order = ()  # type: ignore[misc]

    def test_order_is_deterministic(self) -> None:
        engine = _engine()
        swarm = _chain_swarm(engine, "a", "b", "c")
        p1 = engine.build_execution_plan(swarm)
        p2 = engine.build_execution_plan(swarm)
        assert p1 == p2


# ══════════════════════════════════════════════════════════════════════════════
# TestSwarmEngine_Plan
# ══════════════════════════════════════════════════════════════════════════════


class TestSwarmEnginePlan:
    def test_plan_success_minimal(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.conflict_detected is False
        assert result.validation_result.valid is True
        assert result.execution_plan.execution_order == ("op-a",)

    def test_plan_success_with_deps(self) -> None:
        engine = _engine()
        swarm = _chain_swarm(engine, "a", "b", "c")
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.execution_plan.execution_order == ("a", "b", "c")

    def test_plan_fails_on_invalid_structure(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(id="", job_id="")
        result = engine.plan(swarm)
        assert result.execution_plan is None
        assert not result.validation_result.valid
        assert result.conflict_detected is False

    def test_plan_fails_on_conflict_invalid_ref(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            dependencies=[_dep("external", "op-a")],
        )
        result = engine.plan(swarm)
        assert result.execution_plan is None
        assert result.conflict_detected is True
        assert len(result.conflict_details) > 0

    def test_plan_fails_on_conflict_cycle(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b")],
            dependencies=[_dep("a", "b"), _dep("b", "a")],
        )
        result = engine.plan(swarm)
        assert result.execution_plan is None
        assert result.conflict_detected is True

    def test_plan_result_is_frozen(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        result = engine.plan(swarm)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.conflict_detected = True  # type: ignore[misc]

    def test_plan_validation_takes_priority_over_conflict(self) -> None:
        engine = _engine()
        # Invalid structure (no members) — conflict check should not run
        swarm = engine.build_swarm(id="", job_id="")
        result = engine.plan(swarm)
        assert result.execution_plan is None
        assert not result.validation_result.valid
        assert result.conflict_detected is False

    def test_plan_conflict_details_sorted(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("op-a")],
            dependencies=[_dep("z-ext", "op-a"), _dep("a-ext", "op-a")],
        )
        result = engine.plan(swarm)
        assert list(result.conflict_details) == sorted(result.conflict_details)

    def test_plan_swarm_id_preserved_in_result(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine, id="my-swarm")
        result = engine.plan(swarm)
        assert result.swarm_id == "my-swarm"

    def test_plan_all_strategies_produce_valid_results(self) -> None:
        engine = _engine()
        for strategy in SwarmStrategy:
            swarm = engine.build_swarm(
                id="s", job_id="j",
                strategy=strategy,
                members=[_member("op-a"), _member("op-b", seq=1)],
                dependencies=[_dep("op-a", "op-b")],
            )
            result = engine.plan(swarm)
            assert result.execution_plan is not None
            assert result.execution_plan.strategy is strategy


# ══════════════════════════════════════════════════════════════════════════════
# TestDeterminism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def _make_swarm(self, engine: SwarmEngine) -> SwarmDefinition:
        return engine.build_swarm(
            id="swarm-001",
            job_id="job-001",
            strategy=SwarmStrategy.ACCUMULATE,
            members=[
                _member("op-frontend", "producer", 0),
                _member("op-backend", "producer", 1),
                _member("op-copy", "consumer", 2),
            ],
            dependencies=[
                _dep("op-frontend", "op-copy"),
                _dep("op-backend", "op-copy"),
            ],
            context_keys=["page_structure", "api_spec"],
        )

    def test_build_is_deterministic(self) -> None:
        e = _engine()
        s1 = self._make_swarm(e)
        s2 = self._make_swarm(e)
        assert s1 == s2

    def test_validate_is_deterministic(self) -> None:
        e = _engine()
        s = self._make_swarm(e)
        assert e.validate(s) == e.validate(s)

    def test_detect_conflict_is_deterministic(self) -> None:
        e = _engine()
        s = self._make_swarm(e)
        assert e.detect_conflict(s) == e.detect_conflict(s)

    def test_build_execution_plan_is_deterministic(self) -> None:
        e = _engine()
        s = self._make_swarm(e)
        assert e.build_execution_plan(s) == e.build_execution_plan(s)

    def test_plan_is_deterministic(self) -> None:
        e = _engine()
        s = self._make_swarm(e)
        assert e.plan(s) == e.plan(s)

    def test_plan_across_engine_instances_equal(self) -> None:
        e1 = _engine()
        e2 = _engine()
        s1 = self._make_swarm(e1)
        s2 = self._make_swarm(e2)
        assert e1.plan(s1) == e2.plan(s2)

    def test_members_always_sorted_by_sequence_then_id(self) -> None:
        engine = _engine()
        for _ in range(5):
            swarm = engine.build_swarm(
                id="s", job_id="j",
                members=[_member("z-op", seq=0), _member("a-op", seq=0), _member("m-op", seq=1)],
            )
            ids = [m.operation_id for m in swarm.members]
            assert ids == ["a-op", "z-op", "m-op"]

    def test_context_keys_always_sorted(self) -> None:
        engine = _engine()
        for _ in range(5):
            swarm = engine.build_swarm(
                id="s", job_id="j",
                members=[_member("op-a")],
                context_keys=["zzz", "aaa", "mmm"],
            )
            assert list(swarm.context_keys) == sorted(swarm.context_keys)


# ══════════════════════════════════════════════════════════════════════════════
# TestEdgeCases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_single_member_no_deps_ready(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine)
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.execution_plan.execution_order == ("op-a",)

    def test_long_chain_no_conflict(self) -> None:
        engine = _engine()
        ids = [f"op-{i:03d}" for i in range(10)]
        swarm = _chain_swarm(engine, *ids)
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.execution_plan.execution_order == tuple(ids)

    def test_members_with_same_sequence_index_ordered_by_id(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("z", seq=0), _member("a", seq=0), _member("m", seq=0)],
        )
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.execution_plan.execution_order == ("a", "m", "z")

    def test_empty_member_role_accepted(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[SwarmMember(operation_id="op-x", role="")],
        )
        assert engine.validate(swarm).valid is True

    def test_broadcast_strategy_single_wave(self) -> None:
        engine = _engine()
        # broadcast: no cross-member deps, all members at same level
        swarm = engine.build_swarm(
            id="s", job_id="j",
            strategy=SwarmStrategy.BROADCAST,
            members=[_member("a", seq=0), _member("b", seq=0), _member("c", seq=0)],
        )
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert set(result.execution_plan.execution_order) == {"a", "b", "c"}

    def test_pipeline_strategy_sequential_order(self) -> None:
        engine = _engine()
        swarm = engine.build_swarm(
            id="s", job_id="j",
            strategy=SwarmStrategy.PIPELINE,
            members=[_member("a", seq=0), _member("b", seq=1), _member("c", seq=2)],
            dependencies=[_dep("a", "b"), _dep("b", "c")],
        )
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.execution_plan.execution_order == ("a", "b", "c")

    def test_cycle_in_subset_detected(self) -> None:
        engine = _engine()
        # a and b cycle; c is independent
        swarm = engine.build_swarm(
            id="s", job_id="j",
            members=[_member("a"), _member("b"), _member("c")],
            dependencies=[_dep("a", "b"), _dep("b", "a")],
        )
        found, _ = engine.detect_conflict(swarm)
        assert found is True

    def test_plan_execution_plan_id_matches_swarm_id(self) -> None:
        engine = _engine()
        swarm = _minimal_swarm(engine, id="my-unique-swarm")
        result = engine.plan(swarm)
        assert result.execution_plan is not None
        assert result.execution_plan.swarm_id == "my-unique-swarm"
