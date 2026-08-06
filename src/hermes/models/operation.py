"""Operation models — legacy execution record and Sprint 56 typed planning contracts.

The legacy Operation class (ADR-0004) is preserved unchanged at the top of
this module. The Sprint 56 typed contracts below it are the new Operation
Engine layer.

Architecture separation:
  - Operation (legacy)        : mutable execution record, used by the workspace layer
  - OperationDefinition       : frozen planning contract, used by the Operation Engine
  - OperationResult           : frozen engine output, consumed by the Job Engine / Conductor
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Legacy (ADR-0004) ─────────────────────────────────────────────────────────


OPERATION_STATUSES = frozenset({
    "created",
    "executing",
    "awaiting_escalation",
    "completed",
    "rejected",
    "failed",
})

# Persistent statuses written to Operations.md (Business Knowledge layer).
# awaiting_escalation is an internal workspace lifecycle state, not persisted.
BK_OPERATION_STATUSES = frozenset({
    "created",
    "executing",
    "completed",
    "failed",
    "rejected",
})

# Structured outcome classification for analytics.
OUTCOME_CLASSIFICATIONS = frozenset({
    "success",
    "partial",
    "failure",
    "cancelled",
})

VALID_TRANSITIONS: dict[str, list[str]] = {
    "created": ["executing"],
    "executing": ["completed", "awaiting_escalation", "failed"],
    "awaiting_escalation": ["executing", "rejected"],
}


class InvalidTransitionError(Exception):
    pass


@dataclass(slots=True)
class Operation:
    """A tracked, lifecycle-bound unit of work within a Workspace (ADR-0004)."""

    id: str
    workspace_id: str
    request: str
    status: str
    created_at: datetime
    updated_at: datetime
    outcome: str | None = None
    outcome_classification: str | None = None
    decision_id: str | None = None
    recommendation_id: str | None = None
    review_id: str | None = None
    sop_id: str | None = None
    extra_fields: dict[str, Any] = field(default_factory=dict)


def transition_operation(operation: Operation, new_status: str) -> None:
    """Validate and apply a lifecycle transition in place.

    Raises InvalidTransitionError if the transition is not allowed.
    """
    allowed = VALID_TRANSITIONS.get(operation.status, [])
    if new_status not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from '{operation.status}' to '{new_status}'"
        )
    operation.status = new_status
    operation.updated_at = datetime.now(operation.created_at.tzinfo)


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 56 — Operation Engine typed contracts
#
# An OperationDefinition is a PLANNING contract — it describes the smallest
# independently executable unit of work within a Job.
#
# Operations describe execution. They never execute.
# Execution belongs exclusively to the Execution Gateway.
# ══════════════════════════════════════════════════════════════════════════════


# ── Enums ─────────────────────────────────────────────────────────────────────


class OperationStatus(enum.Enum):
    """Deterministic lifecycle state of an Operation as determined by the
    Operation Engine.

    Lifecycle:
      DEFINED   → initial state; operation constructed but not planned
      VALIDATED → structural validation passed; dependency/blocking check pending
                  reserved for future two-phase planning protocol
      READY     → validation passed, no blocking dependencies
      BLOCKED   → validation passed, one or more prerequisite operations pending
      FAILED    → structural validation failed, or cycle detected in dep graph
      COMPLETED → all execution finished; set by execution layer, not engine
    """

    DEFINED = "defined"
    VALIDATED = "validated"   # structural validation passed; blocking check not yet run
                              # reserved for future two-phase planning protocol
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"   # execution finished; set by execution layer, not engine


class OperationPriority(enum.Enum):
    """Execution priority for an Operation.

    Used by the Conductor and Execution Gateway to order competing operations.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class OperationType(enum.Enum):
    """The required Execution Gateway adapter category for an Operation.

    OperationType classifies WHICH ADAPTER the operation requires, not the
    business meaning of what the operation does. Two operations with entirely
    different business purposes (e.g. "summarise document" and "classify
    sentiment") both have type LLM because both require the LLM adapter.
    The business meaning lives in OperationDefinition.goal.

    Maps to the adapters declared in ExecutionDeclaration.adapters on the
    parent Skill. Used by the Conductor to validate gateway adapter availability
    before submitting an operation for execution.
    """

    LLM = "llm"               # large-language-model inference
    GIT = "git"               # version control operations
    HTTP = "http"             # external HTTP requests
    FILESYSTEM = "filesystem" # local filesystem reads/writes
    DATABASE = "database"     # database queries and mutations
    AUTOMATION = "automation" # browser or UI automation
    DOCKER = "docker"         # container build and run
    GENERIC = "generic"       # no specific gateway adapter required


# ── OperationDependency ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationDependency:
    """A declared dependency on a prerequisite Operation within the same Job.

    operation_id must reference another OperationDefinition in the same job's
    operation set. The Operation Engine uses this to detect cycles and compute
    blocking state.

    Self-dependencies are a validation error.
    Cross-job dependencies are not supported at the operation level; use
    JobDependency for job-level prerequisite relationships.

    Immutable after construction.
    """

    operation_id: str


# ── OperationExecutionReference ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationExecutionReference:
    """Describes which Execution Gateway adapter and action this operation
    targets when it is eventually executed.

    This is a declaration — it never triggers execution. The Conductor uses
    it during planning to verify adapter availability. The Execution Gateway
    uses it at execution time to route the operation.

    adapter_id must match a registered adapter in the Execution Gateway's
    adapter registry (e.g. "llm", "git", "http", "filesystem").
    action_id identifies the specific action within that adapter.

    Immutable after construction.
    """

    adapter_id: str   # target gateway adapter
    action_id: str    # specific action within the adapter


# ── OperationDefinition ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Immutable planning contract for the smallest unit of work within a Job.

    Sprint 56: An OperationDefinition describes what needs to happen, what
    capability it requires, and which gateway adapter it will use. It never
    executes. The Operation Engine consumes it and produces an OperationResult.

    Ownership:
      - Belongs to a single Job (job_id)
      - May depend on other operations within the same job (depends_on)
      - References one capability (capability_id; empty string means none)

    Field ordering in depends_on: sorted by operation_id for determinism.

    Immutable after construction.
    """

    id: str
    job_id: str
    goal: str
    operation_type: OperationType
    priority: OperationPriority
    status: OperationStatus
    sequence_index: int                              # position within the parent job
    capability_id: str                               # required capability (empty = none)
    depends_on: tuple[OperationDependency, ...]      # sorted by operation_id
    execution_ref: OperationExecutionReference | None  # gateway target (optional at planning time)


# ── OperationValidationError ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationValidationError:
    """A single structural validation error on an OperationDefinition.

    Immutable after construction.
    """

    field: str
    message: str


# ── OperationValidationResult ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationValidationResult:
    """Outcome of structural validation of an OperationDefinition.

    valid=True  → all structural checks passed; errors is empty
    valid=False → one or more errors; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[OperationValidationError, ...]


# ── OperationResult ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Immutable output of the Operation Engine's plan() method.

    Captures full planning outcome for a single OperationDefinition:

    blocking_operations → operation IDs from depends_on not yet in
                          completed_op_ids; these block execution
    cycle_detected      → True if a dependency cycle was found in the
                          full operation set passed to plan(); causes FAILED
    validation_result   → structural correctness of this operation

    Immutable after construction.
    """

    operation_id: str
    status: OperationStatus
    blocking_operations: tuple[str, ...]
    cycle_detected: bool
    validation_result: OperationValidationResult


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    # legacy
    "BK_OPERATION_STATUSES",
    "InvalidTransitionError",
    "OPERATION_STATUSES",
    "OUTCOME_CLASSIFICATIONS",
    "Operation",
    "VALID_TRANSITIONS",
    "transition_operation",
    # Sprint 56
    "OperationDefinition",
    "OperationDependency",
    "OperationExecutionReference",
    "OperationPriority",
    "OperationResult",
    "OperationStatus",
    "OperationType",
    "OperationValidationError",
    "OperationValidationResult",
]
