"""Engineering Plan — typed contracts for multi-operation autonomous engineering.

Architecture position:
  EngineeringPlanner (kernel)
       ↓
  EngineeringPlan  ← this module
       ↓
  EngineeringCoordinator → EngineeringWorkflow

Phase 6 introduces multi-operation engineering plans. The Founder provides a
single goal; EngineeringPlanner decomposes it into an ordered set of
PlannedOperations; EngineeringCoordinator drives planning then execution;
EngineeringWorkflow executes each operation through the existing single-file
pipeline (generate → write → validate → run_tests → add), then performs a
single plan-level commit.

PlannedOperation expresses engineering intent — what file to change and why,
not how. The workflow translates intent into concrete filesystem operations.
The RepositoryManipulation gate validates intent against repository state.

All contracts are immutable after construction.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── PlannedOperation ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PlannedOperation:
    """Immutable description of a single planned file operation within an EngineeringPlan.

    operation_id  → unique identifier within the plan
    target        → repo-relative path to the file
    intent        → "create" | "modify" — planner-declared engineering intent;
                    NOT derived from filesystem state
    goal          → structured per-operation goal; fed directly into the generate
                    step as the task description
    depends_on    → operation_ids this depends on; always present; empty tuple
                    when no dependencies

    Immutable after construction.
    """

    operation_id: str
    target: str
    intent: str                       # "create" | "modify"
    goal: str
    depends_on: tuple[str, ...] = ()  # operation_ids of prerequisites


# ── EngineeringPlan ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EngineeringPlan:
    """Immutable ordered set of PlannedOperations decomposed from a single FounderGoal.

    plan_id     → unique identifier for this plan
    goal_id     → parent FounderGoal.goal_id
    confidence  → "high" | "ambiguous"
    basis       → one-sentence rationale for the decomposition
    operations  → ordered tuple of PlannedOperations; list position used as
                  sequence_index for tie-breaking during execution
    candidates  → non-empty only when confidence="ambiguous"; plausible file paths

    Immutable after construction.
    """

    plan_id: str
    goal_id: str
    confidence: str                          # "high" | "ambiguous"
    basis: str
    operations: tuple[PlannedOperation, ...]
    candidates: tuple[str, ...] = ()         # populated when confidence="ambiguous"


# ── EngineeringPlanResult ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EngineeringPlanResult:
    """Immutable result of an EngineeringPlanner.plan() call.

    success  → True iff the LLM call, JSON parse, and validation all succeeded
    plan     → None on LLM failure or JSON parse failure; populated (even when
               confidence="ambiguous") on successful LLM call + parse
    error    → description of the failure; None when success=True

    Immutable after construction.
    """

    success: bool
    plan: EngineeringPlan | None
    error: str | None


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "PlannedOperation",
    "EngineeringPlan",
    "EngineeringPlanResult",
]
