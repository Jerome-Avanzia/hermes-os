"""Swarm — typed planning contracts for the Swarm Engine.

Sprint 57: A Swarm is a coordination pattern for multi-skill jobs.

Architecture position:
  A Swarm is NOT an architectural layer. It is an execution strategy the
  Conductor applies when a job's operations span multiple skills that need
  shared context.

  The execution hierarchy is: Goal → Mission → Job → Operation → Gateway.
  A Swarm sits alongside this hierarchy as a coordination mode, not inside it.
  Swarm definitions reference operations owned by a Job; they do not own them.

Swarm never executes:
  All types are frozen planning contracts. The Swarm Engine computes execution
  order and produces immutable plans. Execution belongs exclusively to the
  Execution Gateway.

Context model (from architecture):
  The SwarmContext is append-only. Each completed operation appends its output.
  Subsequent operations receive the accumulated context alongside their own
  skill context. The Conductor merges the swarm context into each operation's
  ContextPackage before entering the reasoning pipeline.

Formation rules (from architecture):
  Single-skill job             → no swarm
  Multi-skill, independent ops → no swarm
  Multi-skill, cross-skill deps → swarm required
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── Enums ─────────────────────────────────────────────────────────────────────


class SwarmStrategy(enum.Enum):
    """How the Swarm shares context between member operations.

    SwarmStrategy describes the CONTEXT-PASSING pattern, not a scheduling or
    concurrency model. All swarm operations execute sequentially — the Swarm
    does not introduce parallelism (per architecture invariant).

    ACCUMULATE → each operation receives the full accumulated outputs of all
                 previous operations in the swarm (default append-only model)
    PIPELINE   → each operation receives only the immediately preceding
                 operation's output (sliding window, minimal context)
    BROADCAST  → all operations receive the same initial shared context;
                 no accumulation between operations
    """

    ACCUMULATE = "accumulate"   # default: append-only accumulation
    PIPELINE = "pipeline"       # sliding window: previous output only
    BROADCAST = "broadcast"     # fixed initial context; no accumulation


# ── SwarmMember ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmMember:
    """An operation that participates in a Swarm.

    References an operation by ID — the swarm does not own the operation.
    Ownership remains with the parent Job.

    role identifies this member's coordination purpose within the swarm
    (e.g. "producer", "consumer", "reviewer", "aggregator"). Empty string
    is permitted for operations with no specialised coordination role.

    sequence_index provides a deterministic tie-breaker when multiple members
    are at the same topological level (same dependency depth).

    Immutable after construction.
    """

    operation_id: str
    role: str = ""
    sequence_index: int = 0


# ── SwarmDependency ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class SwarmDependency:
    """A coordination constraint: from_operation_id must complete before
    to_operation_id can receive shared context and begin.

    Both operation IDs must reference SwarmMembers within the same
    SwarmDefinition. Cross-swarm dependencies are not supported.

    Self-dependencies (from == to) are a validation error.
    References to non-member operations are a coordination conflict.

    order=True: sorted by (from_operation_id, to_operation_id) for determinism.

    Immutable after construction.
    """

    from_operation_id: str
    to_operation_id: str


# ── SwarmDefinition ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmDefinition:
    """Immutable planning contract for a Swarm.

    Sprint 57: A SwarmDefinition declares which operations coordinate under a
    shared context, what context keys they share, and how context flows between
    them. It never executes. The Swarm Engine consumes it and produces a
    SwarmResult.

    Ownership:
      - Belongs to a single Job (job_id)
      - References operations by ID; does NOT own them
      - Operations are owned by the parent Job

    Field ordering:
      members      → sorted by (sequence_index, operation_id)
      dependencies → sorted by (from_operation_id, to_operation_id)
      context_keys → sorted lexicographically

    Immutable after construction.
    """

    id: str
    job_id: str
    strategy: SwarmStrategy
    members: tuple[SwarmMember, ...]
    dependencies: tuple[SwarmDependency, ...]
    context_keys: tuple[str, ...]


# ── SwarmValidationError ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmValidationError:
    """A single structural validation error on a SwarmDefinition.

    Immutable after construction.
    """

    field: str
    message: str


# ── SwarmValidationResult ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmValidationResult:
    """Outcome of structural validation of a SwarmDefinition.

    valid=True  → all structural checks passed; errors is empty
    valid=False → one or more errors; errors is non-empty

    Immutable after construction.
    """

    valid: bool
    errors: tuple[SwarmValidationError, ...]


# ── SwarmExecutionPlan ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmExecutionPlan:
    """Immutable execution plan produced by the Swarm Engine.

    execution_order → all member operation IDs in topological sequence.
                      The Conductor executes operations in this order,
                      injecting accumulated context between each step
                      according to the declared strategy.

    context_keys    → shared context keys managed by this swarm, sorted.
                      The Conductor uses these to scope what is accumulated
                      in the SwarmContext and injected into each operation.

    total_members   → total number of coordinated operations.

    Note: Operations always execute sequentially in a Swarm. The Swarm Engine
    does not produce a parallel schedule — it produces an ordering. The
    Execution Gateway's sequencing is the Swarm's execution model.

    Immutable after construction.
    """

    swarm_id: str
    strategy: SwarmStrategy
    execution_order: tuple[str, ...]
    context_keys: tuple[str, ...]
    total_members: int


# ── SwarmResult ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmResult:
    """Immutable output of the Swarm Engine's plan() method.

    execution_plan     → populated when validation passes and no conflict is
                         detected; None otherwise
    conflict_detected  → True if any coordination conflict was found (invalid
                         dependency references or cycles in the dep graph)
    conflict_details   → human-readable descriptions of detected conflicts,
                         sorted for determinism

    Immutable after construction.
    """

    swarm_id: str
    execution_plan: SwarmExecutionPlan | None
    conflict_detected: bool
    conflict_details: tuple[str, ...]
    validation_result: SwarmValidationResult


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    "SwarmDefinition",
    "SwarmDependency",
    "SwarmExecutionPlan",
    "SwarmMember",
    "SwarmResult",
    "SwarmStrategy",
    "SwarmValidationError",
    "SwarmValidationResult",
]
