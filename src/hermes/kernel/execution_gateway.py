"""Execution Gateway — the sole dispatch boundary between Hermes and external systems.

Sprint 59: The Execution Gateway is the ONLY place in Hermes where execution
requests cross the boundary to external systems. Every LLM call, Git operation,
Docker invocation, HTTP request, filesystem access, database query, and
automation action must be dispatched through this gateway.

Dispatch contract vs execution:
  The Gateway determines two things and no more:
    1. Which adapter should receive the request.
    2. Whether dispatch is valid (request fields, adapter availability).
  The adapter performs execution outside the deterministic Hermes kernel.

  The Gateway produces a dispatch CONTRACT — an immutable record describing
  exactly which adapter has been selected and why. It does NOT perform or
  trigger execution. Execution is the adapter's responsibility and occurs
  outside the Hermes kernel boundary.

Architecture invariants:
  - The Gateway is the ONLY dispatch boundary. No engine may bypass it.
  - The Gateway is NOT an engine. Engines plan; the Gateway dispatches contracts.
  - The Gateway never implements adapter logic. Adapters are injected; the
    Gateway selects and dispatches to them.
  - The Gateway never calls providers directly.
  - The Gateway never calls Docker, Git, HTTP, filesystems, or databases.
  - The Gateway never performs network operations.
  - The Gateway never performs filesystem work.
  - The Gateway is provider-independent.
  - The Gateway is deterministic: same registry state + same request →
    same dispatch outcome.

Sprint 59 scope:
  The Gateway models dispatch contracts only. Actual adapter implementations
  are outside current scope. dispatch() builds an ExecutionResult with
  status=DISPATCHED — the adapter is identified and the contract is complete,
  but the adapter is not invoked and no execution occurs.

  Adapter implementations (LlmAdapter, GitAdapter, etc.) will be injected
  in future sprints. Execution will occur outside the Gateway, via the adapter.
  The Gateway interface is designed to receive them without changes to
  dispatch logic.

Registration model:
  First registration for an adapter type wins. Subsequent registrations of
  the same ExecutionAdapter type are silently ignored (deterministic,
  consistent with SkillRegistry pattern).
"""

from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)


class ExecutionGateway:
    """The sole dispatch boundary between Hermes and external systems.

    Maintains a registry of AdapterRegistrations. Receives ExecutionRequests,
    validates them, resolves the correct adapter, and produces deterministic
    dispatch contracts.

    The Gateway determines two things:
      1. Which adapter should receive the request.
      2. Whether dispatch is valid (request fields, adapter availability).
    The adapter performs execution outside the deterministic Hermes kernel.

    dispatch() produces an ExecutionResult — a dispatch contract — that
    identifies the selected adapter and records why. It does not invoke
    the adapter. Execution is the adapter's responsibility and occurs
    outside the Gateway.

    Mutable state: the adapter registry. dispatch() is deterministic given
    the same registry state and the same request.

    Usage::

        gateway = ExecutionGateway()

        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.LLM,
            adapter_id="llm-claude",
            available=True,
            description="Claude LLM adapter via Anthropic API",
        ))
        gateway.register(AdapterRegistration(
            adapter=ExecutionAdapter.GIT,
            adapter_id="git-local",
            available=True,
            description="Local Git operations adapter",
        ))

        request = gateway.build_request(
            request_id="req-001",
            operation_id="op-draft",
            adapter_type=ExecutionAdapter.LLM,
            action_id="chat",
            payload={"prompt": "Draft the announcement"},
        )

        result = gateway.dispatch(request)
        # result.status → ExecutionStatus.DISPATCHED
        # result.dispatch_decision.adapter_registration.adapter_id → "llm-claude"
    """

    def __init__(self) -> None:
        # Ordered registry: ExecutionAdapter → AdapterRegistration
        # Order preserved via insertion order (Python 3.7+ dict guarantee)
        self._registry: dict[ExecutionAdapter, AdapterRegistration] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, registration: AdapterRegistration) -> bool:
        """Register an adapter. First registration for a type wins.

        Subsequent registrations for the same ExecutionAdapter type are
        silently ignored. This is consistent with the SkillRegistry
        first-wins pattern and ensures deterministic dispatch across
        repeated registration calls.

        Args:
            registration: The AdapterRegistration to register.

        Returns:
            True if registered; False if a registration for this adapter
            type already exists (ignored).
        """
        if registration.adapter in self._registry:
            logger.debug(
                "ExecutionGateway: adapter type %r already registered as %r — "
                "ignoring duplicate registration %r",
                registration.adapter.value,
                self._registry[registration.adapter].adapter_id,
                registration.adapter_id,
            )
            return False

        self._registry[registration.adapter] = registration
        logger.info(
            "ExecutionGateway: registered adapter %r for type %r (available=%s)",
            registration.adapter_id,
            registration.adapter.value,
            registration.available,
        )
        return True

    # ── Lookup ────────────────────────────────────────────────────────────────

    def resolve(self, adapter_type: ExecutionAdapter) -> AdapterRegistration | None:
        """Return the registered AdapterRegistration for a type, or None.

        Args:
            adapter_type: The ExecutionAdapter category to look up.

        Returns:
            The registered AdapterRegistration, or None if not registered.
        """
        return self._registry.get(adapter_type)

    def list_registrations(self) -> tuple[AdapterRegistration, ...]:
        """Return all registrations in deterministic order.

        Ordered by adapter type value (lexicographic) for full determinism
        regardless of registration order.

        Returns:
            Tuple of all AdapterRegistrations, sorted by adapter type value.
        """
        return tuple(
            self._registry[adapter]
            for adapter in sorted(self._registry, key=lambda a: a.value)
        )

    def registered_adapter_ids(self) -> tuple[str, ...]:
        """Return adapter_ids of all registrations in deterministic order.

        Used for audit trail construction.
        """
        return tuple(r.adapter_id for r in self.list_registrations())

    # ── Request construction ──────────────────────────────────────────────────

    def build_request(
        self,
        *,
        request_id: str,
        operation_id: str,
        adapter_type: ExecutionAdapter,
        action_id: str,
        payload: dict[str, str] | None = None,
    ) -> ExecutionRequest:
        """Build an immutable ExecutionRequest from named parameters.

        Normalises payload to a sorted tuple of (key, value) pairs for
        determinism. Accepts a plain dict for ergonomic construction.

        Args:
            request_id:   Unique identifier for this dispatch request.
            operation_id: The OperationDefinition being dispatched.
            adapter_type: Required gateway adapter category.
            action_id:    Specific action within the adapter.
            payload:      Execution parameters as a plain dict. Defaults to
                          empty dict. Keys and values must be strings.

        Returns:
            ExecutionRequest with payload sorted by key.
        """
        sorted_payload = tuple(sorted((payload or {}).items()))
        return ExecutionRequest(
            request_id=request_id,
            operation_id=operation_id,
            adapter_type=adapter_type,
            action_id=action_id,
            payload=sorted_payload,
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_request(self, request: ExecutionRequest) -> GatewayValidationResult:
        """Validate an ExecutionRequest at the gateway level only.

        This is GATEWAY-LEVEL validation. It checks that the Gateway has
        sufficient information to attempt dispatch:

          1. request_id must be non-empty after stripping whitespace
          2. operation_id must be non-empty after stripping whitespace
          3. action_id must be non-empty after stripping whitespace

        The Gateway does NOT validate:
          - operation correctness  → OperationEngine is the authority
          - job correctness        → JobEngine is the authority
          - skill correctness      → SkillValidator is the authority
          - payload schema         → adapter implementations are the authority

        Args:
            request: The ExecutionRequest to validate.

        Returns:
            GatewayValidationResult with valid=True if all checks pass.
        """
        errors: list[str] = []

        if not request.request_id.strip():
            errors.append("request_id must not be empty")

        if not request.operation_id.strip():
            errors.append("operation_id must not be empty")

        if not request.action_id.strip():
            errors.append("action_id must not be empty")

        return GatewayValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(self, request: ExecutionRequest) -> ExecutionResult:
        """Build a deterministic dispatch contract for an ExecutionRequest.

        The Gateway determines which adapter should receive the request and
        whether dispatch is valid. It does NOT perform execution. The adapter
        performs execution outside the deterministic Hermes kernel.

        Steps (in order):
          1. Validate request → FAILED on validation error
          2. Resolve adapter registration → UNSUPPORTED if not registered
          3. Check adapter availability → FAILED if registered but unavailable
          4. Build dispatch contract → DISPATCHED (success terminal state)

        Status semantics:
          DISPATCHED → the dispatch contract is complete; a valid, available
                       adapter has been identified. The adapter has NOT been
                       invoked. Execution occurs outside the Hermes kernel.
          FAILED     → the contract cannot be built (validation or availability)
          UNSUPPORTED → no adapter is registered for the requested type

        Args:
            request: The ExecutionRequest to dispatch.

        Returns:
            ExecutionResult capturing the full dispatch contract. output=""
            always in Sprint 59 — the adapter has not been invoked and no
            execution has occurred. output will be populated by future adapter
            implementations outside the Gateway.
        """
        # ── 1. Validate request ────────────────────────────────────────────
        validation = self.validate_request(request)
        if not validation.valid:
            logger.warning(
                "ExecutionGateway: dispatch validation failed for request_id=%r: %r",
                request.request_id,
                validation.errors,
            )
            return ExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                status=ExecutionStatus.FAILED,
                dispatch_decision=DispatchDecision(
                    request_id=request.request_id,
                    adapter=None,
                    adapter_registration=None,
                    dispatched=False,
                    reason="validation_failed",
                ),
                audit=GatewayAudit(
                    request_id=request.request_id,
                    adapters_evaluated=self.registered_adapter_ids(),
                    adapter_selected=None,
                    validation_passed=False,
                ),
                validation_result=validation,
                output="",
                error="Request validation failed",
            )

        # ── 2. Resolve adapter ─────────────────────────────────────────────
        registration = self.resolve(request.adapter_type)
        evaluated = self.registered_adapter_ids()

        if registration is None:
            logger.warning(
                "ExecutionGateway: no adapter registered for type %r "
                "(request_id=%r)",
                request.adapter_type.value,
                request.request_id,
            )
            return ExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                status=ExecutionStatus.UNSUPPORTED,
                dispatch_decision=DispatchDecision(
                    request_id=request.request_id,
                    adapter=None,
                    adapter_registration=None,
                    dispatched=False,
                    reason=f"no_adapter_registered_for_{request.adapter_type.value}",
                ),
                audit=GatewayAudit(
                    request_id=request.request_id,
                    adapters_evaluated=evaluated,
                    adapter_selected=None,
                    validation_passed=True,
                ),
                validation_result=validation,
                output="",
                error=f"No adapter registered for adapter type {request.adapter_type.value!r}",
            )

        # ── 3. Check availability ──────────────────────────────────────────
        if not registration.available:
            logger.warning(
                "ExecutionGateway: adapter %r is registered but not available "
                "(request_id=%r)",
                registration.adapter_id,
                request.request_id,
            )
            return ExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                status=ExecutionStatus.FAILED,
                dispatch_decision=DispatchDecision(
                    request_id=request.request_id,
                    adapter=request.adapter_type,
                    adapter_registration=registration,
                    dispatched=False,
                    reason=f"adapter_unavailable_{registration.adapter_id}",
                ),
                audit=GatewayAudit(
                    request_id=request.request_id,
                    adapters_evaluated=evaluated,
                    adapter_selected=registration.adapter_id,
                    validation_passed=True,
                ),
                validation_result=validation,
                output="",
                error=f"Adapter {registration.adapter_id!r} is registered but not available",
            )

        # ── 4. Dispatch contract (Sprint 59: no adapter invocation) ───────
        dispatch_decision = DispatchDecision(
            request_id=request.request_id,
            adapter=request.adapter_type,
            adapter_registration=registration,
            dispatched=True,
            reason=f"dispatched_to_{registration.adapter_id}",
        )

        audit = GatewayAudit(
            request_id=request.request_id,
            adapters_evaluated=evaluated,
            adapter_selected=registration.adapter_id,
            validation_passed=True,
        )

        logger.info(
            "ExecutionGateway: dispatch contract built for request_id=%r "
            "adapter=%r action=%r",
            request.request_id,
            registration.adapter_id,
            request.action_id,
        )

        return ExecutionResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            status=ExecutionStatus.DISPATCHED,
            dispatch_decision=dispatch_decision,
            audit=audit,
            validation_result=validation,
            output="",      # empty: adapter not invoked in Sprint 59
            error=None,
        )
