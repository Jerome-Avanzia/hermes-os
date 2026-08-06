"""Operation Engine — deterministic planning for Operations.

Sprint 56: The Operation Engine validates OperationDefinitions, detects
dependency cycles, computes blocking state, and determines topological
execution order within a Job's operation set.

Invariants:
  - The engine never executes operations.
  - The engine never calls Skills.
  - The engine never calls the Runtime.
  - The engine never calls the Execution Gateway.
  - The engine never invokes providers.
  - The engine never performs filesystem work.
  - The engine never performs network operations.
  - The engine is provider-independent.
  - The engine is deterministic: same inputs → same outputs.

The engine consumes:
  - OperationDefinition (typed planning contracts)
  - frozenset[str] of completed operation IDs (blocking state check)

The engine produces:
  - OperationValidationResult (structural correctness per operation)
  - OperationResult (full planning outcome per operation)
  - tuple[OperationResult, ...] (batch planning across a job's operations)
  - tuple[str, ...] (topological execution order)
  - bool (cycle detection)

Cycle detection uses iterative DFS to avoid Python recursion limits.
Topological ordering uses Kahn's algorithm with deterministic tie-breaking
on (sequence_index, operation_id).
"""

from __future__ import annotations

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


class OperationEngine:
    """Deterministic planning engine for OperationDefinitions.

    Stateless: holds no mutable state. Every method is a pure function
    of its inputs.

    Usage::

        engine = OperationEngine()

        op = engine.build_operation(
            id="op-draft",
            job_id="job-001",
            goal="Draft the product announcement",
            operation_type=OperationType.LLM,
            capability_id="copywriting",
        )

        result = engine.plan(op, all_ops=[op], completed_op_ids=frozenset())
        # result.status → OperationStatus.READY
    """

    # ── Construction ──────────────────────────────────────────────────────────

    def build_operation(
        self,
        *,
        id: str,
        job_id: str,
        goal: str,
        operation_type: OperationType = OperationType.GENERIC,
        priority: OperationPriority = OperationPriority.NORMAL,
        sequence_index: int = 0,
        capability_id: str = "",
        depends_on: list[OperationDependency] | None = None,
        execution_ref: OperationExecutionReference | None = None,
    ) -> OperationDefinition:
        """Build an immutable OperationDefinition with status DEFINED.

        Normalises depends_on to sorted order by operation_id for determinism.

        Args:
            id: Unique operation identifier within the job.
            job_id: Parent job identifier.
            goal: Human-readable description of what this operation accomplishes.
            operation_type: Gateway adapter category (default GENERIC).
            priority: Execution priority (default NORMAL).
            sequence_index: Position within the parent job (default 0).
            capability_id: Required capability ID (empty string = none).
            depends_on: Prerequisite operations within the same job.
            execution_ref: Target gateway adapter and action (optional at plan time).

        Returns:
            OperationDefinition with status=DEFINED.
        """
        sorted_deps = tuple(
            sorted(depends_on or [], key=lambda d: d.operation_id)
        )
        return OperationDefinition(
            id=id,
            job_id=job_id,
            goal=goal,
            operation_type=operation_type,
            priority=priority,
            status=OperationStatus.DEFINED,
            sequence_index=sequence_index,
            capability_id=capability_id,
            depends_on=sorted_deps,
            execution_ref=execution_ref,
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self, op: OperationDefinition) -> OperationValidationResult:
        """Deterministically validate the structure of an OperationDefinition.

        Checks:
          1. id must be non-empty after stripping whitespace
          2. job_id must be non-empty after stripping whitespace
          3. goal must be non-empty after stripping whitespace
          4. sequence_index must be >= 0
          5. depends_on: no empty operation_id, no self-dependency, no duplicates
          6. execution_ref: if present, adapter_id and action_id both non-empty

        Does NOT validate capability_id against any registry. That is the
        responsibility of the Job Engine.

        Returns:
            OperationValidationResult with valid=True and empty errors if all
            checks pass; valid=False with one or more errors otherwise.
        """
        errors: list[OperationValidationError] = []

        # ── id ────────────────────────────────────────────────────────────
        if not op.id.strip():
            errors.append(OperationValidationError(
                field="id",
                message="Operation ID must not be empty",
            ))

        # ── job_id ────────────────────────────────────────────────────────
        if not op.job_id.strip():
            errors.append(OperationValidationError(
                field="job_id",
                message="Job ID must not be empty",
            ))

        # ── goal ──────────────────────────────────────────────────────────
        if not op.goal.strip():
            errors.append(OperationValidationError(
                field="goal",
                message="Operation goal must not be empty",
            ))

        # ── sequence_index ────────────────────────────────────────────────
        if op.sequence_index < 0:
            errors.append(OperationValidationError(
                field="sequence_index",
                message=f"sequence_index must be >= 0, got {op.sequence_index}",
            ))

        # ── depends_on ────────────────────────────────────────────────────
        seen_deps: set[str] = set()
        for dep in op.depends_on:
            if not dep.operation_id.strip():
                errors.append(OperationValidationError(
                    field="depends_on",
                    message="Dependency operation_id must not be empty",
                ))
            elif dep.operation_id == op.id:
                errors.append(OperationValidationError(
                    field="depends_on",
                    message=f"Operation {op.id!r} cannot depend on itself",
                ))
            elif dep.operation_id in seen_deps:
                errors.append(OperationValidationError(
                    field="depends_on",
                    message=f"Duplicate dependency operation_id: {dep.operation_id!r}",
                ))
            else:
                seen_deps.add(dep.operation_id)

        # ── execution_ref ─────────────────────────────────────────────────
        if op.execution_ref is not None:
            if not op.execution_ref.adapter_id.strip():
                errors.append(OperationValidationError(
                    field="execution_ref.adapter_id",
                    message="execution_ref.adapter_id must not be empty",
                ))
            if not op.execution_ref.action_id.strip():
                errors.append(OperationValidationError(
                    field="execution_ref.action_id",
                    message="execution_ref.action_id must not be empty",
                ))

        return OperationValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Cycle detection ───────────────────────────────────────────────────────

    def detect_cycle(self, ops: list[OperationDefinition]) -> bool:
        """Return True if the dependency graph of ops contains a cycle.

        Uses iterative DFS (avoids Python recursion limit for deep graphs).
        Only edges between operations within the provided set are considered;
        references to operations outside the set are ignored.

        A cycle means at least one operation (directly or transitively) depends
        on itself, making a valid execution order impossible.

        Args:
            ops: All OperationDefinitions to inspect (typically a full job's
                 operation set).

        Returns:
            True if a cycle exists; False otherwise.
        """
        op_ids = {op.id for op in ops}
        # Build adjacency: node → list of nodes it depends on (within set)
        graph: dict[str, list[str]] = {
            op.id: sorted(
                dep.operation_id
                for dep in op.depends_on
                if dep.operation_id in op_ids
            )
            for op in ops
        }

        visited: set[str] = set()

        for start in sorted(graph):
            if start in visited:
                continue
            # Iterative DFS with explicit stack tracking the current path
            stack: list[tuple[str, int]] = [(start, 0)]
            in_path: set[str] = set()
            path_iters: dict[str, int] = {}

            while stack:
                node, idx = stack[-1]

                if idx == 0:
                    if node in in_path:
                        return True
                    in_path.add(node)

                neighbors = graph.get(node, [])
                if idx < len(neighbors):
                    stack[-1] = (node, idx + 1)
                    neighbor = neighbors[idx]
                    if neighbor in in_path:
                        return True
                    if neighbor not in visited:
                        stack.append((neighbor, 0))
                else:
                    in_path.discard(node)
                    visited.add(node)
                    stack.pop()

        return False

    # ── Blocking state ────────────────────────────────────────────────────────

    def is_blocked(
        self,
        op: OperationDefinition,
        completed_op_ids: frozenset[str],
    ) -> bool:
        """Return True if any prerequisite operation has not yet completed."""
        return any(dep.operation_id not in completed_op_ids for dep in op.depends_on)

    # ── Topological ordering ──────────────────────────────────────────────────

    def determine_execution_order(
        self, ops: list[OperationDefinition]
    ) -> tuple[str, ...]:
        """Return operation IDs in a valid topological execution order.

        Uses Kahn's algorithm. Within each topological level (operations that
        could execute concurrently), tie-breaking is by (sequence_index,
        operation_id) for determinism.

        Only edges between operations within the provided set are considered.
        If ops contains a cycle, the result is partial (cycled nodes are
        omitted). Callers should run detect_cycle() first.

        Args:
            ops: All OperationDefinitions to order.

        Returns:
            Tuple of operation IDs in a valid execution order. Partial if a
            cycle exists.
        """
        if not ops:
            return ()

        op_ids = {op.id for op in ops}
        op_map = {op.id: op for op in ops}

        # Build in-degree and reverse adjacency (node → list of nodes that depend on it)
        in_degree: dict[str, int] = {op.id: 0 for op in ops}
        reverse_adj: dict[str, list[str]] = {op.id: [] for op in ops}

        for op in ops:
            for dep in op.depends_on:
                if dep.operation_id in op_ids:
                    in_degree[op.id] += 1
                    reverse_adj[dep.operation_id].append(op.id)

        # Sort reverse adjacency lists for determinism
        for adj_list in reverse_adj.values():
            adj_list.sort()

        # Initial ready set: ops with no prerequisites, sorted by (sequence_index, id)
        def sort_key(oid: str) -> tuple[int, str]:
            return (op_map[oid].sequence_index, oid)

        ready = sorted(
            [oid for oid, deg in in_degree.items() if deg == 0],
            key=sort_key,
        )

        result: list[str] = []
        while ready:
            current_id = ready.pop(0)
            result.append(current_id)

            newly_ready: list[str] = []
            for dep_id in reverse_adj[current_id]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    newly_ready.append(dep_id)

            newly_ready.sort(key=sort_key)
            # Merge into ready in sorted order
            ready.extend(newly_ready)
            ready.sort(key=sort_key)

        return tuple(result)

    # ── Planning ──────────────────────────────────────────────────────────────

    def plan(
        self,
        op: OperationDefinition,
        all_ops: list[OperationDefinition],
        completed_op_ids: frozenset[str] | None = None,
    ) -> OperationResult:
        """Run full deterministic planning for a single OperationDefinition.

        Steps (in order):
          1. Structural validation via validate()
             → If invalid: return OperationResult(status=FAILED)
          2. Cycle detection across all_ops
             → If cycle detected: status=FAILED, cycle_detected=True
          3. Blocking state check against completed_op_ids
             → If blocked: status=BLOCKED
          4. Otherwise: status=READY

        Args:
            op: The OperationDefinition to plan.
            all_ops: All operations in the same job (needed for cycle detection).
                     Must include op itself.
            completed_op_ids: Set of operation IDs that have already completed.
                              Defaults to empty frozenset.

        Returns:
            OperationResult capturing full planning outcome for op.
        """
        completed = completed_op_ids if completed_op_ids is not None else frozenset()

        # Step 1: structural validation
        validation = self.validate(op)
        if not validation.valid:
            return OperationResult(
                operation_id=op.id,
                status=OperationStatus.FAILED,
                blocking_operations=(),
                cycle_detected=False,
                validation_result=validation,
            )

        # Step 2: cycle detection
        cycle = self.detect_cycle(all_ops)
        if cycle:
            return OperationResult(
                operation_id=op.id,
                status=OperationStatus.FAILED,
                blocking_operations=(),
                cycle_detected=True,
                validation_result=validation,
            )

        # Step 3: blocking state
        blocking = tuple(sorted(
            dep.operation_id
            for dep in op.depends_on
            if dep.operation_id not in completed
        ))

        # Step 4: derive status
        status = OperationStatus.BLOCKED if blocking else OperationStatus.READY

        return OperationResult(
            operation_id=op.id,
            status=status,
            blocking_operations=blocking,
            cycle_detected=False,
            validation_result=validation,
        )

    def plan_all(
        self,
        ops: list[OperationDefinition],
        completed_op_ids: frozenset[str] | None = None,
    ) -> tuple[OperationResult, ...]:
        """Run planning for every OperationDefinition in a job's operation set.

        Cycle detection runs once across the full set (not per operation).
        Results parallel the input list: result[i] corresponds to ops[i].

        Args:
            ops: All OperationDefinitions in the job (order preserved in output).
            completed_op_ids: Set of operation IDs that have already completed.

        Returns:
            Tuple of OperationResult, one per input operation, in input order.
        """
        completed = completed_op_ids if completed_op_ids is not None else frozenset()

        # Detect cycles once for the whole set
        cycle = self.detect_cycle(ops)

        results: list[OperationResult] = []
        for op in ops:
            validation = self.validate(op)

            if not validation.valid:
                results.append(OperationResult(
                    operation_id=op.id,
                    status=OperationStatus.FAILED,
                    blocking_operations=(),
                    cycle_detected=cycle,
                    validation_result=validation,
                ))
                continue

            if cycle:
                results.append(OperationResult(
                    operation_id=op.id,
                    status=OperationStatus.FAILED,
                    blocking_operations=(),
                    cycle_detected=True,
                    validation_result=validation,
                ))
                continue

            blocking = tuple(sorted(
                dep.operation_id
                for dep in op.depends_on
                if dep.operation_id not in completed
            ))
            status = OperationStatus.BLOCKED if blocking else OperationStatus.READY

            results.append(OperationResult(
                operation_id=op.id,
                status=status,
                blocking_operations=blocking,
                cycle_detected=False,
                validation_result=validation,
            ))

        return tuple(results)
