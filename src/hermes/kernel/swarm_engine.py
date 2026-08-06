"""Swarm Engine — deterministic coordination planning for Swarms.

Sprint 57: The Swarm Engine validates SwarmDefinitions, detects coordination
conflicts (invalid dependency references, dependency cycles), computes
topological execution order, and produces immutable SwarmExecutionPlans.

Architecture invariants:
  - The engine never executes operations.
  - The engine never calls the Runtime.
  - The engine never calls Skills.
  - The engine never calls the Execution Gateway.
  - The engine never invokes providers.
  - The engine never performs filesystem work.
  - The engine never performs network operations.
  - The engine is provider-independent.
  - The engine is deterministic: same inputs → same outputs.

Swarm as execution strategy (not architectural layer):
  The Swarm Engine is stateless. It holds no registry, no capability index,
  and no reference to any layer in the execution hierarchy. It receives a
  SwarmDefinition — a pure data contract — and returns a SwarmResult.
  This reflects the architectural truth: a Swarm is a coordination mode
  applied ON TOP of an existing job structure, not a layer within it.
  The Swarm contributes coordination metadata (sequencing and context-passing)
  while lifecycle ownership and execution remain unchanged.

Execution model:
  Operations in a swarm always execute sequentially (per architecture).
  The engine produces an execution ORDER, not a parallel schedule.
  Topological sort uses Kahn's algorithm with (sequence_index, operation_id)
  tie-breaking for full determinism.

Cycle detection uses iterative DFS to avoid Python recursion limits.
"""

from __future__ import annotations

from hermes.models.swarm import (
    SwarmDefinition,
    SwarmDependency,
    SwarmExecutionPlan,
    SwarmMember,
    SwarmResult,
    SwarmStrategy,
    SwarmValidationError,
    SwarmValidationResult,
)


class SwarmEngine:
    """Deterministic coordination planning engine for SwarmDefinitions.

    Stateless: holds no mutable state. Every method is a pure function of
    its inputs, consistent with the Swarm's role as a strategy, not a layer.

    Usage::

        engine = SwarmEngine()

        swarm = engine.build_swarm(
            id="swarm-checkout",
            job_id="job-001",
            strategy=SwarmStrategy.ACCUMULATE,
            members=[
                SwarmMember(operation_id="op-frontend", role="producer", sequence_index=0),
                SwarmMember(operation_id="op-backend",  role="producer", sequence_index=1),
                SwarmMember(operation_id="op-copy",     role="consumer", sequence_index=2),
            ],
            dependencies=[
                SwarmDependency(from_operation_id="op-frontend", to_operation_id="op-copy"),
                SwarmDependency(from_operation_id="op-backend",  to_operation_id="op-copy"),
            ],
            context_keys=["page_structure", "api_spec"],
        )

        result = engine.plan(swarm)
        # result.execution_plan.execution_order → ("op-frontend", "op-backend", "op-copy")
    """

    # ── Construction ──────────────────────────────────────────────────────────

    def build_swarm(
        self,
        *,
        id: str,
        job_id: str,
        strategy: SwarmStrategy = SwarmStrategy.ACCUMULATE,
        members: list[SwarmMember] | None = None,
        dependencies: list[SwarmDependency] | None = None,
        context_keys: list[str] | None = None,
    ) -> SwarmDefinition:
        """Build an immutable SwarmDefinition.

        Normalises field ordering for determinism:
          members      → sorted by (sequence_index, operation_id)
          dependencies → sorted by (from_operation_id, to_operation_id)
          context_keys → sorted lexicographically

        Args:
            id: Unique swarm identifier.
            job_id: Parent job identifier.
            strategy: Context-passing strategy (default ACCUMULATE).
            members: Operations coordinated by this swarm.
            dependencies: Coordination constraints between members.
            context_keys: Shared context keys managed by this swarm.

        Returns:
            SwarmDefinition with all tuple fields in deterministic order.
        """
        sorted_members = tuple(
            sorted(members or [], key=lambda m: (m.sequence_index, m.operation_id))
        )
        sorted_deps = tuple(sorted(dependencies or []))  # order=True on dataclass
        sorted_keys = tuple(sorted(context_keys or []))

        return SwarmDefinition(
            id=id,
            job_id=job_id,
            strategy=strategy,
            members=sorted_members,
            dependencies=sorted_deps,
            context_keys=sorted_keys,
        )

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self, swarm: SwarmDefinition) -> SwarmValidationResult:
        """Deterministically validate the structure of a SwarmDefinition.

        Checks:
          1. id must be non-empty after stripping whitespace
          2. job_id must be non-empty after stripping whitespace
          3. At least one member must be declared
          4. members: no empty operation_ids, no duplicates
          5. members: sequence_index >= 0 for all members
          6. context_keys: no empty keys, no duplicates
          7. dependencies: no empty from/to operation_ids
          8. dependencies: no self-dependencies (from == to)
          9. dependencies: no duplicate (from, to) pairs

        Does NOT check whether dependency endpoints are registered members —
        that is handled by detect_conflict().

        Returns:
            SwarmValidationResult with valid=True and empty errors if all
            checks pass; valid=False with one or more errors otherwise.
        """
        errors: list[SwarmValidationError] = []

        # ── id ────────────────────────────────────────────────────────────
        if not swarm.id.strip():
            errors.append(SwarmValidationError(
                field="id",
                message="Swarm ID must not be empty",
            ))

        # ── job_id ────────────────────────────────────────────────────────
        if not swarm.job_id.strip():
            errors.append(SwarmValidationError(
                field="job_id",
                message="Job ID must not be empty",
            ))

        # ── members ───────────────────────────────────────────────────────
        if not swarm.members:
            errors.append(SwarmValidationError(
                field="members",
                message="Swarm must have at least one member",
            ))

        seen_member_ids: set[str] = set()
        for member in swarm.members:
            if not member.operation_id.strip():
                errors.append(SwarmValidationError(
                    field="members",
                    message="Member operation_id must not be empty",
                ))
            elif member.operation_id in seen_member_ids:
                errors.append(SwarmValidationError(
                    field="members",
                    message=f"Duplicate member operation_id: {member.operation_id!r}",
                ))
            else:
                seen_member_ids.add(member.operation_id)

            if member.sequence_index < 0:
                errors.append(SwarmValidationError(
                    field="members",
                    message=(
                        f"Member {member.operation_id!r} has negative "
                        f"sequence_index: {member.sequence_index}"
                    ),
                ))

        # ── context_keys ──────────────────────────────────────────────────
        seen_keys: set[str] = set()
        for key in swarm.context_keys:
            if not key.strip():
                errors.append(SwarmValidationError(
                    field="context_keys",
                    message="Context key must not be empty",
                ))
            elif key in seen_keys:
                errors.append(SwarmValidationError(
                    field="context_keys",
                    message=f"Duplicate context key: {key!r}",
                ))
            else:
                seen_keys.add(key)

        # ── dependencies ──────────────────────────────────────────────────
        seen_dep_pairs: set[tuple[str, str]] = set()
        for dep in swarm.dependencies:
            if not dep.from_operation_id.strip():
                errors.append(SwarmValidationError(
                    field="dependencies",
                    message="Dependency from_operation_id must not be empty",
                ))
            if not dep.to_operation_id.strip():
                errors.append(SwarmValidationError(
                    field="dependencies",
                    message="Dependency to_operation_id must not be empty",
                ))
            if dep.from_operation_id and dep.to_operation_id:
                if dep.from_operation_id == dep.to_operation_id:
                    errors.append(SwarmValidationError(
                        field="dependencies",
                        message=(
                            f"Operation {dep.from_operation_id!r} cannot "
                            f"depend on itself"
                        ),
                    ))
                pair = (dep.from_operation_id, dep.to_operation_id)
                if pair in seen_dep_pairs:
                    errors.append(SwarmValidationError(
                        field="dependencies",
                        message=(
                            f"Duplicate dependency: "
                            f"{dep.from_operation_id!r} → {dep.to_operation_id!r}"
                        ),
                    ))
                else:
                    seen_dep_pairs.add(pair)

        return SwarmValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Conflict detection ────────────────────────────────────────────────────

    def detect_conflict(self, swarm: SwarmDefinition) -> tuple[bool, tuple[str, ...]]:
        """Detect coordination conflicts in a SwarmDefinition.

        A coordination conflict is any condition that makes a valid execution
        order impossible:

          1. Invalid reference: a dependency's from_operation_id or
             to_operation_id is not in the swarm's member set.
          2. Cycle: the dependency graph contains a directed cycle, making
             topological ordering impossible.

        Uses iterative DFS for cycle detection.

        Args:
            swarm: The SwarmDefinition to inspect.

        Returns:
            (conflict_found, conflict_details) where conflict_details is a
            sorted tuple of human-readable conflict descriptions.
        """
        member_ids = {m.operation_id for m in swarm.members}
        details: list[str] = []

        # Check 1: invalid dependency references
        for dep in swarm.dependencies:
            if dep.from_operation_id and dep.from_operation_id not in member_ids:
                details.append(
                    f"Dependency references non-member: "
                    f"{dep.from_operation_id!r} is not in swarm members"
                )
            if dep.to_operation_id and dep.to_operation_id not in member_ids:
                details.append(
                    f"Dependency references non-member: "
                    f"{dep.to_operation_id!r} is not in swarm members"
                )

        # Check 2: cycle detection (iterative DFS)
        graph: dict[str, list[str]] = {op_id: [] for op_id in member_ids}
        for dep in swarm.dependencies:
            if dep.from_operation_id in graph and dep.to_operation_id in member_ids:
                graph[dep.from_operation_id].append(dep.to_operation_id)

        for adj in graph.values():
            adj.sort()

        visited: set[str] = set()

        for start in sorted(graph):
            if start in visited:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            in_path: set[str] = set()

            while stack:
                node, idx = stack[-1]

                if idx == 0:
                    if node in in_path:
                        details.append(
                            f"Dependency cycle detected involving operation: {node!r}"
                        )
                        # Remove to avoid re-reporting the same cycle
                        stack.pop()
                        in_path.discard(node)
                        continue
                    in_path.add(node)

                neighbors = graph.get(node, [])
                if idx < len(neighbors):
                    stack[-1] = (node, idx + 1)
                    neighbor = neighbors[idx]
                    if neighbor in in_path:
                        details.append(
                            f"Dependency cycle detected involving operation: {neighbor!r}"
                        )
                    elif neighbor not in visited:
                        stack.append((neighbor, 0))
                else:
                    in_path.discard(node)
                    visited.add(node)
                    stack.pop()

        sorted_details = tuple(sorted(set(details)))
        return (len(sorted_details) > 0, sorted_details)

    # ── Execution plan ────────────────────────────────────────────────────────

    def build_execution_plan(self, swarm: SwarmDefinition) -> SwarmExecutionPlan:
        """Compute an immutable SwarmExecutionPlan using topological sort.

        Uses Kahn's algorithm. Tie-breaking within the same topological level
        is by (sequence_index, operation_id) for determinism.

        Only edges between members within this swarm are considered.
        References to non-member operations are ignored (detect_conflict
        handles those separately).

        If the graph contains a cycle, the execution_order is partial
        (cycled nodes are omitted). Callers should run plan() which checks
        for cycles before calling build_execution_plan().

        Args:
            swarm: A validated, conflict-free SwarmDefinition.

        Returns:
            SwarmExecutionPlan with deterministic execution_order.
        """
        member_ids = {m.operation_id for m in swarm.members}
        member_map = {m.operation_id: m for m in swarm.members}

        # Build in-degree and reverse adjacency
        in_degree: dict[str, int] = {op_id: 0 for op_id in member_ids}
        reverse_adj: dict[str, list[str]] = {op_id: [] for op_id in member_ids}

        for dep in swarm.dependencies:
            if dep.from_operation_id in member_ids and dep.to_operation_id in member_ids:
                in_degree[dep.to_operation_id] += 1
                reverse_adj[dep.from_operation_id].append(dep.to_operation_id)

        for adj_list in reverse_adj.values():
            adj_list.sort()

        def sort_key(op_id: str) -> tuple[int, str]:
            m = member_map.get(op_id)
            return (m.sequence_index if m else 0, op_id)

        ready = sorted(
            [op_id for op_id, deg in in_degree.items() if deg == 0],
            key=sort_key,
        )

        result: list[str] = []
        while ready:
            current = ready.pop(0)
            result.append(current)
            newly_ready: list[str] = []
            for dep_id in reverse_adj[current]:
                in_degree[dep_id] -= 1
                if in_degree[dep_id] == 0:
                    newly_ready.append(dep_id)
            newly_ready.sort(key=sort_key)
            ready.extend(newly_ready)
            ready.sort(key=sort_key)

        return SwarmExecutionPlan(
            swarm_id=swarm.id,
            strategy=swarm.strategy,
            execution_order=tuple(result),
            context_keys=swarm.context_keys,  # already sorted by build_swarm
            total_members=len(swarm.members),
        )

    # ── Planning ──────────────────────────────────────────────────────────────

    def plan(self, swarm: SwarmDefinition) -> SwarmResult:
        """Run full deterministic coordination planning for a SwarmDefinition.

        Steps (in order):
          1. Structural validation via validate()
             → If invalid: return SwarmResult with execution_plan=None
          2. Conflict detection via detect_conflict()
             → If conflict: return SwarmResult with execution_plan=None
          3. Build execution plan via build_execution_plan()
             → Returns populated SwarmResult with execution_plan

        Args:
            swarm: The SwarmDefinition to plan.

        Returns:
            SwarmResult capturing full coordination planning outcome.
        """
        # Step 1: structural validation
        validation = self.validate(swarm)
        if not validation.valid:
            return SwarmResult(
                swarm_id=swarm.id,
                execution_plan=None,
                conflict_detected=False,
                conflict_details=(),
                validation_result=validation,
            )

        # Step 2: conflict detection
        conflict_found, conflict_details = self.detect_conflict(swarm)
        if conflict_found:
            return SwarmResult(
                swarm_id=swarm.id,
                execution_plan=None,
                conflict_detected=True,
                conflict_details=conflict_details,
                validation_result=validation,
            )

        # Step 3: build execution plan
        execution_plan = self.build_execution_plan(swarm)

        return SwarmResult(
            swarm_id=swarm.id,
            execution_plan=execution_plan,
            conflict_detected=False,
            conflict_details=(),
            validation_result=validation,
        )
