"""Tests for Phase 6 — EngineeringPlan typed contracts.

Coverage:
  - PlannedOperation construction with and without depends_on
  - PlannedOperation.depends_on is always present (empty tuple default)
  - EngineeringPlan with 1 and 2 operations
  - EngineeringPlan.confidence valid values
  - EngineeringPlanResult success=True with high confidence
  - EngineeringPlanResult success=True with ambiguous confidence
  - EngineeringPlanResult success=False with error
  - Immutability of all dataclasses
"""

from __future__ import annotations

import pytest

from hermes.models.engineering_plan import (
    EngineeringPlan,
    EngineeringPlanResult,
    PlannedOperation,
)


# ── PlannedOperation ──────────────────────────────────────────────────────────


class TestPlannedOperation:
    def test_construction_basic(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="src/hello.py",
            intent="create",
            goal="Create hello module",
        )
        assert op.operation_id == "op-0"
        assert op.target == "src/hello.py"
        assert op.intent == "create"
        assert op.goal == "Create hello module"

    def test_depends_on_defaults_to_empty_tuple(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="src/hello.py",
            intent="create",
            goal="Create hello module",
        )
        assert op.depends_on == ()

    def test_depends_on_with_explicit_empty_tuple(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="src/hello.py",
            intent="create",
            goal="Create hello module",
            depends_on=(),
        )
        assert op.depends_on == ()

    def test_depends_on_with_one_dependency(self):
        op = PlannedOperation(
            operation_id="op-1",
            target="src/world.py",
            intent="modify",
            goal="Modify world module",
            depends_on=("op-0",),
        )
        assert op.depends_on == ("op-0",)

    def test_depends_on_with_multiple_dependencies(self):
        op = PlannedOperation(
            operation_id="op-2",
            target="src/combined.py",
            intent="create",
            goal="Create combined module",
            depends_on=("op-0", "op-1"),
        )
        assert op.depends_on == ("op-0", "op-1")

    def test_intent_create(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="new_file.py",
            intent="create",
            goal="Create new file",
        )
        assert op.intent == "create"

    def test_intent_modify(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="existing.py",
            intent="modify",
            goal="Modify existing file",
        )
        assert op.intent == "modify"

    def test_frozen(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="src/hello.py",
            intent="create",
            goal="Create hello module",
        )
        with pytest.raises((AttributeError, TypeError)):
            op.operation_id = "other"  # type: ignore[misc]

    def test_slots(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="src/hello.py",
            intent="create",
            goal="Create hello module",
        )
        assert not hasattr(op, "__dict__")

    def test_equality(self):
        kwargs = dict(
            operation_id="op-0",
            target="src/hello.py",
            intent="create",
            goal="Create hello module",
            depends_on=("op-1",),
        )
        assert PlannedOperation(**kwargs) == PlannedOperation(**kwargs)

    def test_depends_on_is_tuple_not_list(self):
        op = PlannedOperation(
            operation_id="op-0",
            target="x.py",
            intent="create",
            goal="goal",
            depends_on=("a", "b"),
        )
        assert isinstance(op.depends_on, tuple)


# ── EngineeringPlan ───────────────────────────────────────────────────────────


class TestEngineeringPlan:
    def _make_op(self, i: int, intent: str = "create") -> PlannedOperation:
        return PlannedOperation(
            operation_id=f"op-{i}",
            target=f"src/module{i}.py",
            intent=intent,
            goal=f"Goal for op {i}",
        )

    def test_construction_single_operation(self):
        op = self._make_op(0)
        plan = EngineeringPlan(
            plan_id="plan-001",
            goal_id="goal-001",
            confidence="high",
            basis="Single file change required",
            operations=(op,),
        )
        assert plan.plan_id == "plan-001"
        assert plan.goal_id == "goal-001"
        assert plan.confidence == "high"
        assert len(plan.operations) == 1

    def test_construction_two_operations(self):
        ops = (self._make_op(0), self._make_op(1))
        plan = EngineeringPlan(
            plan_id="plan-002",
            goal_id="goal-002",
            confidence="high",
            basis="Two files need changes",
            operations=ops,
        )
        assert len(plan.operations) == 2
        assert plan.operations[0].operation_id == "op-0"
        assert plan.operations[1].operation_id == "op-1"

    def test_confidence_high(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="high",
            basis="certain", operations=(self._make_op(0),),
        )
        assert plan.confidence == "high"

    def test_confidence_ambiguous(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="ambiguous",
            basis="uncertain",
            operations=(),
            candidates=("src/a.py", "src/b.py"),
        )
        assert plan.confidence == "ambiguous"
        assert len(plan.candidates) == 2

    def test_candidates_defaults_to_empty(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="high",
            basis="certain", operations=(self._make_op(0),),
        )
        assert plan.candidates == ()

    def test_basis_stored(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="high",
            basis="Adding new validation layer",
            operations=(self._make_op(0),),
        )
        assert plan.basis == "Adding new validation layer"

    def test_frozen(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="high",
            basis="b", operations=(self._make_op(0),),
        )
        with pytest.raises((AttributeError, TypeError)):
            plan.plan_id = "other"  # type: ignore[misc]

    def test_slots(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="high",
            basis="b", operations=(self._make_op(0),),
        )
        assert not hasattr(plan, "__dict__")

    def test_operations_is_tuple(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="high",
            basis="b", operations=(self._make_op(0), self._make_op(1)),
        )
        assert isinstance(plan.operations, tuple)

    def test_candidates_is_tuple(self):
        plan = EngineeringPlan(
            plan_id="p", goal_id="g", confidence="ambiguous",
            basis="b", operations=(),
            candidates=("a.py", "b.py"),
        )
        assert isinstance(plan.candidates, tuple)


# ── EngineeringPlanResult ─────────────────────────────────────────────────────


class TestEngineeringPlanResult:
    def _make_plan(self) -> EngineeringPlan:
        return EngineeringPlan(
            plan_id="plan-001",
            goal_id="goal-001",
            confidence="high",
            basis="one file",
            operations=(PlannedOperation(
                operation_id="op-0",
                target="src/a.py",
                intent="create",
                goal="Create A",
            ),),
        )

    def test_success_true_with_high_confidence_plan(self):
        plan = self._make_plan()
        result = EngineeringPlanResult(success=True, plan=plan, error=None)
        assert result.success is True
        assert result.plan is not None
        assert result.error is None

    def test_success_true_with_ambiguous_confidence(self):
        plan = EngineeringPlan(
            plan_id="plan-amb",
            goal_id="goal-amb",
            confidence="ambiguous",
            basis="unclear",
            operations=(),
            candidates=("src/x.py", "src/y.py"),
        )
        result = EngineeringPlanResult(success=True, plan=plan, error=None)
        assert result.success is True
        assert result.plan is not None
        assert result.plan.confidence == "ambiguous"

    def test_success_false_with_error(self):
        result = EngineeringPlanResult(
            success=False,
            plan=None,
            error="json_parse_error: unexpected token",
        )
        assert result.success is False
        assert result.plan is None
        assert result.error is not None
        assert "json_parse_error" in result.error

    def test_success_false_gateway_failure(self):
        result = EngineeringPlanResult(
            success=False,
            plan=None,
            error="gateway_dispatch_failed: adapter not registered",
        )
        assert result.success is False
        assert result.plan is None

    def test_frozen(self):
        result = EngineeringPlanResult(success=True, plan=self._make_plan(), error=None)
        with pytest.raises((AttributeError, TypeError)):
            result.success = False  # type: ignore[misc]

    def test_slots(self):
        result = EngineeringPlanResult(success=True, plan=self._make_plan(), error=None)
        assert not hasattr(result, "__dict__")

    def test_plan_none_on_failure(self):
        result = EngineeringPlanResult(success=False, plan=None, error="some error")
        assert result.plan is None

    def test_equality(self):
        plan = self._make_plan()
        r1 = EngineeringPlanResult(success=True, plan=plan, error=None)
        r2 = EngineeringPlanResult(success=True, plan=plan, error=None)
        assert r1 == r2


# ── Public API ────────────────────────────────────────────────────────────────


class TestPublicAPI:
    def test_all_three_exported(self):
        from hermes.models.engineering_plan import __all__
        assert "PlannedOperation" in __all__
        assert "EngineeringPlan" in __all__
        assert "EngineeringPlanResult" in __all__
