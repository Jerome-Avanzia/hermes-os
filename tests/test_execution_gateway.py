"""Tests for the Execution Gateway — Sprint 59.

Covers:
  - All 8 typed contracts (construction, frozen, equality)
  - ExecutionAdapter values and count
  - ExecutionStatus values and lifecycle
  - AdapterRegistration construction and immutability
  - ExecutionRequest construction and immutability
  - GatewayValidationResult
  - DispatchDecision
  - GatewayAudit
  - ExecutionResult
  - ExecutionGateway.register() — first-wins, duplicate handling
  - ExecutionGateway.resolve() — hit, miss
  - ExecutionGateway.list_registrations() — deterministic order
  - ExecutionGateway.registered_adapter_ids()
  - ExecutionGateway.build_request() — payload normalisation
  - ExecutionGateway.validate_request() — all error paths
  - ExecutionGateway.dispatch() — success (DISPATCHED)
  - ExecutionGateway.dispatch() — validation failure (FAILED)
  - ExecutionGateway.dispatch() — unregistered adapter (UNSUPPORTED)
  - ExecutionGateway.dispatch() — unavailable adapter (FAILED)
  - Dispatch Matrix: all 8 adapter types
  - Determinism
  - Immutability
  - Edge cases
"""

from __future__ import annotations

import pytest

from hermes.kernel.execution_gateway import ExecutionGateway
from hermes.models.execution_gateway import (
    AdapterRegistration,
    DispatchDecision,
    ExecutionAdapter,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    GatewayAudit,
    GatewayValidationResult,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _registration(
    adapter: ExecutionAdapter,
    adapter_id: str | None = None,
    available: bool = True,
    description: str = "Test adapter",
) -> AdapterRegistration:
    return AdapterRegistration(
        adapter=adapter,
        adapter_id=adapter_id or f"{adapter.value}-adapter",
        available=available,
        description=description,
    )


def _gateway_with(*adapter_types: ExecutionAdapter) -> ExecutionGateway:
    gw = ExecutionGateway()
    for a in adapter_types:
        gw.register(_registration(a))
    return gw


def _request(
    adapter_type: ExecutionAdapter = ExecutionAdapter.LLM,
    request_id: str = "req-001",
    operation_id: str = "op-001",
    action_id: str = "chat",
    payload: dict[str, str] | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=request_id,
        operation_id=operation_id,
        adapter_type=adapter_type,
        action_id=action_id,
        payload=tuple(sorted((payload or {}).items())),
    )


# ── TestExecutionAdapter ──────────────────────────────────────────────────────


class TestExecutionAdapter:
    def test_all_values(self) -> None:
        values = {a.value for a in ExecutionAdapter}
        assert values == {
            "llm", "git", "http", "filesystem",
            "database", "automation", "docker", "validation", "generic",
        }

    def test_exactly_nine_adapters(self) -> None:
        assert len(ExecutionAdapter) == 9

    def test_from_value(self) -> None:
        assert ExecutionAdapter("llm") is ExecutionAdapter.LLM
        assert ExecutionAdapter("docker") is ExecutionAdapter.DOCKER

    def test_all_values_unique(self) -> None:
        values = [a.value for a in ExecutionAdapter]
        assert len(values) == len(set(values))

    def test_matches_operation_type_values(self) -> None:
        from hermes.models.operation import OperationType
        gateway_values = {a.value for a in ExecutionAdapter}
        operation_values = {t.value for t in OperationType}
        assert gateway_values == operation_values


# ── TestExecutionStatus ───────────────────────────────────────────────────────


class TestExecutionStatus:
    def test_all_values(self) -> None:
        values = {s.value for s in ExecutionStatus}
        assert values == {"pending", "dispatched", "succeeded", "failed", "unsupported"}

    def test_exactly_five_statuses(self) -> None:
        assert len(ExecutionStatus) == 5

    def test_from_value(self) -> None:
        assert ExecutionStatus("dispatched") is ExecutionStatus.DISPATCHED
        assert ExecutionStatus("unsupported") is ExecutionStatus.UNSUPPORTED

    def test_dispatched_is_sprint59_terminal_success(self) -> None:
        # DISPATCHED is the terminal success state in Sprint 59
        assert ExecutionStatus.DISPATCHED.value == "dispatched"

    def test_succeeded_reserved_for_future(self) -> None:
        # SUCCEEDED exists but is not set by Sprint 59 dispatch()
        assert ExecutionStatus.SUCCEEDED.value == "succeeded"


# ── TestAdapterRegistration ───────────────────────────────────────────────────


class TestAdapterRegistration:
    def test_construction(self) -> None:
        r = AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude LLM adapter",
        )
        assert r.adapter is ExecutionAdapter.LLM
        assert r.adapter_id == "llm-claude"
        assert r.available is True
        assert r.description == "Claude LLM adapter"

    def test_frozen(self) -> None:
        r = _registration(ExecutionAdapter.LLM)
        with pytest.raises(AttributeError):
            r.available = False  # type: ignore[misc]

    def test_equality(self) -> None:
        a = _registration(ExecutionAdapter.GIT, adapter_id="git-local")
        b = _registration(ExecutionAdapter.GIT, adapter_id="git-local")
        assert a == b

    def test_inequality_adapter_id(self) -> None:
        a = _registration(ExecutionAdapter.GIT, adapter_id="git-local")
        b = _registration(ExecutionAdapter.GIT, adapter_id="git-remote")
        assert a != b

    def test_unavailable(self) -> None:
        r = _registration(ExecutionAdapter.HTTP, available=False)
        assert r.available is False


# ── TestExecutionRequest ──────────────────────────────────────────────────────


class TestExecutionRequest:
    def test_construction(self) -> None:
        r = ExecutionRequest(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload=(("prompt", "hello"), ("temperature", "0.7")),
        )
        assert r.request_id == "req-001"
        assert r.operation_id == "op-001"
        assert r.adapter_type is ExecutionAdapter.LLM
        assert r.action_id == "chat"
        assert r.payload == (("prompt", "hello"), ("temperature", "0.7"))

    def test_frozen(self) -> None:
        r = _request()
        with pytest.raises(AttributeError):
            r.action_id = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = _request()
        b = _request()
        assert a == b

    def test_empty_payload(self) -> None:
        r = _request(payload={})
        assert r.payload == ()

    def test_payload_is_tuple_of_tuples(self) -> None:
        r = ExecutionRequest(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.GIT,
            action_id="clone",
            payload=(("url", "https://example.com/repo.git"),),
        )
        assert isinstance(r.payload, tuple)
        assert isinstance(r.payload[0], tuple)


# ── TestGatewayValidationResult ───────────────────────────────────────────────


class TestGatewayValidationResult:
    def test_valid(self) -> None:
        r = GatewayValidationResult(valid=True, errors=())
        assert r.valid is True
        assert r.errors == ()

    def test_invalid(self) -> None:
        r = GatewayValidationResult(
            valid=False,
            errors=("request_id must not be empty", "action_id must not be empty"),
        )
        assert r.valid is False
        assert len(r.errors) == 2

    def test_frozen(self) -> None:
        r = GatewayValidationResult(valid=True, errors=())
        with pytest.raises(AttributeError):
            r.valid = False  # type: ignore[misc]

    def test_equality(self) -> None:
        a = GatewayValidationResult(valid=True, errors=())
        b = GatewayValidationResult(valid=True, errors=())
        assert a == b


# ── TestDispatchDecision ──────────────────────────────────────────────────────


class TestDispatchDecision:
    def test_construction_dispatched(self) -> None:
        reg = _registration(ExecutionAdapter.LLM, adapter_id="llm-claude")
        d = DispatchDecision(
            request_id="req-001",
            adapter=ExecutionAdapter.LLM,
            adapter_registration=reg,
            dispatched=True,
            reason="dispatched_to_llm-claude",
        )
        assert d.request_id == "req-001"
        assert d.adapter is ExecutionAdapter.LLM
        assert d.adapter_registration is reg
        assert d.dispatched is True
        assert "llm-claude" in d.reason

    def test_construction_not_dispatched(self) -> None:
        d = DispatchDecision(
            request_id="req-001",
            adapter=None,
            adapter_registration=None,
            dispatched=False,
            reason="no_adapter_registered_for_llm",
        )
        assert d.adapter is None
        assert d.adapter_registration is None
        assert d.dispatched is False

    def test_frozen(self) -> None:
        d = DispatchDecision(
            request_id="req-001",
            adapter=None,
            adapter_registration=None,
            dispatched=False,
            reason="test",
        )
        with pytest.raises(AttributeError):
            d.dispatched = True  # type: ignore[misc]

    def test_equality(self) -> None:
        a = DispatchDecision(
            request_id="req-001", adapter=None, adapter_registration=None,
            dispatched=False, reason="test",
        )
        b = DispatchDecision(
            request_id="req-001", adapter=None, adapter_registration=None,
            dispatched=False, reason="test",
        )
        assert a == b


# ── TestGatewayAudit ──────────────────────────────────────────────────────────


class TestGatewayAudit:
    def test_construction(self) -> None:
        audit = GatewayAudit(
            request_id="req-001",
            adapters_evaluated=("git-local", "llm-claude"),
            adapter_selected="llm-claude",
            validation_passed=True,
        )
        assert audit.request_id == "req-001"
        assert audit.adapters_evaluated == ("git-local", "llm-claude")
        assert audit.adapter_selected == "llm-claude"
        assert audit.validation_passed is True

    def test_frozen(self) -> None:
        audit = GatewayAudit(
            request_id="req-001",
            adapters_evaluated=(),
            adapter_selected=None,
            validation_passed=True,
        )
        with pytest.raises(AttributeError):
            audit.validation_passed = False  # type: ignore[misc]

    def test_equality(self) -> None:
        a = GatewayAudit(
            request_id="req-001", adapters_evaluated=(), adapter_selected=None, validation_passed=True,
        )
        b = GatewayAudit(
            request_id="req-001", adapters_evaluated=(), adapter_selected=None, validation_passed=True,
        )
        assert a == b


# ── TestExecutionResult ───────────────────────────────────────────────────────


class TestExecutionResult:
    def _make_result(self, **kwargs) -> ExecutionResult:
        decision = DispatchDecision(
            request_id="req-001", adapter=None, adapter_registration=None,
            dispatched=False, reason="test",
        )
        audit = GatewayAudit(
            request_id="req-001", adapters_evaluated=(), adapter_selected=None, validation_passed=True,
        )
        validation = GatewayValidationResult(valid=True, errors=())
        defaults = dict(
            request_id="req-001",
            operation_id="op-001",
            status=ExecutionStatus.DISPATCHED,
            dispatch_decision=decision,
            audit=audit,
            validation_result=validation,
            output="",
            error=None,
        )
        defaults.update(kwargs)
        return ExecutionResult(**defaults)

    def test_construction_success(self) -> None:
        r = self._make_result()
        assert r.request_id == "req-001"
        assert r.status is ExecutionStatus.DISPATCHED
        assert r.output == ""
        assert r.error is None

    def test_construction_failure(self) -> None:
        r = self._make_result(status=ExecutionStatus.FAILED, error="Adapter unavailable")
        assert r.status is ExecutionStatus.FAILED
        assert r.error == "Adapter unavailable"

    def test_frozen(self) -> None:
        r = self._make_result()
        with pytest.raises(AttributeError):
            r.status = ExecutionStatus.SUCCEEDED  # type: ignore[misc]

    def test_equality(self) -> None:
        a = self._make_result()
        b = self._make_result()
        assert a == b

    def test_unsupported_status(self) -> None:
        r = self._make_result(
            status=ExecutionStatus.UNSUPPORTED,
            error="No adapter registered for llm",
        )
        assert r.status is ExecutionStatus.UNSUPPORTED


# ── TestExecutionGatewayRegistration ──────────────────────────────────────────


class TestExecutionGatewayRegistration:
    def setup_method(self) -> None:
        self.gw = ExecutionGateway()

    def test_register_returns_true_on_first(self) -> None:
        result = self.gw.register(_registration(ExecutionAdapter.LLM))
        assert result is True

    def test_register_returns_false_on_duplicate(self) -> None:
        self.gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-a"))
        result = self.gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-b"))
        assert result is False

    def test_first_wins_on_duplicate(self) -> None:
        self.gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-first"))
        self.gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-second"))
        reg = self.gw.resolve(ExecutionAdapter.LLM)
        assert reg is not None
        assert reg.adapter_id == "llm-first"

    def test_different_adapter_types_all_registered(self) -> None:
        self.gw.register(_registration(ExecutionAdapter.LLM))
        self.gw.register(_registration(ExecutionAdapter.GIT))
        self.gw.register(_registration(ExecutionAdapter.DOCKER))
        assert self.gw.resolve(ExecutionAdapter.LLM) is not None
        assert self.gw.resolve(ExecutionAdapter.GIT) is not None
        assert self.gw.resolve(ExecutionAdapter.DOCKER) is not None

    def test_resolve_returns_none_for_unregistered(self) -> None:
        assert self.gw.resolve(ExecutionAdapter.HTTP) is None

    def test_resolve_returns_registered_adapter(self) -> None:
        reg = _registration(ExecutionAdapter.LLM, adapter_id="llm-claude")
        self.gw.register(reg)
        assert self.gw.resolve(ExecutionAdapter.LLM) == reg

    def test_list_registrations_empty(self) -> None:
        assert self.gw.list_registrations() == ()

    def test_list_registrations_sorted_by_adapter_value(self) -> None:
        # Register in non-alphabetical order
        self.gw.register(_registration(ExecutionAdapter.LLM))
        self.gw.register(_registration(ExecutionAdapter.AUTOMATION))
        self.gw.register(_registration(ExecutionAdapter.DATABASE))
        regs = self.gw.list_registrations()
        values = [r.adapter.value for r in regs]
        assert values == sorted(values)

    def test_list_registrations_is_tuple(self) -> None:
        self.gw.register(_registration(ExecutionAdapter.LLM))
        assert isinstance(self.gw.list_registrations(), tuple)

    def test_registered_adapter_ids_empty(self) -> None:
        assert self.gw.registered_adapter_ids() == ()

    def test_registered_adapter_ids_sorted(self) -> None:
        self.gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-x"))
        self.gw.register(_registration(ExecutionAdapter.GIT, adapter_id="git-x"))
        ids = self.gw.registered_adapter_ids()
        # Sorted by adapter type value: "git" < "llm"
        assert ids == ("git-x", "llm-x")

    def test_register_all_nine_adapter_types(self) -> None:
        for adapter in ExecutionAdapter:
            self.gw.register(_registration(adapter))
        assert len(self.gw.list_registrations()) == 9


# ── TestExecutionGatewayBuildRequest ─────────────────────────────────────────


class TestExecutionGatewayBuildRequest:
    def setup_method(self) -> None:
        self.gw = ExecutionGateway()

    def test_build_request_basic(self) -> None:
        r = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
        )
        assert r.request_id == "req-001"
        assert r.operation_id == "op-001"
        assert r.adapter_type is ExecutionAdapter.LLM
        assert r.action_id == "chat"
        assert r.payload == ()

    def test_build_request_payload_sorted_by_key(self) -> None:
        r = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload={"z_key": "z", "a_key": "a", "m_key": "m"},
        )
        keys = [pair[0] for pair in r.payload]
        assert keys == sorted(keys)

    def test_build_request_payload_values_preserved(self) -> None:
        r = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.GIT,
            action_id="clone",
            payload={"url": "https://example.com/repo.git", "depth": "1"},
        )
        payload_dict = dict(r.payload)
        assert payload_dict["url"] == "https://example.com/repo.git"
        assert payload_dict["depth"] == "1"

    def test_build_request_empty_payload_default(self) -> None:
        r = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.GENERIC,
            action_id="run",
        )
        assert r.payload == ()

    def test_build_request_returns_frozen(self) -> None:
        r = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
        )
        with pytest.raises(AttributeError):
            r.action_id = "changed"  # type: ignore[misc]

    def test_build_request_payload_sorted_deterministic(self) -> None:
        r1 = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload={"b": "2", "a": "1"},
        )
        r2 = self.gw.build_request(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload={"a": "1", "b": "2"},
        )
        assert r1 == r2


# ── TestExecutionGatewayValidateRequest ───────────────────────────────────────


class TestExecutionGatewayValidateRequest:
    def setup_method(self) -> None:
        self.gw = ExecutionGateway()

    def test_valid_request(self) -> None:
        r = _request()
        result = self.gw.validate_request(r)
        assert result.valid is True
        assert result.errors == ()

    def test_empty_request_id(self) -> None:
        r = _request(request_id="")
        result = self.gw.validate_request(r)
        assert result.valid is False
        assert any("request_id" in e for e in result.errors)

    def test_whitespace_request_id(self) -> None:
        r = _request(request_id="   ")
        result = self.gw.validate_request(r)
        assert result.valid is False
        assert any("request_id" in e for e in result.errors)

    def test_empty_operation_id(self) -> None:
        r = _request(operation_id="")
        result = self.gw.validate_request(r)
        assert result.valid is False
        assert any("operation_id" in e for e in result.errors)

    def test_empty_action_id(self) -> None:
        r = _request(action_id="")
        result = self.gw.validate_request(r)
        assert result.valid is False
        assert any("action_id" in e for e in result.errors)

    def test_multiple_errors_all_reported(self) -> None:
        r = _request(request_id="", operation_id="", action_id="")
        result = self.gw.validate_request(r)
        assert result.valid is False
        assert len(result.errors) >= 3

    def test_validation_result_is_frozen(self) -> None:
        r = _request()
        result = self.gw.validate_request(r)
        with pytest.raises(AttributeError):
            result.valid = False  # type: ignore[misc]

    def test_gateway_does_not_validate_payload_schema(self) -> None:
        # Gateway does not check payload contents — that is adapter's responsibility
        r = ExecutionRequest(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload=(("unexpected_key", "unexpected_value"),),
        )
        result = self.gw.validate_request(r)
        assert result.valid is True


# ── TestExecutionGatewayDispatch ──────────────────────────────────────────────


class TestExecutionGatewayDispatch:
    def setup_method(self) -> None:
        self.gw = ExecutionGateway()
        self.gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-claude"))
        self.gw.register(_registration(ExecutionAdapter.GIT, adapter_id="git-local"))

    def test_dispatch_success_status_dispatched(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.status is ExecutionStatus.DISPATCHED

    def test_dispatch_success_request_id_preserved(self) -> None:
        result = self.gw.dispatch(_request(request_id="req-abc"))
        assert result.request_id == "req-abc"

    def test_dispatch_success_operation_id_preserved(self) -> None:
        result = self.gw.dispatch(_request(operation_id="op-xyz"))
        assert result.operation_id == "op-xyz"

    def test_dispatch_success_decision_dispatched(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.dispatch_decision.dispatched is True

    def test_dispatch_success_correct_adapter_selected(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.dispatch_decision.adapter is ExecutionAdapter.LLM
        assert result.dispatch_decision.adapter_registration is not None
        assert result.dispatch_decision.adapter_registration.adapter_id == "llm-claude"

    def test_dispatch_success_no_error(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.error is None

    def test_dispatch_success_output_empty(self) -> None:
        # Sprint 59: adapters not invoked — output is always empty
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.output == ""

    def test_dispatch_success_result_is_frozen(self) -> None:
        result = self.gw.dispatch(_request())
        with pytest.raises(AttributeError):
            result.status = ExecutionStatus.SUCCEEDED  # type: ignore[misc]

    def test_dispatch_success_audit_adapter_selected(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.audit.adapter_selected == "llm-claude"

    def test_dispatch_success_audit_validation_passed(self) -> None:
        result = self.gw.dispatch(_request())
        assert result.audit.validation_passed is True

    def test_dispatch_success_validation_result_valid(self) -> None:
        result = self.gw.dispatch(_request())
        assert result.validation_result.valid is True

    def test_dispatch_validation_failure_status_failed(self) -> None:
        result = self.gw.dispatch(_request(request_id=""))
        assert result.status is ExecutionStatus.FAILED

    def test_dispatch_validation_failure_not_dispatched(self) -> None:
        result = self.gw.dispatch(_request(request_id=""))
        assert result.dispatch_decision.dispatched is False

    def test_dispatch_validation_failure_error_set(self) -> None:
        result = self.gw.dispatch(_request(request_id=""))
        assert result.error is not None
        assert len(result.error) > 0

    def test_dispatch_validation_failure_no_engines_touched(self) -> None:
        result = self.gw.dispatch(_request(request_id=""))
        assert result.dispatch_decision.adapter is None
        assert result.dispatch_decision.adapter_registration is None

    def test_dispatch_validation_failure_audit_not_passed(self) -> None:
        result = self.gw.dispatch(_request(request_id=""))
        assert result.audit.validation_passed is False

    def test_dispatch_unregistered_adapter_unsupported(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.DOCKER))
        assert result.status is ExecutionStatus.UNSUPPORTED

    def test_dispatch_unregistered_adapter_not_dispatched(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.DOCKER))
        assert result.dispatch_decision.dispatched is False

    def test_dispatch_unregistered_adapter_error_set(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.DOCKER))
        assert result.error is not None
        assert "docker" in result.error.lower()

    def test_dispatch_unregistered_adapter_validation_passed(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.DOCKER))
        assert result.audit.validation_passed is True

    def test_dispatch_unavailable_adapter_failed(self) -> None:
        gw = ExecutionGateway()
        gw.register(_registration(ExecutionAdapter.HTTP, available=False))
        result = gw.dispatch(_request(adapter_type=ExecutionAdapter.HTTP))
        assert result.status is ExecutionStatus.FAILED

    def test_dispatch_unavailable_adapter_not_dispatched(self) -> None:
        gw = ExecutionGateway()
        gw.register(_registration(ExecutionAdapter.HTTP, available=False))
        result = gw.dispatch(_request(adapter_type=ExecutionAdapter.HTTP))
        assert result.dispatch_decision.dispatched is False

    def test_dispatch_unavailable_adapter_error_set(self) -> None:
        gw = ExecutionGateway()
        gw.register(_registration(ExecutionAdapter.HTTP, adapter_id="http-x", available=False))
        result = gw.dispatch(_request(adapter_type=ExecutionAdapter.HTTP))
        assert result.error is not None
        assert "http-x" in result.error

    def test_dispatch_unavailable_adapter_registration_in_decision(self) -> None:
        gw = ExecutionGateway()
        reg = _registration(ExecutionAdapter.HTTP, available=False)
        gw.register(reg)
        result = gw.dispatch(_request(adapter_type=ExecutionAdapter.HTTP))
        assert result.dispatch_decision.adapter_registration == reg

    def test_dispatch_git_adapter(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.GIT))
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.dispatch_decision.adapter is ExecutionAdapter.GIT

    def test_dispatch_reason_contains_adapter_id(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert "llm-claude" in result.dispatch_decision.reason

    def test_dispatch_audit_includes_all_registered_adapters(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        # Both llm-claude and git-local should appear in evaluated
        assert "llm-claude" in result.audit.adapters_evaluated
        assert "git-local" in result.audit.adapters_evaluated

    def test_dispatch_audit_adapters_sorted_by_adapter_value(self) -> None:
        result = self.gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        ids = list(result.audit.adapters_evaluated)
        assert ids == sorted(ids)


# ── TestDispatchMatrix ────────────────────────────────────────────────────────


class TestDispatchMatrix:
    """Verify that each of the 8 adapter types can be dispatched through the
    Gateway. Each adapter type: declared, dispatch-only, execution deferred."""

    def setup_method(self) -> None:
        self.gw = ExecutionGateway()
        for adapter in ExecutionAdapter:
            self.gw.register(_registration(adapter))

    def _dispatch(self, adapter: ExecutionAdapter) -> ExecutionResult:
        return self.gw.dispatch(_request(adapter_type=adapter))

    def test_llm_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.LLM)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""   # not executed

    def test_git_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.GIT)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_docker_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.DOCKER)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_filesystem_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.FILESYSTEM)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_http_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.HTTP)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_database_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.DATABASE)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_automation_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.AUTOMATION)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_generic_dispatch_only(self) -> None:
        result = self._dispatch(ExecutionAdapter.GENERIC)
        assert result.status is ExecutionStatus.DISPATCHED
        assert result.output == ""

    def test_all_adapters_produce_immutable_result(self) -> None:
        for adapter in ExecutionAdapter:
            result = self._dispatch(adapter)
            with pytest.raises(AttributeError):
                result.status = ExecutionStatus.SUCCEEDED  # type: ignore[misc]

    def test_all_adapters_no_provider_calls(self) -> None:
        # Verify output is always empty (no external system contacted)
        for adapter in ExecutionAdapter:
            result = self._dispatch(adapter)
            assert result.output == ""


# ── TestDeterminism ───────────────────────────────────────────────────────────


class TestDeterminism:
    def test_validate_request_deterministic(self) -> None:
        gw = ExecutionGateway()
        r = _request()
        result_a = gw.validate_request(r)
        result_b = gw.validate_request(r)
        assert result_a == result_b

    def test_dispatch_deterministic_same_registry(self) -> None:
        gw_a = ExecutionGateway()
        gw_b = ExecutionGateway()
        for gw in (gw_a, gw_b):
            gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-x"))
        r = _request(adapter_type=ExecutionAdapter.LLM)
        result_a = gw_a.dispatch(r)
        result_b = gw_b.dispatch(r)
        assert result_a.status == result_b.status
        assert result_a.dispatch_decision.reason == result_b.dispatch_decision.reason

    def test_list_registrations_deterministic(self) -> None:
        gw = ExecutionGateway()
        gw.register(_registration(ExecutionAdapter.LLM))
        gw.register(_registration(ExecutionAdapter.GIT))
        regs_a = gw.list_registrations()
        regs_b = gw.list_registrations()
        assert regs_a == regs_b

    def test_build_request_deterministic(self) -> None:
        gw = ExecutionGateway()
        r_a = gw.build_request(
            request_id="req-001", operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM, action_id="chat",
            payload={"b": "2", "a": "1"},
        )
        r_b = gw.build_request(
            request_id="req-001", operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM, action_id="chat",
            payload={"a": "1", "b": "2"},
        )
        assert r_a == r_b

    def test_dispatch_unregistered_deterministic(self) -> None:
        gw_a = ExecutionGateway()
        gw_b = ExecutionGateway()
        r = _request(adapter_type=ExecutionAdapter.DOCKER)
        result_a = gw_a.dispatch(r)
        result_b = gw_b.dispatch(r)
        assert result_a.status == result_b.status
        assert result_a.dispatch_decision.reason == result_b.dispatch_decision.reason


# ── TestEdgeCases ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_gateway_resolve_all_return_none(self) -> None:
        gw = ExecutionGateway()
        for adapter in ExecutionAdapter:
            assert gw.resolve(adapter) is None

    def test_dispatch_on_empty_gateway_unsupported(self) -> None:
        gw = ExecutionGateway()
        result = gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.status is ExecutionStatus.UNSUPPORTED

    def test_register_unavailable_then_available_first_wins(self) -> None:
        gw = ExecutionGateway()
        gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-down", available=False))
        gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-up", available=True))
        # First registration (unavailable) wins
        reg = gw.resolve(ExecutionAdapter.LLM)
        assert reg is not None
        assert reg.adapter_id == "llm-down"
        assert reg.available is False

    def test_dispatch_result_audit_is_frozen(self) -> None:
        gw = _gateway_with(ExecutionAdapter.LLM)
        result = gw.dispatch(_request())
        with pytest.raises(AttributeError):
            result.audit.adapter_selected = "changed"  # type: ignore[misc]

    def test_dispatch_result_dispatch_decision_is_frozen(self) -> None:
        gw = _gateway_with(ExecutionAdapter.LLM)
        result = gw.dispatch(_request())
        with pytest.raises(AttributeError):
            result.dispatch_decision.dispatched = False  # type: ignore[misc]

    def test_dispatch_with_payload(self) -> None:
        gw = _gateway_with(ExecutionAdapter.LLM)
        r = ExecutionRequest(
            request_id="req-001",
            operation_id="op-001",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload=(("model", "claude-3"), ("temperature", "0.5")),
        )
        result = gw.dispatch(r)
        assert result.status is ExecutionStatus.DISPATCHED

    def test_whitespace_operation_id_fails_validation(self) -> None:
        gw = ExecutionGateway()
        r = _request(operation_id="   ")
        result = gw.validate_request(r)
        assert result.valid is False

    def test_whitespace_action_id_fails_validation(self) -> None:
        gw = ExecutionGateway()
        r = _request(action_id="   ")
        result = gw.validate_request(r)
        assert result.valid is False

    def test_full_audit_trail_on_success(self) -> None:
        gw = ExecutionGateway()
        gw.register(_registration(ExecutionAdapter.LLM, adapter_id="llm-a"))
        gw.register(_registration(ExecutionAdapter.GIT, adapter_id="git-a"))
        result = gw.dispatch(_request(adapter_type=ExecutionAdapter.LLM))
        assert result.audit.request_id == result.request_id
        assert result.audit.adapter_selected == "llm-a"
        assert result.audit.validation_passed is True
        assert len(result.audit.adapters_evaluated) == 2
