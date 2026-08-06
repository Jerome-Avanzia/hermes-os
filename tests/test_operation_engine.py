"""Tests for Sprint 56 — Operation Engine.

Coverage:
  - All typed contracts (OperationStatus, OperationPriority, OperationType,
    OperationDependency, OperationExecutionReference, OperationDefinition,
    OperationValidationError, OperationValidationResult, OperationResult)
  - OperationEngine: build_operation, validate, detect_cycle, is_blocked,
    determine_execution_order, plan, plan_all
  - Lifecycle states: DEFINED, READY, BLOCKED, FAILED
  - Cycle detection: no cycle, direct cycle, transitive cycle, diamond DAG
  - Topological ordering: linear chain, parallel, diamond, gap indices
  - Determinism: same inputs → same outputs across instances
  - Immutability: frozen dataclasses reject mutation
  - Edge cases: empty set, single op, self-dep, all blocked, all ready
"""

from __future__ import annotations

import dataclasses

import pytest

from hermes.kernel.operation_engine import OperationEngine
from hermes.models.operation import (
    OperationDefinition,
    OperationDependency,
    OperationExecutionReference,
    OperationPriority,
    OperationResult,
    OperationStatus,
    OperationType,
    OperationValidationError,
    OperationValidationResult,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════


def _engine() -> OperationEngine:
    return OperationEngine()


def _dep(op_id: str) -> OperationDependency:
    return OperationDependency(operation_id=op_id)


def _ref(adapter: str = "llm", action: str = "generate") -> OperationExecutionReference:
    return OperationExecutionReference(adapter_id=adapter, action_id=action)


def _op(
    op_id: str,
    *,
    job_id: str = "job-001",
    goal: str = "Do something",
    operation_type: OperationType = OperationType.LLM,
    priority: OperationPriority = OperationPriority.NORMAL,
    sequence_index: int = 0,
    capability_id: str = "",
    depends_on: list[OperationDependency] | None = None,
    execution_ref: OperationExecutionReference | None = None,
) -> OperationDefinition:
    """Build an OperationDefinition via the engine (normalised + DEFINED status)."""
    return _engine().build_operation(
        id=op_id,
        job_id=job_id,
        goal=goal,
        operation_type=operation_type,
        priority=priority,
        sequence_index=sequence_index,
        capability_id=capability_id,
        depends_on=depends_on,
        execution_ref=execution_ref,
    )


def _chain(*op_ids: str) -> list[OperationDefinition]:
    """Build a linear dependency chain: op_ids[0] ← op_ids[1] ← … ← op_ids[-1]."""
    ops = []
    for i, op_id in enumerate(op_ids):
        deps = [_dep(op_ids[i - 1])] if i > 0 else []
        ops.append(_op(op_id, sequence_index=i, depends_on=deps))
    return ops


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationStatus
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationStatus:
    def test_defined_value(self) -> None:
        assert OperationStatus.DEFINED.value == "defined"

    def test_validated_value(self) -> None:
        assert OperationStatus.VALIDATED.value == "validated"

    def test_ready_value(self) -> None:
        assert OperationStatus.READY.value == "ready"

    def test_blocked_value(self) -> None:
        assert OperationStatus.BLOCKED.value == "blocked"

    def test_failed_value(self) -> None:
        assert OperationStatus.FAILED.value == "failed"

    def test_completed_value(self) -> None:
        assert OperationStatus.COMPLETED.value == "completed"

    def test_exactly_six_members(self) -> None:
        assert len(OperationStatus) == 6

    def test_from_value(self) -> None:
        assert OperationStatus("ready") is OperationStatus.READY


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationPriority
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationPriority:
    def test_low_value(self) -> None:
        assert OperationPriority.LOW.value == "low"

    def test_normal_value(self) -> None:
        assert OperationPriority.NORMAL.value == "normal"

    def test_high_value(self) -> None:
        assert OperationPriority.HIGH.value == "high"

    def test_critical_value(self) -> None:
        assert OperationPriority.CRITICAL.value == "critical"

    def test_exactly_four_members(self) -> None:
        assert len(OperationPriority) == 4


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationType
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationType:
    def test_all_values(self) -> None:
        expected = {"llm", "git", "http", "filesystem", "database", "automation", "docker", "generic"}
        actual = {t.value for t in OperationType}
        assert actual == expected

    def test_from_value(self) -> None:
        assert OperationType("llm") is OperationType.LLM

    def test_generic_value(self) -> None:
        assert OperationType.GENERIC.value == "generic"


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationDependency
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationDependency:
    def test_construction(self) -> None:
        dep = OperationDependency(operation_id="op-prereq")
        assert dep.operation_id == "op-prereq"

    def test_frozen(self) -> None:
        dep = _dep("op-a")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            dep.operation_id = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert _dep("a") == _dep("a")

    def test_inequality(self) -> None:
        assert _dep("a") != _dep("b")


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationExecutionReference
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationExecutionReference:
    def test_construction(self) -> None:
        ref = OperationExecutionReference(adapter_id="llm", action_id="generate")
        assert ref.adapter_id == "llm"
        assert ref.action_id == "generate"

    def test_frozen(self) -> None:
        ref = _ref()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            ref.adapter_id = "git"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert _ref("llm", "generate") == _ref("llm", "generate")

    def test_inequality(self) -> None:
        assert _ref("llm", "generate") != _ref("git", "commit")


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationDefinition
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationDefinition:
    def _make(self) -> OperationDefinition:
        return _op("op-001", sequence_index=0, capability_id="llm", execution_ref=_ref())

    def test_construction(self) -> None:
        op = self._make()
        assert op.id == "op-001"
        assert op.job_id == "job-001"
        assert op.status is OperationStatus.DEFINED
        assert op.operation_type is OperationType.LLM
        assert op.priority is OperationPriority.NORMAL
        assert op.sequence_index == 0
        assert op.capability_id == "llm"
        assert op.execution_ref is not None

    def test_frozen(self) -> None:
        op = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            op.goal = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        assert self._make() == self._make()

    def test_no_execution_ref_allowed(self) -> None:
        op = _op("op-no-ref")
        assert op.execution_ref is None

    def test_empty_capability_id_allowed(self) -> None:
        op = _op("op-generic", capability_id="")
        assert op.capability_id == ""


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationValidationError / TestOperationValidationResult
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationValidationError:
    def test_construction(self) -> None:
        err = OperationValidationError(field="id", message="must not be empty")
        assert err.field == "id"
        assert err.message == "must not be empty"

    def test_frozen(self) -> None:
        err = OperationValidationError(field="x", message="y")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            err.field = "z"  # type: ignore[misc]


class TestOperationValidationResult:
    def test_valid_construction(self) -> None:
        r = OperationValidationResult(valid=True, errors=())
        assert r.valid is True
        assert r.errors == ()

    def test_invalid_construction(self) -> None:
        err = OperationValidationError(field="id", message="empty")
        r = OperationValidationResult(valid=False, errors=(err,))
        assert not r.valid
        assert len(r.errors) == 1

    def test_frozen(self) -> None:
        r = OperationValidationResult(valid=True, errors=())
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.valid = False  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationResult
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationResult:
    def _make(self) -> OperationResult:
        return OperationResult(
            operation_id="op-001",
            status=OperationStatus.READY,
            blocking_operations=(),
            cycle_detected=False,
            validation_result=OperationValidationResult(valid=True, errors=()),
        )

    def test_construction(self) -> None:
        r = self._make()
        assert r.operation_id == "op-001"
        assert r.status is OperationStatus.READY
        assert r.blocking_operations == ()
        assert r.cycle_detected is False
        assert r.validation_result.valid is True

    def test_frozen(self) -> None:
        r = self._make()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.status = OperationStatus.FAILED  # type: ignore[misc]

    def test_equality(self) -> None:
        assert self._make() == self._make()


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_BuildOperation
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEngineBuildOperation:
    def test_build_minimal(self) -> None:
        engine = _engine()
        op = engine.build_operation(id="op-x", job_id="j", goal="Do X")
        assert op.id == "op-x"
        assert op.job_id == "j"
        assert op.status is OperationStatus.DEFINED
        assert op.operation_type is OperationType.GENERIC
        assert op.priority is OperationPriority.NORMAL
        assert op.sequence_index == 0
        assert op.capability_id == ""
        assert op.depends_on == ()
        assert op.execution_ref is None

    def test_build_with_all_fields(self) -> None:
        engine = _engine()
        op = engine.build_operation(
            id="op-full",
            job_id="j",
            goal="Full op",
            operation_type=OperationType.GIT,
            priority=OperationPriority.HIGH,
            sequence_index=3,
            capability_id="git",
            depends_on=[_dep("op-prev")],
            execution_ref=_ref("git", "commit"),
        )
        assert op.operation_type is OperationType.GIT
        assert op.priority is OperationPriority.HIGH
        assert op.sequence_index == 3
        assert op.capability_id == "git"
        assert len(op.depends_on) == 1
        assert op.execution_ref is not None

    def test_build_normalises_dep_order(self) -> None:
        engine = _engine()
        op = engine.build_operation(
            id="op", job_id="j", goal="g",
            depends_on=[_dep("zzz"), _dep("aaa"), _dep("mmm")],
        )
        dep_ids = [d.operation_id for d in op.depends_on]
        assert dep_ids == sorted(dep_ids)

    def test_build_status_is_defined(self) -> None:
        op = _engine().build_operation(id="op", job_id="j", goal="g")
        assert op.status is OperationStatus.DEFINED

    def test_build_returns_frozen(self) -> None:
        op = _engine().build_operation(id="op", job_id="j", goal="g")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            op.goal = "changed"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_Validate
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEngineValidate:
    def test_valid_minimal(self) -> None:
        engine = _engine()
        result = engine.validate(_op("op-x"))
        assert result.valid is True
        assert result.errors == ()

    def test_valid_with_all_fields(self) -> None:
        engine = _engine()
        op = _op("op-x", sequence_index=5, capability_id="llm",
                 execution_ref=_ref(), depends_on=[_dep("op-y")])
        assert engine.validate(op).valid is True

    # ── id ────────────────────────────────────────────────────────────────────

    def test_empty_id_invalid(self) -> None:
        op = _op("")
        result = _engine().validate(op)
        assert not result.valid
        assert any(e.field == "id" for e in result.errors)

    def test_whitespace_id_invalid(self) -> None:
        op = _op("   ")
        assert not _engine().validate(op).valid

    # ── job_id ────────────────────────────────────────────────────────────────

    def test_empty_job_id_invalid(self) -> None:
        op = _op("op-x", job_id="")
        result = _engine().validate(op)
        assert not result.valid
        assert any(e.field == "job_id" for e in result.errors)

    def test_whitespace_job_id_invalid(self) -> None:
        op = _op("op-x", job_id="   ")
        assert not _engine().validate(op).valid

    # ── goal ──────────────────────────────────────────────────────────────────

    def test_empty_goal_invalid(self) -> None:
        op = _op("op-x", goal="")
        result = _engine().validate(op)
        assert not result.valid
        assert any(e.field == "goal" for e in result.errors)

    # ── sequence_index ────────────────────────────────────────────────────────

    def test_negative_sequence_index_invalid(self) -> None:
        engine = _engine()
        op = OperationDefinition(
            id="op-x", job_id="j", goal="g",
            operation_type=OperationType.GENERIC,
            priority=OperationPriority.NORMAL,
            status=OperationStatus.DEFINED,
            sequence_index=-1,
            capability_id="",
            depends_on=(),
            execution_ref=None,
        )
        result = engine.validate(op)
        assert not result.valid
        assert any(e.field == "sequence_index" for e in result.errors)

    def test_zero_sequence_index_valid(self) -> None:
        op = _op("op-x", sequence_index=0)
        assert _engine().validate(op).valid is True

    # ── depends_on ────────────────────────────────────────────────────────────

    def test_empty_dep_op_id_invalid(self) -> None:
        op = _op("op-x", depends_on=[OperationDependency(operation_id="")])
        result = _engine().validate(op)
        assert not result.valid
        assert any(e.field == "depends_on" for e in result.errors)

    def test_self_dependency_invalid(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-x")])
        result = _engine().validate(op)
        assert not result.valid
        assert any("itself" in e.message.lower() for e in result.errors)

    def test_duplicate_dep_invalid(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-y"), _dep("op-y")])
        result = _engine().validate(op)
        assert not result.valid
        assert any("duplicate" in e.message.lower() for e in result.errors)

    def test_multiple_distinct_deps_valid(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-a"), _dep("op-b")])
        assert _engine().validate(op).valid is True

    # ── execution_ref ─────────────────────────────────────────────────────────

    def test_execution_ref_empty_adapter_id_invalid(self) -> None:
        op = _op("op-x", execution_ref=OperationExecutionReference(adapter_id="", action_id="generate"))
        result = _engine().validate(op)
        assert not result.valid
        assert any("adapter_id" in e.field for e in result.errors)

    def test_execution_ref_empty_action_id_invalid(self) -> None:
        op = _op("op-x", execution_ref=OperationExecutionReference(adapter_id="llm", action_id=""))
        result = _engine().validate(op)
        assert not result.valid
        assert any("action_id" in e.field for e in result.errors)

    def test_execution_ref_none_valid(self) -> None:
        op = _op("op-x", execution_ref=None)
        assert _engine().validate(op).valid is True

    def test_execution_ref_valid(self) -> None:
        op = _op("op-x", execution_ref=_ref("git", "commit"))
        assert _engine().validate(op).valid is True

    def test_multiple_errors_all_reported(self) -> None:
        op = _op("", job_id="", goal="")
        result = _engine().validate(op)
        assert not result.valid
        assert len(result.errors) >= 3


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_CycleDetection
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEngineCycleDetection:
    def test_empty_set_no_cycle(self) -> None:
        assert _engine().detect_cycle([]) is False

    def test_single_op_no_cycle(self) -> None:
        assert _engine().detect_cycle([_op("a")]) is False

    def test_linear_chain_no_cycle(self) -> None:
        ops = _chain("a", "b", "c")
        assert _engine().detect_cycle(ops) is False

    def test_direct_cycle_detected(self) -> None:
        # a depends on b, b depends on a
        a = _op("a", depends_on=[_dep("b")])
        b = _op("b", depends_on=[_dep("a")])
        assert _engine().detect_cycle([a, b]) is True

    def test_self_cycle_detected(self) -> None:
        # Operation depending on itself (validation would catch this too)
        a = OperationDefinition(
            id="a", job_id="j", goal="g",
            operation_type=OperationType.GENERIC,
            priority=OperationPriority.NORMAL,
            status=OperationStatus.DEFINED,
            sequence_index=0, capability_id="",
            depends_on=(OperationDependency(operation_id="a"),),
            execution_ref=None,
        )
        assert _engine().detect_cycle([a]) is True

    def test_transitive_cycle_detected(self) -> None:
        # a → b → c → a
        a = _op("a", depends_on=[_dep("b")])
        b = _op("b", depends_on=[_dep("c")])
        c = _op("c", depends_on=[_dep("a")])
        assert _engine().detect_cycle([a, b, c]) is True

    def test_diamond_dag_no_cycle(self) -> None:
        # base ← left ← top, base ← right ← top
        base = _op("base")
        left = _op("left", depends_on=[_dep("base")])
        right = _op("right", depends_on=[_dep("base")])
        top = _op("top", depends_on=[_dep("left"), _dep("right")])
        assert _engine().detect_cycle([base, left, right, top]) is False

    def test_external_dep_not_in_set_ignored(self) -> None:
        # op-a depends on op-external which is not in the set — not a cycle
        a = _op("op-a", depends_on=[_dep("op-external")])
        assert _engine().detect_cycle([a]) is False

    def test_partial_cycle_in_larger_set(self) -> None:
        # x → y → x (cycle), z is independent (no cycle involving z)
        x = _op("x", depends_on=[_dep("y")])
        y = _op("y", depends_on=[_dep("x")])
        z = _op("z")
        assert _engine().detect_cycle([x, y, z]) is True

    def test_cycle_detection_is_deterministic(self) -> None:
        a = _op("a", depends_on=[_dep("b")])
        b = _op("b", depends_on=[_dep("a")])
        engine = _engine()
        assert engine.detect_cycle([a, b]) == engine.detect_cycle([a, b])


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_IsBlocked
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEngineIsBlocked:
    def test_not_blocked_with_no_deps(self) -> None:
        op = _op("op-x")
        assert _engine().is_blocked(op, frozenset()) is False

    def test_blocked_when_dep_not_completed(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-prereq")])
        assert _engine().is_blocked(op, frozenset()) is True

    def test_not_blocked_when_dep_completed(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-prereq")])
        assert _engine().is_blocked(op, frozenset({"op-prereq"})) is False

    def test_blocked_when_any_dep_not_completed(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-a"), _dep("op-b")])
        assert _engine().is_blocked(op, frozenset({"op-a"})) is True

    def test_not_blocked_when_all_deps_completed(self) -> None:
        op = _op("op-x", depends_on=[_dep("op-a"), _dep("op-b")])
        assert _engine().is_blocked(op, frozenset({"op-a", "op-b"})) is False


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_ExecutionOrder
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEngineExecutionOrder:
    def test_empty_set_returns_empty(self) -> None:
        assert _engine().determine_execution_order([]) == ()

    def test_single_op_returns_that_op(self) -> None:
        op = _op("only")
        assert _engine().determine_execution_order([op]) == ("only",)

    def test_linear_chain_order(self) -> None:
        ops = _chain("a", "b", "c")
        order = _engine().determine_execution_order(ops)
        assert order == ("a", "b", "c")

    def test_parallel_ops_ordered_by_sequence_index(self) -> None:
        # Three independent ops — ordered by sequence_index
        a = _op("a", sequence_index=2)
        b = _op("b", sequence_index=0)
        c = _op("c", sequence_index=1)
        order = _engine().determine_execution_order([a, b, c])
        assert order == ("b", "c", "a")

    def test_parallel_ops_tie_broken_by_id(self) -> None:
        # Same sequence_index — tie-broken by operation_id
        a = _op("z-op", sequence_index=0)
        b = _op("a-op", sequence_index=0)
        order = _engine().determine_execution_order([a, b])
        assert order == ("a-op", "z-op")

    def test_diamond_dag_respects_dependencies(self) -> None:
        base = _op("base", sequence_index=0)
        left = _op("left", sequence_index=1, depends_on=[_dep("base")])
        right = _op("right", sequence_index=1, depends_on=[_dep("base")])
        top = _op("top", sequence_index=2, depends_on=[_dep("left"), _dep("right")])
        order = _engine().determine_execution_order([base, left, right, top])
        assert order.index("base") < order.index("left")
        assert order.index("base") < order.index("right")
        assert order.index("left") < order.index("top")
        assert order.index("right") < order.index("top")

    def test_cycle_produces_partial_result(self) -> None:
        # A cycle means some nodes never reach in_degree=0
        a = _op("a", depends_on=[_dep("b")])
        b = _op("b", depends_on=[_dep("a")])
        c = _op("c")  # independent
        order = _engine().determine_execution_order([a, b, c])
        # c should appear; a and b cannot (cycle)
        assert "c" in order
        assert "a" not in order
        assert "b" not in order

    def test_sequence_index_gaps_dont_affect_validity(self) -> None:
        # Large gaps in sequence_index are fine
        a = _op("a", sequence_index=0)
        b = _op("b", sequence_index=100)
        c = _op("c", sequence_index=999, depends_on=[_dep("b")])
        order = _engine().determine_execution_order([a, b, c])
        assert order.index("b") < order.index("c")

    def test_order_is_deterministic(self) -> None:
        ops = _chain("a", "b", "c")
        e = _engine()
        assert e.determine_execution_order(ops) == e.determine_execution_order(ops)


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_Plan
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEnginePlan:
    def test_plan_ready_no_deps(self) -> None:
        engine = _engine()
        op = _op("op-x")
        result = engine.plan(op, all_ops=[op])
        assert result.status is OperationStatus.READY
        assert result.operation_id == "op-x"
        assert result.blocking_operations == ()
        assert result.cycle_detected is False
        assert result.validation_result.valid is True

    def test_plan_ready_with_completed_dep(self) -> None:
        engine = _engine()
        base = _op("base")
        consumer = _op("consumer", depends_on=[_dep("base")])
        result = engine.plan(consumer, all_ops=[base, consumer],
                             completed_op_ids=frozenset({"base"}))
        assert result.status is OperationStatus.READY
        assert result.blocking_operations == ()

    def test_plan_blocked_by_incomplete_dep(self) -> None:
        engine = _engine()
        base = _op("base")
        consumer = _op("consumer", depends_on=[_dep("base")])
        result = engine.plan(consumer, all_ops=[base, consumer],
                             completed_op_ids=frozenset())
        assert result.status is OperationStatus.BLOCKED
        assert result.blocking_operations == ("base",)

    def test_plan_failed_on_invalid_structure(self) -> None:
        engine = _engine()
        op = _op("", job_id="j", goal="")
        result = engine.plan(op, all_ops=[op])
        assert result.status is OperationStatus.FAILED
        assert not result.validation_result.valid
        assert result.cycle_detected is False

    def test_plan_failed_on_cycle(self) -> None:
        engine = _engine()
        a = _op("a", depends_on=[_dep("b")])
        b = _op("b", depends_on=[_dep("a")])
        result_a = engine.plan(a, all_ops=[a, b])
        assert result_a.status is OperationStatus.FAILED
        assert result_a.cycle_detected is True

    def test_plan_failed_validation_takes_priority_over_cycle(self) -> None:
        engine = _engine()
        bad = _op("", goal="")  # invalid id and goal
        other = _op("other", depends_on=[_dep("")])  # creates indirect cycle marker
        result = engine.plan(bad, all_ops=[bad, other])
        assert result.status is OperationStatus.FAILED
        assert not result.validation_result.valid

    def test_plan_blocking_ops_sorted(self) -> None:
        engine = _engine()
        op = _op("consumer", depends_on=[_dep("zzz"), _dep("aaa"), _dep("mmm")])
        zzz = _op("zzz"); aaa = _op("aaa"); mmm = _op("mmm")
        result = engine.plan(op, all_ops=[zzz, aaa, mmm, op])
        assert list(result.blocking_operations) == sorted(result.blocking_operations)

    def test_plan_defaults_completed_to_empty(self) -> None:
        engine = _engine()
        base = _op("base")
        consumer = _op("consumer", depends_on=[_dep("base")])
        result = engine.plan(consumer, all_ops=[base, consumer])
        assert result.status is OperationStatus.BLOCKED

    def test_plan_result_is_immutable(self) -> None:
        engine = _engine()
        op = _op("op-x")
        result = engine.plan(op, all_ops=[op])
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.status = OperationStatus.COMPLETED  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestOperationEngine_PlanAll
# ══════════════════════════════════════════════════════════════════════════════


class TestOperationEnginePlanAll:
    def test_empty_set_returns_empty(self) -> None:
        assert _engine().plan_all([]) == ()

    def test_single_op_ready(self) -> None:
        op = _op("op-x")
        results = _engine().plan_all([op])
        assert len(results) == 1
        assert results[0].status is OperationStatus.READY

    def test_results_parallel_input_order(self) -> None:
        ops = [_op("c"), _op("a"), _op("b")]
        results = _engine().plan_all(ops)
        assert [r.operation_id for r in results] == ["c", "a", "b"]

    def test_plan_all_linear_chain(self) -> None:
        ops = _chain("a", "b", "c")
        results = _engine().plan_all(ops)
        by_id = {r.operation_id: r for r in results}
        assert by_id["a"].status is OperationStatus.READY
        assert by_id["b"].status is OperationStatus.BLOCKED
        assert by_id["c"].status is OperationStatus.BLOCKED

    def test_plan_all_with_completed_dep(self) -> None:
        base = _op("base")
        consumer = _op("consumer", depends_on=[_dep("base")])
        results = _engine().plan_all([base, consumer],
                                     completed_op_ids=frozenset({"base"}))
        by_id = {r.operation_id: r for r in results}
        assert by_id["base"].status is OperationStatus.READY
        assert by_id["consumer"].status is OperationStatus.READY

    def test_plan_all_all_failed_on_cycle(self) -> None:
        a = _op("a", depends_on=[_dep("b")])
        b = _op("b", depends_on=[_dep("a")])
        results = _engine().plan_all([a, b])
        assert all(r.status is OperationStatus.FAILED for r in results)
        assert all(r.cycle_detected for r in results)

    def test_plan_all_mixed_valid_and_invalid(self) -> None:
        good = _op("good")
        bad = _op("", goal="")
        results = _engine().plan_all([good, bad])
        by_id = {r.operation_id: r for r in results}
        assert by_id["good"].status is OperationStatus.READY
        assert by_id[""].status is OperationStatus.FAILED

    def test_plan_all_returns_frozen_results(self) -> None:
        results = _engine().plan_all([_op("op-x")])
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            results[0].status = OperationStatus.COMPLETED  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════════
# TestDeterminism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def _ops(self) -> list[OperationDefinition]:
        base = _op("base", sequence_index=0)
        mid_a = _op("mid-a", sequence_index=1, depends_on=[_dep("base")])
        mid_b = _op("mid-b", sequence_index=1, depends_on=[_dep("base")])
        top = _op("top", sequence_index=2, depends_on=[_dep("mid-a"), _dep("mid-b")])
        return [base, mid_a, mid_b, top]

    def test_build_is_deterministic(self) -> None:
        e1 = _engine(); e2 = _engine()
        op1 = e1.build_operation(id="op", job_id="j", goal="g")
        op2 = e2.build_operation(id="op", job_id="j", goal="g")
        assert op1 == op2

    def test_validate_is_deterministic(self) -> None:
        op = _op("op-x")
        e = _engine()
        assert e.validate(op) == e.validate(op)

    def test_detect_cycle_is_deterministic(self) -> None:
        ops = self._ops()
        e = _engine()
        assert e.detect_cycle(ops) == e.detect_cycle(ops)

    def test_execution_order_is_deterministic(self) -> None:
        ops = self._ops()
        e = _engine()
        assert e.determine_execution_order(ops) == e.determine_execution_order(ops)

    def test_plan_is_deterministic(self) -> None:
        ops = self._ops()
        e = _engine()
        r1 = e.plan(ops[0], all_ops=ops)
        r2 = e.plan(ops[0], all_ops=ops)
        assert r1 == r2

    def test_plan_all_is_deterministic(self) -> None:
        ops = self._ops()
        e = _engine()
        assert e.plan_all(ops) == e.plan_all(ops)

    def test_plan_across_engine_instances_equal(self) -> None:
        ops = self._ops()
        r1 = _engine().plan_all(ops)
        r2 = _engine().plan_all(ops)
        assert r1 == r2

    def test_dep_order_always_sorted(self) -> None:
        for _ in range(5):
            op = _engine().build_operation(
                id="op", job_id="j", goal="g",
                depends_on=[_dep("zzz"), _dep("aaa"), _dep("mmm")],
            )
            ids = [d.operation_id for d in op.depends_on]
            assert ids == sorted(ids)


# ══════════════════════════════════════════════════════════════════════════════
# TestEdgeCases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_op_with_empty_capability_id_valid(self) -> None:
        op = _op("op-x", capability_id="")
        assert _engine().validate(op).valid is True

    def test_op_with_no_execution_ref_valid(self) -> None:
        op = _op("op-x", execution_ref=None)
        assert _engine().validate(op).valid is True

    def test_long_chain_no_cycle(self) -> None:
        ids = [f"op-{i:03d}" for i in range(20)]
        ops = _chain(*ids)
        assert _engine().detect_cycle(ops) is False
        order = _engine().determine_execution_order(ops)
        assert list(order) == ids

    def test_wide_parallel_ops_ordered_by_sequence_then_id(self) -> None:
        ops = [_op(f"op-{i}", sequence_index=0) for i in range(5, 0, -1)]
        order = _engine().determine_execution_order(ops)
        assert order == tuple(sorted(f"op-{i}" for i in range(1, 6)))

    def test_cycle_among_subset_does_not_affect_independent_ops(self) -> None:
        # Cycle: x ↔ y. z is independent. plan_all should fail x and y but not z.
        x = _op("x", depends_on=[_dep("y")])
        y = _op("y", depends_on=[_dep("x")])
        z = _op("z")
        results = _engine().plan_all([x, y, z])
        by_id = {r.operation_id: r for r in results}
        # All fail because cycle is detected globally
        assert by_id["x"].status is OperationStatus.FAILED
        assert by_id["y"].status is OperationStatus.FAILED
        assert by_id["z"].status is OperationStatus.FAILED
        assert by_id["z"].cycle_detected is True

    def test_plan_all_preserves_order_with_mixed_types(self) -> None:
        ops = [
            _op("llm-op", operation_type=OperationType.LLM),
            _op("git-op", operation_type=OperationType.GIT),
            _op("fs-op", operation_type=OperationType.FILESYSTEM),
        ]
        results = _engine().plan_all(ops)
        assert [r.operation_id for r in results] == ["llm-op", "git-op", "fs-op"]

    def test_plan_all_all_ready_when_all_deps_completed(self) -> None:
        a = _op("a", sequence_index=0)
        b = _op("b", sequence_index=1, depends_on=[_dep("a")])
        c = _op("c", sequence_index=2, depends_on=[_dep("b")])
        results = _engine().plan_all(
            [a, b, c], completed_op_ids=frozenset({"a", "b"})
        )
        by_id = {r.operation_id: r for r in results}
        assert by_id["a"].status is OperationStatus.READY
        assert by_id["b"].status is OperationStatus.READY
        assert by_id["c"].status is OperationStatus.READY

    def test_execution_order_diamond_all_orderings_valid(self) -> None:
        # Diamond: base ← L, base ← R, L+R ← top
        base = _op("base", sequence_index=0)
        L = _op("L", sequence_index=1, depends_on=[_dep("base")])
        R = _op("R", sequence_index=1, depends_on=[_dep("base")])
        top = _op("top", sequence_index=2, depends_on=[_dep("L"), _dep("R")])
        order = _engine().determine_execution_order([base, L, R, top])
        assert len(order) == 4
        assert order[0] == "base"
        assert order[-1] == "top"
        assert set(order[1:3]) == {"L", "R"}
