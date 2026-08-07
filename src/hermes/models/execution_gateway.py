"""Execution Gateway — typed dispatch contracts for the Execution Gateway.

Sprint 59: The Execution Gateway is the ONLY boundary between Hermes and
external systems. Every interaction with LLMs, Docker, Git, HTTP, filesystems,
databases, and future tools must pass through it.

Architecture position:
  The Gateway is NOT an engine. Engines plan; the Gateway dispatches contracts.
  The Gateway is NOT an execution layer. In Sprint 59 it models dispatch
  contracts only. Actual adapter implementations are outside current scope.

  Dispatch contract vs execution:
    The Gateway determines two things and no more:
      1. Which adapter should receive the request.
      2. Whether dispatch is valid (request fields, adapter availability).
    The adapter performs execution outside the deterministic Hermes kernel.
    The Gateway never crosses into execution — it produces a contract
    describing what WOULD execute, and the adapter acts on that contract.

  Hierarchy:
    Goal → Mission → Job → Operation → Execution Gateway → [Adapter → External System]
                                              ↑                   ↑
                                       dispatch contract      execution
                                       (deterministic,        (outside
                                        inside Hermes)         Hermes)

  The Gateway is the terminal boundary of the Hermes kernel. Everything
  beyond the Gateway — adapter logic and external system interaction — is
  outside the deterministic kernel.

Relationship to existing types:
  OperationType (operation.py)   → operation-level adapter category declaration
  ExecutionDeclaration (skill.py) → skill-level adapter requirement declaration
  ExecutionCapability (routing_decision.py) → model-level capability tags (LLM routing)
  ExecutionAdapter (this file)   → gateway-level registered adapter type

  ExecutionAdapter values intentionally mirror OperationType values so that
  the Conductor can map operation type → gateway adapter without translation.
  They are separate types because they serve separate architectural roles:
  OperationType classifies what an operation NEEDS; ExecutionAdapter classifies
  what the Gateway HAS.

Sprint 59 scope:
  All contracts are dispatch-only. ExecutionStatus.DISPATCHED is the terminal
  success state in this sprint — it means a valid adapter was identified and
  the dispatch contract is complete. SUCCEEDED is reserved for future adapters
  and reflects actual execution completion outside the Hermes kernel.
  No provider logic, no network calls, no filesystem access.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── ExecutionAdapter ───────────────────────────────────────────────────────────


class ExecutionAdapter(enum.Enum):
    """Execution Gateway adapter category.

    Classifies which external system an adapter targets. Values mirror
    OperationType so that operation-to-gateway mapping requires no
    translation.

    Each value represents a family of adapters. Multiple registrations of
    the same adapter type (e.g. two LLM providers) are not supported in the
    current architecture — the first registration wins.

    LLM        → large-language-model inference
    GIT        → version control operations
    HTTP       → external HTTP requests
    FILESYSTEM → local filesystem reads/writes
    DATABASE   → database queries and mutations
    AUTOMATION → browser or UI automation
    DOCKER     → container build and run
    VALIDATION → code validation (syntax check, test runner, linter)
    GENERIC    → no specific external system required
    """

    LLM = "llm"
    GIT = "git"
    HTTP = "http"
    FILESYSTEM = "filesystem"
    DATABASE = "database"
    AUTOMATION = "automation"
    DOCKER = "docker"
    VALIDATION = "validation"
    GENERIC = "generic"


# ── ExecutionStatus ────────────────────────────────────────────────────────────


class ExecutionStatus(enum.Enum):
    """Lifecycle status of a Gateway dispatch contract.

    The Gateway produces dispatch contracts, not execution. Status reflects
    where the contract stands — not whether execution has occurred.

    PENDING      → request received; dispatch contract not yet built
    DISPATCHED   → dispatch contract complete; a valid, available adapter has
                   been identified. The adapter has NOT been invoked. Execution
                   occurs outside the Hermes kernel. Terminal success state in
                   Sprint 59.
    SUCCEEDED    → adapter reported successful execution. Set by the adapter
                   after execution completes outside the Hermes kernel.
                   Reserved for future adapter implementations.
    FAILED       → contract cannot be built: validation error or adapter
                   unavailable
    UNSUPPORTED  → no adapter registered for the requested type
    """

    PENDING = "pending"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


# ── AdapterRegistration ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AdapterRegistration:
    """Declaration that an adapter is registered and available in the Gateway.

    The Gateway maintains one AdapterRegistration per ExecutionAdapter type
    (first-wins). An AdapterRegistration is a pure data declaration — it
    carries no execution logic and never invokes external systems.

    adapter     → the category this registration covers
    adapter_id  → unique human-readable identifier (e.g. "llm-claude", "git-local")
    available   → whether this adapter is currently available for dispatch
    description → human-readable description of what this adapter does

    Immutable after construction.
    """

    adapter: ExecutionAdapter
    adapter_id: str
    available: bool
    description: str


# ── ExecutionRequest ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Immutable request to dispatch an operation through the Gateway.

    An ExecutionRequest carries everything the Gateway needs to identify
    the correct adapter and build a dispatch contract. It never triggers
    execution — the Gateway produces a DispatchDecision from it.

    request_id   → unique identifier for this dispatch request
    operation_id → the OperationDefinition being dispatched
    adapter_type → which ExecutionAdapter category is required
    action_id    → specific action within the adapter (e.g. "chat", "clone", "pull")
    payload      → immutable ordered key-value pairs passed to the adapter;
                   sorted by key for determinism

    payload uses tuple[tuple[str, str], ...] because frozen dataclasses
    require immutable fields. Use ExecutionGateway.build_request() to
    construct from a plain dict.

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    adapter_type: ExecutionAdapter
    action_id: str
    payload: tuple[tuple[str, str], ...]   # sorted (key, value) pairs


# ── GatewayValidationResult ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GatewayValidationResult:
    """Outcome of gateway-level validation of an ExecutionRequest.

    This is GATEWAY-LEVEL validation only:
      - required request fields (request_id, operation_id, action_id)
      - dispatch preconditions

    The Gateway does NOT validate:
      - operation correctness  → OperationEngine is the authority
      - job correctness        → JobEngine is the authority
      - skill correctness      → SkillValidator is the authority

    valid=True  → all gateway preconditions met; errors is empty
    valid=False → one or more precondition failures; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[str, ...]


# ── DispatchDecision ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DispatchDecision:
    """Immutable dispatch decision produced by the Execution Gateway.

    Captures which adapter was selected, why, and whether dispatch succeeded.
    This is the Gateway's primary planning output — it describes what WOULD
    happen when an adapter executes, without triggering execution.

    request_id           → request this decision belongs to
    adapter              → selected adapter type (None if not dispatched)
    adapter_registration → the registered adapter selected (None if not found)
    dispatched           → True if a valid, available adapter was found
    reason               → human-readable dispatch outcome description

    Immutable after construction.
    """

    request_id: str
    adapter: ExecutionAdapter | None
    adapter_registration: AdapterRegistration | None
    dispatched: bool
    reason: str


# ── GatewayAudit ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GatewayAudit:
    """Immutable audit trail for a single Gateway dispatch run.

    adapters_evaluated → adapter_ids of all registered adapters at dispatch time,
                         sorted by adapter type value for determinism
    adapter_selected   → adapter_id of the selected adapter, or None
    validation_passed  → True if request-level validation succeeded

    Immutable after construction.
    """

    request_id: str
    adapters_evaluated: tuple[str, ...]
    adapter_selected: str | None
    validation_passed: bool


# ── ExecutionResult ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Immutable dispatch contract produced by the Execution Gateway.

    This is a DISPATCH CONTRACT, not an execution result. The Gateway
    determines which adapter should receive the request and whether dispatch
    is valid. The adapter performs execution outside the Hermes kernel.

    The Gateway builds this object and returns it — no adapter is invoked,
    no external system is contacted, no execution occurs.

    status=DISPATCHED → the contract is complete; a valid adapter was
                        identified. Execution has NOT occurred.
    status=SUCCEEDED  → reserved for future adapters; set by the adapter
                        after execution completes outside the Hermes kernel.

    output  → empty string in Sprint 59; the adapter has not been invoked.
              Populated by adapter implementations in future sprints after
              execution completes outside the Gateway.
    error   → error message if status=FAILED or status=UNSUPPORTED; None
              otherwise

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    status: ExecutionStatus
    dispatch_decision: DispatchDecision
    audit: GatewayAudit
    validation_result: GatewayValidationResult
    output: str              # empty in Sprint 59; populated by future adapters
    error: str | None        # None on success; non-None on FAILED/UNSUPPORTED


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "AdapterRegistration",
    "DispatchDecision",
    "ExecutionAdapter",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "GatewayAudit",
    "GatewayValidationResult",
]
