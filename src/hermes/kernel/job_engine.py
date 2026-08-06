"""Job Engine — deterministic planning for Jobs.

Sprint 55: The Job Engine validates JobDefinitions, resolves capability
requirements against the SkillRegistry, determines operation execution order,
and computes blocking state from job dependencies.

Invariants:
  - The engine never executes operations.
  - The engine never calls Skills.
  - The engine never calls the Gateway.
  - The engine never invokes LLMs.
  - The engine never performs network or filesystem writes.
  - The engine is provider-independent.
  - The engine is deterministic: same inputs → same outputs.

The engine consumes:
  - JobDefinition (typed planning contract)
  - SkillRegistry (capability resolution source)
  - frozenset[str] of completed job IDs (dependency satisfaction check)

The engine produces:
  - JobValidationResult (structural correctness)
  - JobResult (full planning outcome including status, resolved capabilities,
    execution order, blocking jobs)
"""

from __future__ import annotations

from hermes.kernel.skill_registry import SkillRegistry
from hermes.models.job import (
    JobCapabilityRequirement,
    JobDefinition,
    JobDependency,
    JobOperationReference,
    JobPriority,
    JobResult,
    JobStatus,
    JobValidationError,
    JobValidationResult,
)


class JobEngine:
    """Deterministic planning engine for JobDefinitions.

    Accepts a SkillRegistry at construction time. The registry is the sole
    external dependency and is read-only within the engine.

    Usage::

        engine = JobEngine(registry=skill_registry)

        job = engine.build_job(
            id="job-copywriting-001",
            mission_id="mission-launch",
            goal="Draft the product announcement",
            capability_requirements=[
                JobCapabilityRequirement(capability_id="copywriting"),
            ],
            operation_refs=[
                JobOperationReference(sequence_index=0, operation_id="op-draft",
                                      capability_id="copywriting"),
            ],
        )

        result = engine.plan(job, completed_job_ids=frozenset())
        # result.status → JobStatus.READY (if copywriting skill is registered)
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    # ── Construction ──────────────────────────────────────────────────────────

    def build_job(
        self,
        *,
        id: str,
        mission_id: str,
        goal: str,
        priority: JobPriority = JobPriority.NORMAL,
        capability_requirements: list[JobCapabilityRequirement] | None = None,
        operation_refs: list[JobOperationReference] | None = None,
        depends_on: list[JobDependency] | None = None,
    ) -> JobDefinition:
        """Build an immutable JobDefinition with status DEFINED.

        Normalises tuple field ordering for determinism:
          - capability_requirements sorted by capability_id
          - operation_refs sorted by (sequence_index, operation_id, capability_id)
          - depends_on sorted by job_id

        Args:
            id: Unique job identifier.
            mission_id: Parent mission identifier.
            goal: Human-readable description of what this job accomplishes.
            priority: Execution priority (default NORMAL).
            capability_requirements: Skills needed to execute this job.
            operation_refs: Operations that constitute this job's work.
            depends_on: Prerequisite jobs that must complete first.

        Returns:
            JobDefinition with status=DEFINED.
        """
        sorted_caps = tuple(
            sorted(capability_requirements or [], key=lambda r: r.capability_id)
        )
        sorted_ops = tuple(sorted(operation_refs or []))  # order=True on dataclass
        sorted_deps = tuple(
            sorted(depends_on or [], key=lambda d: d.job_id)
        )
        return JobDefinition(
            id=id,
            mission_id=mission_id,
            goal=goal,
            priority=priority,
            status=JobStatus.DEFINED,
            capability_requirements=sorted_caps,
            operation_refs=sorted_ops,
            depends_on=sorted_deps,
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self, job: JobDefinition) -> JobValidationResult:
        """Deterministically validate the structure of a JobDefinition.

        Checks:
          1. id must be non-empty after stripping whitespace
          2. goal must be non-empty after stripping whitespace
          3. capability_requirements: no empty capability_id, no duplicates
          4. operation_refs: no empty operation_id, no duplicates;
             no negative sequence_index, no duplicate sequence_index
          5. depends_on: no empty job_id, no self-dependency, no duplicates

        Does NOT check capability availability against the registry — that is
        the responsibility of plan() and resolve_capabilities().

        Returns:
            JobValidationResult with valid=True and empty errors if all
            checks pass; valid=False with one or more errors otherwise.
        """
        errors: list[JobValidationError] = []

        # ── id ────────────────────────────────────────────────────────────
        if not job.id.strip():
            errors.append(JobValidationError(
                field="id",
                message="Job ID must not be empty",
            ))

        # ── goal ──────────────────────────────────────────────────────────
        if not job.goal.strip():
            errors.append(JobValidationError(
                field="goal",
                message="Job goal must not be empty",
            ))

        # ── capability_requirements ────────────────────────────────────────
        seen_caps: set[str] = set()
        for req in job.capability_requirements:
            if not req.capability_id.strip():
                errors.append(JobValidationError(
                    field="capability_requirements",
                    message="Capability ID must not be empty",
                ))
            elif req.capability_id in seen_caps:
                errors.append(JobValidationError(
                    field="capability_requirements",
                    message=f"Duplicate capability_id: {req.capability_id!r}",
                ))
            else:
                seen_caps.add(req.capability_id)

        # ── operation_refs ─────────────────────────────────────────────────
        seen_op_ids: set[str] = set()
        seen_indices: set[int] = set()
        for ref in job.operation_refs:
            if not ref.operation_id.strip():
                errors.append(JobValidationError(
                    field="operation_refs",
                    message="Operation ID must not be empty",
                ))
            elif ref.operation_id in seen_op_ids:
                errors.append(JobValidationError(
                    field="operation_refs",
                    message=f"Duplicate operation_id: {ref.operation_id!r}",
                ))
            else:
                seen_op_ids.add(ref.operation_id)

            if ref.sequence_index < 0:
                errors.append(JobValidationError(
                    field="operation_refs",
                    message=f"Negative sequence_index: {ref.sequence_index}",
                ))
            elif ref.sequence_index in seen_indices:
                errors.append(JobValidationError(
                    field="operation_refs",
                    message=f"Duplicate sequence_index: {ref.sequence_index}",
                ))
            else:
                seen_indices.add(ref.sequence_index)

        # ── depends_on ─────────────────────────────────────────────────────
        seen_deps: set[str] = set()
        for dep in job.depends_on:
            if not dep.job_id.strip():
                errors.append(JobValidationError(
                    field="depends_on",
                    message="Dependency job_id must not be empty",
                ))
            elif dep.job_id == job.id:
                errors.append(JobValidationError(
                    field="depends_on",
                    message=f"Job {job.id!r} cannot depend on itself",
                ))
            elif dep.job_id in seen_deps:
                errors.append(JobValidationError(
                    field="depends_on",
                    message=f"Duplicate dependency job_id: {dep.job_id!r}",
                ))
            else:
                seen_deps.add(dep.job_id)

        return JobValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Capability resolution ─────────────────────────────────────────────────

    def resolve_capabilities(self, job: JobDefinition) -> tuple[str, ...]:
        """Return sorted capability IDs matched in the SkillRegistry.

        Includes both required and optional capabilities that have at least
        one registered skill. Sorted lexicographically.
        """
        return tuple(sorted(
            req.capability_id
            for req in job.capability_requirements
            if self._registry.find_by_capability(req.capability_id)
        ))

    def resolve_unresolved_capabilities(self, job: JobDefinition) -> tuple[str, ...]:
        """Return sorted REQUIRED capability IDs with no matching skill.

        Optional capabilities (required=False) are excluded from this result.
        Sorted lexicographically.
        """
        return tuple(sorted(
            req.capability_id
            for req in job.capability_requirements
            if req.required and not self._registry.find_by_capability(req.capability_id)
        ))

    # ── Execution order ───────────────────────────────────────────────────────

    def determine_execution_order(self, job: JobDefinition) -> tuple[str, ...]:
        """Return operation IDs in ascending sequence_index order.

        Ties within sequence_index are broken by operation_id (lexicographic),
        which is guaranteed by JobOperationReference's order=True dataclass.
        """
        return tuple(ref.operation_id for ref in sorted(job.operation_refs))

    # ── Dependency state ──────────────────────────────────────────────────────

    def is_blocked(
        self,
        job: JobDefinition,
        completed_job_ids: frozenset[str],
    ) -> bool:
        """Return True if any prerequisite job has not yet completed."""
        return any(dep.job_id not in completed_job_ids for dep in job.depends_on)

    # ── Planning ──────────────────────────────────────────────────────────────

    def plan(
        self,
        job: JobDefinition,
        completed_job_ids: frozenset[str] | None = None,
    ) -> JobResult:
        """Run full deterministic planning for a JobDefinition.

        Steps (in order):
          1. Structural validation via validate()
             → If invalid: return JobResult(status=FAILED)
          2. Capability resolution against SkillRegistry
             → If required capabilities are missing: status=FAILED
          3. Dependency check against completed_job_ids
             → If prerequisites outstanding: status=BLOCKED
          4. Otherwise: status=READY

        Args:
            job: The JobDefinition to plan.
            completed_job_ids: Set of job IDs that have already completed.
                Defaults to empty frozenset (no completed jobs).

        Returns:
            JobResult capturing full planning outcome.
        """
        completed = completed_job_ids if completed_job_ids is not None else frozenset()

        # Step 1: structural validation
        validation = self.validate(job)
        if not validation.valid:
            return JobResult(
                job_id=job.id,
                status=JobStatus.FAILED,
                resolved_capabilities=(),
                unresolved_capabilities=(),
                execution_order=(),
                blocking_jobs=(),
                validation_result=validation,
            )

        # Step 2: capability resolution
        resolved = self.resolve_capabilities(job)
        unresolved = self.resolve_unresolved_capabilities(job)
        execution_order = self.determine_execution_order(job)

        # Step 3: blocking state
        blocking_jobs = tuple(sorted(
            dep.job_id
            for dep in job.depends_on
            if dep.job_id not in completed
        ))

        # Step 4: derive status
        if unresolved:
            status = JobStatus.FAILED
        elif blocking_jobs:
            status = JobStatus.BLOCKED
        else:
            status = JobStatus.READY

        return JobResult(
            job_id=job.id,
            status=status,
            resolved_capabilities=resolved,
            unresolved_capabilities=unresolved,
            execution_order=execution_order,
            blocking_jobs=blocking_jobs,
            validation_result=validation,
        )
