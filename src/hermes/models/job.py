"""Job models — legacy execution record and Sprint 55 typed planning contracts.

The legacy Job class (ADR-0004) is preserved unchanged at the top of this
module. The Sprint 55 typed contracts below it are the new Job Engine layer.

Architecture separation:
  - Job (legacy)      : mutable execution record, used by the operation layer
  - JobDefinition     : frozen planning contract, used by the Job Engine
  - JobResult         : frozen engine output, consumed by the Conductor
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Legacy (ADR-0004) ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class Job:
    """A discrete execution run within an Operation (ADR-0004)."""

    id: str
    workspace_id: str
    operation_id: str
    status: str
    started_at: datetime
    finished_at: datetime
    completed_steps: list[str]
    generated_output: str | None
    created_at: datetime
    updated_at: datetime
    extra_fields: dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════════
# Sprint 55 — Job Engine typed contracts
#
# A JobDefinition is a PLANNING contract — it defines what work needs to
# happen, what capabilities are required, and how operations are ordered.
# It never executes. It never calls Skills. It never touches the Gateway.
# ══════════════════════════════════════════════════════════════════════════════


# ── Enums ─────────────────────────────────────────────────────────────────────


class JobStatus(enum.Enum):
    """Deterministic lifecycle state of a Job as determined by the Job Engine.

    Lifecycle:
      DEFINED   → initial state; job has been constructed but not planned
      VALIDATED → structural validation passed (no execution check yet)
      READY     → validation passed, capabilities resolved, dependencies clear
      BLOCKED   → validation passed, capabilities resolved, deps outstanding
      FAILED    → structural validation failed OR required capabilities missing
      COMPLETED → all operations finished (set by execution layer, not engine)
    """

    DEFINED = "defined"
    VALIDATED = "validated"    # structural validation passed; capability check not yet run
                               # reserved for future two-phase planning protocol
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"    # all operations finished; set by execution layer, not engine


class JobPriority(enum.Enum):
    """Execution priority for a Job.

    Used by the Conductor during planning to order competing jobs.
    Higher priority jobs are planned before lower priority jobs.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# ── JobCapabilityRequirement ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JobCapabilityRequirement:
    """A capability that a Job requires to execute.

    capability_id must match a SkillCapability.id registered in the
    SkillRegistry. The Job Engine resolves each requirement against the
    registry to determine whether the job can proceed.

    required=True  → missing capability → JobStatus.FAILED
    required=False → missing capability → allowed; execution may degrade

    Immutable after construction.
    """

    capability_id: str
    required: bool = True


# ── JobOperationReference ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class JobOperationReference:
    """A reference to an Operation within a Job, with its execution position.

    sequence_index defines the planned execution order (0-based, ascending).
    Duplicate sequence_index values within a job are a validation error.

    order=True sorts by (sequence_index, operation_id, capability_id) for
    deterministic ordering.

    Immutable after construction.
    """

    sequence_index: int     # primary sort key
    operation_id: str       # secondary sort key
    capability_id: str      # capability this operation requires


# ── JobDependency ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JobDependency:
    """A declared dependency on a prerequisite Job.

    The Job Engine uses job_id to determine whether a job is BLOCKED.
    If job_id is not in the completed_job_ids set at planning time, the
    dependent job is BLOCKED.

    Self-dependencies are a validation error.

    Immutable after construction.
    """

    job_id: str


# ── JobDefinition ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JobDefinition:
    """Immutable planning contract for a unit of work.

    Sprint 55: A JobDefinition defines what a job needs to do, what
    capabilities it requires, and how its operations are ordered.
    It never executes. The Job Engine consumes it and produces a JobResult.

    Field ordering within tuple fields:
      capability_requirements → sorted by capability_id
      operation_refs          → sorted by sequence_index
      depends_on              → sorted by job_id

    Immutable after construction.
    """

    id: str
    mission_id: str
    goal: str
    priority: JobPriority
    status: JobStatus
    capability_requirements: tuple[JobCapabilityRequirement, ...]
    operation_refs: tuple[JobOperationReference, ...]
    depends_on: tuple[JobDependency, ...]


# ── JobValidationError ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JobValidationError:
    """A single structural validation error on a JobDefinition.

    Immutable after construction.
    """

    field: str
    message: str


# ── JobValidationResult ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JobValidationResult:
    """Outcome of structural validation of a JobDefinition.

    valid=True  → all structural checks passed
    valid=False → one or more errors in errors tuple

    errors is empty when valid=True.
    errors is non-empty when valid=False.

    Immutable after construction.
    """

    valid: bool
    errors: tuple[JobValidationError, ...]


# ── JobResult ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class JobResult:
    """Immutable output of the Job Engine's plan() method.

    Captures the full planning outcome: structural validity, capability
    resolution, execution order, blocking state, and derived status.

    resolved_capabilities   → capability_ids matched in the SkillRegistry
                              (includes both required and optional)
    unresolved_capabilities → REQUIRED capability_ids with no matching skill
                              (these block execution; status=FAILED)
    execution_order         → operation_ids sorted by sequence_index
    blocking_jobs           → job_ids from depends_on not yet completed
                              (these block execution; status=BLOCKED)

    Immutable after construction.
    """

    job_id: str
    status: JobStatus
    resolved_capabilities: tuple[str, ...]
    unresolved_capabilities: tuple[str, ...]
    execution_order: tuple[str, ...]
    blocking_jobs: tuple[str, ...]
    validation_result: JobValidationResult


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    # legacy
    "Job",
    # Sprint 55
    "JobCapabilityRequirement",
    "JobDefinition",
    "JobDependency",
    "JobOperationReference",
    "JobPriority",
    "JobResult",
    "JobStatus",
    "JobValidationError",
    "JobValidationResult",
]
